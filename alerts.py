"""Fresh-sample rules; unknown enum changes are never diagnosed as faults."""
import json
import math
from store import number, LABELS, distance

RAW = {**{str(2102001+i):name+'胎压相关状态' for i,name in enumerate(('左前','右前','左后','右后'))},
       **{str(2102007+i):name+'胎温相关状态' for i,name in enumerate(('左前','右前','左后','右后'))},
       '9000005':'燃油报警相关状态'}
APERTURES = {'2206002':'左前门','2206004':'右前门','2206003':'左后门','2206005':'右后门','2206001':'尾门',
             '2210001':'左前窗','2210002':'右前窗','2210004':'左后窗','2210003':'右后窗','2210005':'天窗'}


class AlertMonitor:
    def __init__(self, store):
        self.store = store

    def load(self, vehicle):
        row = self.store.db.execute('SELECT * FROM monitor WHERE vehicle=?',(vehicle,)).fetchone()
        return (row['source_ms'],json.loads(row['state'])) if row else (0,{})

    def save(self, vehicle, stamp, state):
        self.store.db.execute('INSERT OR REPLACE INTO monitor VALUES(?,?,?)',(vehicle,stamp,json.dumps(state)))
        self.store.db.commit()

    def observe(self, vehicle, stamp, now, signals, settings, lat=None, lon=None):
        if type(stamp) is not int or not -30000 <= now-stamp <= 300000:
            return
        previous, state = self.load(vehicle)
        if stamp <= previous:
            return
        if stamp-previous > max(180000,settings['interval']*2000+30000):
            for item in state.values():
                item.pop('candidate',None); item.pop('since',None)
            state.pop('_parking',None)
        events = []
        rules = {}
        # Parking is inferred from a continuous fresh position/odometer sequence,
        # never from the unvalidated engine or sunroof enum.
        parked = False
        valid_location = all(type(v) in (int,float) and math.isfinite(v) for v in (lat,lon)) and -90<=lat<=90 and -180<=lon<=180 and (lat,lon)!=(0,0)
        if valid_location:
            odo = number(signals,'2103010') if signals.get('2103010',{}).get('unit')=='km' else None
            park = state.get('_parking')
            if not park or distance((park['lat'],park['lon']),(lat,lon))>=50 or (odo is not None and park.get('odo') is not None and abs(odo-park['odo'])>.05):
                park = {'lat':lat,'lon':lon,'odo':odo,'start':stamp}
            state['_parking'] = park
            parked = stamp-park['start'] >= 300000
        else:
            state.pop('_parking',None)
        if settings['parked_apertures'] and settings.get('aperture_baseline_vehicle') == vehicle:
            for code,label in APERTURES.items():
                closed = settings.get('aperture_baseline',{}).get(code)
                if closed is None:
                    continue
                value = signals.get(code,{}).get('value')
                value = str(value) if type(value) in (str,int) else None
                if value is None or not value.isdigit() or int(value)>15 or code!='2210005' and value not in ('0','1'):
                    value = None
                target = 'normal' if value==closed else 'open' if value is not None and parked else None
                rules['aperture:'+code]=(target,label)
        for i in range(4):
            code = str(2101001+i); value = number(signals,code)
            value = value if signals.get(code,{}).get('unit')=='kPa' and value is not None and 0<value<=600 else None
            old = state.get(code,{}).get('value','normal')
            low,high = settings['tire_low_kpa'],settings['tire_high_kpa']
            target = None if value is None else ('low' if value<low or old=='low' and value<low+10 else 'high' if value>high or old=='high' and value>high-10 else 'normal')
            rules[code]=(target,f"{LABELS[code]}：{value:g} kPa（自设范围 {low}–{high} kPa）" if value is not None else '')
            code = str(2101005+i); value = number(signals,code)
            value = value if signals.get(code,{}).get('unit') in ('°C','℃') and value is not None and -40<=value<=125 else None
            old = state.get(code,{}).get('value','normal')
            target = None if value is None else ('high' if value>settings['tire_high_c'] or old=='high' and value>settings['tire_high_c']-5 else 'normal')
            rules[code]=(target,f"{LABELS[code]}：{value:g} °C（自设上限 {settings['tire_high_c']} °C）" if value is not None else '')
        fuel = number(signals,'2017002')
        if signals.get('2017002',{}).get('unit') != 'L' or fuel is None or not 0<=fuel<=200:
            fuel = None
        old = state.get('fuel',{}).get('value','normal')
        target = None if fuel is None else ('low' if fuel<settings['fuel_low_l'] or old=='low' and fuel<settings['fuel_low_l']+2 else 'normal')
        rules['fuel']=(target,f"剩余油量：{fuel:g} L（自设下限 {settings['fuel_low_l']} L）" if fuel is not None else '')
        for code,label in RAW.items():
            value = signals.get(code,{}).get('value')
            # Reject long/unexpected strings rather than echoing arbitrary API data.
            value = str(value) if type(value) in (int,str) and str(value).isdigit() and len(str(value))<=3 else None
            rules['raw:'+code]=(value,label)
        for key,(target,label) in rules.items():
            raw = key.startswith('raw:')
            item = state.setdefault(key,{'value':None if raw else 'normal'})
            if target is None:
                item.pop('candidate',None); item.pop('since',None)
                continue
            if raw and item['value'] is None:
                item['value'] = target
            if target == item['value']:
                item.pop('candidate',None); item.pop('since',None)
                continue
            if item.get('candidate') != target:
                item.update(candidate=target,since=stamp)
                continue
            if stamp-item['since'] < 30000:
                continue
            old = item['value']; item['value'] = target
            item.pop('candidate',None); item.pop('since',None)
            if raw:
                events.append(f"{label}：原始编码 {old} → {target}（枚举未校准，请核对官方 App/仪表）")
            elif key.startswith('aperture:'):
                events.append(label+('：云端状态已恢复到已确认关闭基准' if target=='normal' else '：停车后疑似未关（新采样偏离已确认关闭基准；连续 5 分钟位置未明显移动推定，请核对官方 App）'))
            else:
                events.append(('恢复至自设范围：' if target=='normal' else '超出自设范围：')+label)
        if events:
            key = f'alert:{vehicle}:{stamp}'
            text = 'H6 状态提醒\n'+'\n'.join(events)+'\n已由两次新采样确认；自设阈值不是原厂故障诊断。'
            # Outbox and monitor checkpoint commit together, including after restart.
            self.store.db.execute('INSERT OR IGNORE INTO outbox(event_key,kind,text,created_ms,next_ms) VALUES(?,?,?,?,?)',
                                  (key,'alert',text,now,now))
        self.save(vehicle,stamp,state)

    def cloud(self, success, now):
        _,state = self.load('cloud-service')
        state.setdefault('failures',0); state.setdefault('active',False)
        text = None
        if success:
            if state['active']:
                text = 'H6 采集恢复：车辆云端请求已恢复成功。定位新鲜度仍以页面采样时间为准。'
            state.update(failures=0,active=False)
        else:
            state['failures'] += 1
            if state['failures'] >= 3 and not state['active']:
                state['active'] = True
                text = 'H6 采集异常：车辆云端连续 3 次请求失败，轨迹可能断档；请检查网络和登录状态。这不代表车辆本身故障。'
        if text:
            self.store.db.execute('INSERT OR IGNORE INTO outbox(event_key,kind,text,created_ms,next_ms) VALUES(?,?,?,?,?)',
                                  (f'cloud:{now}', 'alert',text,now,now))
        self.save('cloud-service',now,state)
