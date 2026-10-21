"""Timestamp-aware local storage. Trips are explicitly motion-inferred."""
import json
import math
from pathlib import Path
import sqlite3
from collections import Counter

LABELS = {
    "2103010": "总里程", "2011007": "燃油续航", "2017002": "剩余油量",
    "9000019": "油量原始状态", "2016001": "发动机原始状态", "2201001": "车内温度原始值",
    "2101001": "左前胎压", "2101002": "右前胎压", "2101003": "左后胎压", "2101004": "右后胎压",
    "2101005": "左前胎温", "2101006": "右前胎温", "2101007": "左后胎温", "2101008": "右后胎温",
    "2208001": "门锁原始状态", "2206001": "尾门原始状态", "2206002": "左前门原始状态",
    "2206004": "右前门原始状态", "2206003": "左后门原始状态", "2206005": "右后门原始状态",
    "2210001": "左前窗原始状态", "2210002": "右前窗原始状态", "2210003": "左后窗原始状态",
    "2210004": "右后窗原始状态", "2210005": "天窗原始状态", "2202001": "空调原始状态",
    "9000005": "燃油报警原始状态", "9000017": "TBox 原始状态", "9000018": "无线信号原始状态",
}


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def number(signals, code):
    try:
        raw = signals.get(code, {}).get("value")
        if isinstance(raw, bool):
            return None
        value = float(raw)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS points(
          id INTEGER PRIMARY KEY, vehicle TEXT NOT NULL, source_ms INTEGER NOT NULL,
          received_ms INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
          signals TEXT NOT NULL, trip_id INTEGER, break_before INTEGER NOT NULL DEFAULT 0,
          UNIQUE(vehicle,source_ms));
        CREATE TABLE IF NOT EXISTS trips(
          id INTEGER PRIMARY KEY,vehicle TEXT NOT NULL,start_ms INTEGER NOT NULL,
          end_ms INTEGER NOT NULL,closed INTEGER NOT NULL DEFAULT 0,stationary_ms INTEGER,
          basis TEXT NOT NULL DEFAULT 'motion_inferred',end_reason TEXT);
        CREATE TABLE IF NOT EXISTS notices(trip_id INTEGER PRIMARY KEY,attempted_ms INTEGER,status TEXT);
        CREATE TABLE IF NOT EXISTS outbox(event_key TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL,
          created_ms INTEGER NOT NULL,next_ms INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS monitor(vehicle TEXT PRIMARY KEY,source_ms INTEGER NOT NULL,state TEXT NOT NULL);
        """)
        if "end_reason" not in {row[1] for row in self.db.execute("PRAGMA table_info(trips)")}:
            self.db.execute("ALTER TABLE trips ADD COLUMN end_reason TEXT")
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(trips)')}
        for name,kind in {'last_sample_ms':'INTEGER','stop_ms':'INTEGER','stop_confirmed_ms':'INTEGER',
                          'parking_evidence':'TEXT','engine_running_seen':'INTEGER NOT NULL DEFAULT 0'}.items():
            if name not in columns:
                self.db.execute(f'ALTER TABLE trips ADD COLUMN {name} {kind}')
        if 'last_sample_ms' not in columns:
            self.db.execute('UPDATE trips SET last_sample_ms=(SELECT MAX(source_ms) FROM points WHERE trip_id=trips.id)')
        if 'image_key' not in {row[1] for row in self.db.execute('PRAGMA table_info(outbox)')}:
            self.db.execute('ALTER TABLE outbox ADD COLUMN image_key TEXT')
        self.db.commit()
        for file in (self.path, Path(str(self.path)+"-wal"), Path(str(self.path)+"-shm")):
            if file.exists():
                file.chmod(0o600)

    def ingest(self, vehicle, source_ms, received_ms, lat, lon, signals, parking_profile=None):
        if type(source_ms) is not int or source_ms <= 0:
            return {"accepted": False, "reason": "no_source_time"}
        age = (received_ms-source_ms)/1000
        if age < -30 or age > 300:
            return {"accepted": False, "reason": "future_time" if age < -30 else "stale"}
        if not all(type(x) in (float, int) and math.isfinite(x) for x in (lat, lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0, 0):
            return {"accepted": False, "reason": "invalid_location"}
        previous = self.db.execute("SELECT * FROM points WHERE vehicle=? ORDER BY source_ms DESC LIMIT 1", (vehicle,)).fetchone()
        if previous and source_ms <= previous["source_ms"]:
            return {"accepted": False, "reason": "duplicate_or_out_of_order"}
        trip = self.db.execute("SELECT * FROM trips WHERE vehicle=? AND closed=0 ORDER BY id DESC LIMIT 1", (vehicle,)).fetchone()
        gap = (source_ms-previous["source_ms"])/1000 if previous else None
        if previous and 0 < gap <= 300 and distance((previous["lat"],previous["lon"]),(lat,lon))/gap > 300/3.6:
            return {"accepted":False,"reason":"implausible_location_jump"}
        moved = previous is not None and distance((previous["lat"], previous["lon"]), (lat, lon)) >= 50
        completed = []
        if trip and gap is not None and gap > 900:
            self.db.execute("UPDATE trips SET closed=1,end_reason='data_gap' WHERE id=?", (trip["id"],))
            completed.append(trip["id"])
            trip = None
        trip_id = trip["id"] if trip else None
        # Never infer a connecting journey across an unobserved long gap.
        if not trip and moved and gap <= 300:
            start = previous["source_ms"]
            cur = self.db.execute("INSERT INTO trips(vehicle,start_ms,end_ms) VALUES(?,?,?)", (vehicle, start, source_ms))
            trip_id = cur.lastrowid
            self.db.execute("UPDATE points SET trip_id=? WHERE id=?", (trip_id, previous["id"]))
            running = self._engine_running(signals,parking_profile) or self._engine_running(json.loads(previous['signals']),parking_profile)
            self.db.execute('UPDATE trips SET last_sample_ms=?,engine_running_seen=? WHERE id=?',(source_ms,int(running),trip_id))
        elif trip:
            if moved:
                self.db.execute("UPDATE trips SET end_ms=?,stationary_ms=NULL,parking_evidence=NULL WHERE id=?", (source_ms, trip_id))
            else:
                if self._observe_stop(trip,source_ms,lat,lon,signals,gap,parking_profile):
                    completed.append(trip_id)
            self.db.execute('UPDATE trips SET last_sample_ms=?,engine_running_seen=? WHERE id=?',
                            (source_ms,int(bool(trip['engine_running_seen']) or self._engine_running(signals,parking_profile)),trip['id']))
        self.db.execute("INSERT INTO points(vehicle,source_ms,received_ms,lat,lon,signals,trip_id,break_before) VALUES(?,?,?,?,?,?,?,?)",
                        (vehicle,source_ms,received_ms,lat,lon,json.dumps(signals,ensure_ascii=False),trip_id,int(gap is not None and gap > 180)))
        self.db.commit()
        return {"accepted": True, "reason": "fresh", "trip_id": trip_id, "completed": completed}

    @staticmethod
    def _engine_running(signals, profile):
        return bool(profile and str(signals.get('2016001',{}).get('value')) == profile.get('running'))

    def _observe_stop(self, trip, stamp, lat, lon, signals, gap, profile):
        evidence = json.loads(trip['parking_evidence']) if trip['parking_evidence'] else None
        odo = number(signals,'2103010') if signals.get('2103010',{}).get('unit')=='km' else None
        # Each confirmation needs a new source timestamp and continuous observations.
        if (not evidence or gap is None or gap>180 or
            distance((evidence['lat'],evidence['lon']),(lat,lon))>35 or
            odo is not None and evidence.get('odo') is not None and abs(odo-evidence['odo'])>.05):
            evidence = {'start':stamp,'lat':lat,'lon':lon,'odo':odo,'count':0,'off_start':None,'off_count':0}
        evidence['count'] += 1
        off = bool(profile and profile.get('off') != profile.get('running') and
                   str(signals.get('2016001',{}).get('value')) == profile.get('off') and trip['engine_running_seen'])
        if off:
            if evidence['off_start'] is None:
                evidence['off_start'] = stamp
            evidence['off_count'] += 1
        else:
            evidence['off_start'] = None; evidence['off_count'] = 0
        fast = off and evidence['off_count']>=3 and stamp-evidence['off_start']>=120000
        stationary = evidence['count']>=3 and stamp-evidence['start']>=300000 and not self._engine_running(signals,profile)
        reason = 'parked_engine_off' if fast else 'stationary_samples' if stationary else None
        self.db.execute('UPDATE trips SET stationary_ms=?,parking_evidence=? WHERE id=?',
                        (evidence['start'],json.dumps(evidence),trip['id']))
        if reason:
            self.db.execute('UPDATE trips SET closed=1,end_reason=?,stop_ms=?,stop_confirmed_ms=? WHERE id=?',
                            (reason,evidence['off_start'] if fast else evidence['start'],stamp,trip['id']))
        return reason

    def reclassify_parking(self, trip_id, profile):
        """Replay private samples; retain original points and already sent notices."""
        original = self.db.execute('SELECT * FROM trips WHERE id=?',(trip_id,)).fetchone()
        if not original or original['end_reason']!='data_gap':
            return False
        samples = self.db.execute('SELECT * FROM points WHERE trip_id=? ORDER BY source_ms',(trip_id,)).fetchall()
        replay = Store(':memory:')
        try:
            for point in samples:
                replay.ingest(point['vehicle'],point['source_ms'],point['received_ms'],point['lat'],point['lon'],json.loads(point['signals']),profile)
            trips = replay.db.execute('SELECT * FROM trips ORDER BY id').fetchall()
            if len(trips)!=1 or trips[0]['end_reason']!='parked_engine_off':
                return False
            fixed = trips[0]
            self.db.execute('UPDATE trips SET end_reason=?,stop_ms=?,stop_confirmed_ms=?,last_sample_ms=?,engine_running_seen=1,parking_evidence=? WHERE id=?',
                            (fixed['end_reason'],fixed['stop_ms'],fixed['stop_confirmed_ms'],samples[-1]['source_ms'],fixed['parking_evidence'],trip_id))
            self.db.commit()
            return True
        finally:
            replay.close()

    def parking_profile_from_trip(self, trip_id, vehicle):
        trip = self.db.execute('SELECT * FROM trips WHERE id=? AND vehicle=?',(trip_id,vehicle)).fetchone()
        if not trip or not trip['closed']:
            return None
        rows = self.db.execute('SELECT * FROM points WHERE trip_id=? ORDER BY source_ms',(trip_id,)).fetchall()
        if len(rows)<5:
            return None
        def engine(p):
            raw = json.loads(p['signals']).get('2016001',{}).get('value')
            return str(raw) if type(raw) in (str,int) and str(raw).isdigit() and len(str(raw))<=2 else None
        votes = Counter(engine(b) for a,b in zip(rows,rows[1:]) if
                        0<b['source_ms']-a['source_ms']<=180000 and distance((a['lat'],a['lon']),(b['lat'],b['lon']))>=50)
        votes.pop(None,None)
        if not votes or votes.most_common(1)[0][1]<3:
            return None
        running = votes.most_common(1)[0][0]
        off = engine(rows[-1])
        if off is None or off==running:
            return None
        tail=[]
        for p in reversed(rows):
            if engine(p)!=off or distance((p['lat'],p['lon']),(rows[-1]['lat'],rows[-1]['lon']))>35:
                break
            if tail and tail[-1]['source_ms']-p['source_ms']>180000:
                break
            tail.append(p)
        if len(tail)<3 or tail[0]['source_ms']-tail[-1]['source_ms']<120000:
            return None
        odos = [number(json.loads(p['signals']),'2103010') if json.loads(p['signals']).get('2103010',{}).get('unit')=='km' else None for p in tail]
        if any(v is None for v in odos) or max(odos)-min(odos)>.05:
            return None
        return {'running':running,'off':off,'basis':'owner-confirmed parking plus moving and stationary samples','trip_id':trip_id}

    def close_stale(self, now_ms):
        rows = self.db.execute("SELECT id FROM trips WHERE closed=0 AND COALESCE(last_sample_ms,end_ms)<?", (now_ms-900000,)).fetchall()
        for row in rows:
            self.db.execute("UPDATE trips SET closed=1,end_reason='data_gap' WHERE id=?",(row["id"],))
        self.db.commit()
        return [row["id"] for row in rows]

    def list_trips(self, trip_id=None):
        rows = self.db.execute("SELECT * FROM trips ORDER BY id DESC LIMIT 100" if trip_id is None else "SELECT * FROM trips WHERE id=?",
                               () if trip_id is None else (trip_id,)).fetchall()
        result = []
        for row in rows:
            points = self.db.execute("SELECT source_ms,signals,break_before,lat,lon FROM points WHERE trip_id=? ORDER BY source_ms", (row["id"],)).fetchall()
            observed = [p for p in points if p['source_ms'] <= row['end_ms']]
            route_m = moving_seconds = 0
            for a,b in zip(observed,observed[1:]):
                gap = (b['source_ms']-a['source_ms'])/1000
                if b['break_before'] or not 0 < gap <= 180:
                    continue
                meters = distance((a['lat'],a['lon']),(b['lat'],b['lon']))
                route_m += meters
                if meters >= 50:
                    moving_seconds += gap
            odo = [number(json.loads(p["signals"]), "2103010") if json.loads(p['signals']).get('2103010',{}).get('unit')=='km' else None for p in observed]
            delta = None
            if len(odo) > 1 and odo[0] is not None and odo[-1] is not None and 0 <= odo[-1]-odo[0] < 3000:
                delta = round(odo[-1]-odo[0], 3)
            fuel = [number(json.loads(p['signals']), '2017002') if json.loads(p['signals']).get('2017002',{}).get('unit')=='L' else None for p in observed]
            fuel_change = round(fuel[0]-fuel[-1],3) if len(fuel)>1 and all(v is not None and 0<=v<=200 for v in (fuel[0],fuel[-1])) else None
            duration=max(0,(row['end_ms']-row['start_ms'])/1000)
            average_speed=round(delta*3600/duration,1) if delta is not None and duration>0 else None
            result.append({"id":row["id"], "start_ms":row["start_ms"], "end_ms":row["end_ms"],
                           "closed":bool(row["closed"]), "basis":row["basis"], "end_reason":row["end_reason"], "point_count":len(points),
                           "stop_ms":row['stop_ms'],"stop_confirmed_ms":row['stop_confirmed_ms'],
                           "observed_span_seconds":duration,"average_speed_kmh":average_speed,
                           "odometer_delta":delta, "route_distance_km":round(route_m/1000,3),
                           "moving_observed_seconds":moving_seconds,"fuel_gauge_drop_l":fuel_change,
                           "gap_count":sum(p["break_before"] for p in points)})
        return result

    def enqueue(self, key, kind, text, now_ms):
        self.db.execute('INSERT OR IGNORE INTO outbox(event_key,kind,text,created_ms,next_ms) VALUES(?,?,?,?,?)',
                        (key,kind,text,now_ms,now_ms))
        self.db.commit()

    def fuel_summary(self, trip_id):
        """Fuel at this trip's end and a recent gauge estimate, never future readings.

        Use up to 500 km / 30 days / 10000 samples, stop at refuelling or invalid
        readings, and wait for at least 10 km of odometer movement. This is a
        gauge-based average over the displayed window, not measured trip fuel.
        """
        result={'fuel_remaining_l':None,'fuel_remaining_at_ms':None,
                'estimated_fuel_l_per_100km':None,'fuel_estimate_distance_km':None,
                'fuel_estimate_drop_l':None,'fuel_estimate_status':'missing'}
        trip=self.db.execute('SELECT vehicle,end_ms FROM trips WHERE id=?',(trip_id,)).fetchone()
        if not trip:return result
        rows=self.db.execute('SELECT source_ms,signals FROM points WHERE vehicle=? AND source_ms<=? AND source_ms>=? ORDER BY source_ms DESC LIMIT 10000',
                             (trip['vehicle'],trip['end_ms'],trip['end_ms']-30*86400000)).fetchall()
        if not rows:return result
        def sample(row):
            signals=json.loads(row['signals'])
            fuel=number(signals,'2017002') if signals.get('2017002',{}).get('unit')=='L' else None
            odo=number(signals,'2103010') if signals.get('2103010',{}).get('unit')=='km' else None
            return (fuel if fuel is not None and 0<=fuel<=200 else None,
                    odo if odo is not None and odo>=0 else None,row['source_ms'])
        end_fuel,end_odo,end_ms=sample(rows[0])
        result.update(fuel_remaining_l=end_fuel,fuel_remaining_at_ms=end_ms if end_fuel is not None else None)
        if end_fuel is None or end_odo is None:return result
        previous_fuel,previous_odo,previous_ms=end_fuel,end_odo,end_ms
        start_fuel,start_odo=end_fuel,end_odo
        for row in rows[1:]:
            fuel,odo,stamp=sample(row)
            if fuel is None or odo is None or previous_ms-stamp>7*86400000:break
            # Looking backwards: a lower fuel value means a later increase/refill.
            if fuel<previous_fuel-.001 or odo>previous_odo+.001 or end_odo-odo>500:break
            start_fuel,start_odo=fuel,odo
            previous_fuel,previous_odo,previous_ms=fuel,odo,stamp
        distance=round(end_odo-start_odo,3);drop=round(start_fuel-end_fuel,3)
        result.update(fuel_estimate_distance_km=distance,fuel_estimate_drop_l=drop,fuel_estimate_status='collecting')
        if distance>=10 and drop>0:
            rate=round(drop*100/distance,1)
            if rate>0:result.update(estimated_fuel_l_per_100km=rate,fuel_estimate_status='estimated')
        return result

    def points(self, trip_id=None):
        if trip_id is None:
            rows = self.db.execute("SELECT * FROM (SELECT * FROM points ORDER BY source_ms DESC LIMIT 2000) ORDER BY source_ms").fetchall()
        else:
            rows = self.db.execute("SELECT * FROM points WHERE trip_id=? ORDER BY source_ms LIMIT 10000", (trip_id,)).fetchall()
        return [{"source_ms":p["source_ms"], "lat":p["lat"], "lon":p["lon"], "break_before":bool(p["break_before"])} for p in rows]

    def observations(self,trip_id):
        rows=self.db.execute('SELECT source_ms,lat,lon,break_before,signals FROM points WHERE trip_id=? ORDER BY source_ms LIMIT 10000',(trip_id,)).fetchall()
        result=[]
        for p in rows:
            signals=json.loads(p['signals'])
            odo=number(signals,'2103010') if signals.get('2103010',{}).get('unit')=='km' else None
            result.append({'source_ms':p['source_ms'],'lat':p['lat'],'lon':p['lon'],'break_before':bool(p['break_before']),'odometer_km':odo})
        return result

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM points").fetchone()[0]

    def close(self):
        self.db.close()
