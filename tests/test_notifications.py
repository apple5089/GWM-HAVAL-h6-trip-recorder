import json
import hashlib
from types import SimpleNamespace
import pytest
from test_recorder import module, NOW, ingest
from store import Store
from alerts import AlertMonitor


def signals(pressure=230, fuel=28, raw='0'):
    return {'2101001':{'value':str(pressure),'unit':'kPa'},
            '2017002':{'value':str(fuel),'unit':'L'},'2102001':{'value':raw,'unit':None}}


def trip(db, seconds=120):
    for i,offset in enumerate(range(0,seconds+1,60)):
        ingest(db,offset,lon=120+i*.001,signals={**signals(fuel=28-i), '2103010':{'value':1000+i*.1,'unit':'km'}})
    lastlon=120+seconds//60*.001
    for offset in range(seconds+60,seconds+421,60):
        ingest(db,offset,lon=lastlon,signals=signals(fuel=26))
    return db.list_trips()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('seconds,expected',[(60,'skipped_short'),(120,'queued')])
async def test_strict_minute_filter_summary_and_no_repeat(tmp_path,seconds,expected):
    r=module.Recorder(tmp_path); r.settings['notify']=True; r.vault['feishu_app']={'synthetic':True}
    try:
        t=trip(r.store,seconds)
        await r.notify_trip(t['id']); await r.notify_trip(t['id'])
        assert r.store.db.execute('SELECT status FROM notices').fetchone()[0]==expected
        rows=r.store.db.execute("SELECT * FROM outbox WHERE kind='trip'").fetchall()
        assert len(rows)==(1 if expected=='queued' else 0)
        assert r.store.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='trip_image'").fetchone()[0]==len(rows)
        if rows:
            text=rows[0]['text']
            assert '行驶距离：0.2 km' in text and '平均速度（估算）：' in text
            assert '轨迹折线里程' not in text and '表显里程差' not in text
            assert '油量表读数下降：2 L' in text and '无法可靠提供' in text
            assert '/?trip=1' in text and '北京时间' in text
            assert t['moving_observed_seconds']==120
            assert .18<t['route_distance_km']<.20
    finally: await r.close()


def test_gap_is_not_distance_or_moving_time_and_refuel_is_not_consumption(tmp_path):
    db=Store(tmp_path/'t.db')
    ingest(db,signals=signals(fuel=10))
    ingest(db,60,lon=120.001,signals=signals(fuel=11))
    ingest(db,300,lon=120.003,signals=signals(fuel=40))
    t=db.list_trips()[0]
    assert t['moving_observed_seconds']==60
    assert t['gap_count']==1
    assert t['route_distance_km']<.1
    assert t['fuel_gauge_drop_l']==-30
    db.close()


@pytest.mark.asyncio
async def test_durable_retry_success_and_enabled_kind_does_not_starve(tmp_path):
    r=module.Recorder(tmp_path); r.vault['feishu_app']={'synthetic':True}
    r.settings.update(notify=False,alerts=True)
    for i in range(4): r.store.enqueue('trip:'+str(i),'trip','synthetic trip',0)
    r.store.enqueue('alert:test','alert','synthetic alert',0)
    calls=[]
    async def fail(text,event_key=None): calls.append(event_key); raise ValueError('synthetic')
    r.send_feishu=fail
    await r.flush_notifications()
    row=r.store.db.execute("SELECT * FROM outbox WHERE kind='alert'").fetchone()
    assert row['status']=='pending' and row['attempts']==1
    assert calls==['alert:test']
    await r.close()
    r=module.Recorder(tmp_path); r.vault['feishu_app']={'synthetic':True}
    r.settings.update(notify=False,alerts=True)
    async def succeed(text,event_key=None): calls.append(event_key)
    r.send_feishu=succeed
    r.store.db.execute("UPDATE outbox SET next_ms=0 WHERE kind='alert'");r.store.db.commit()
    await r.flush_notifications();await r.flush_notifications()
    assert calls==['alert:test','alert:test']
    assert r.store.db.execute("SELECT status FROM outbox WHERE kind='alert'").fetchone()[0]=='sent'
    await r.close()


def test_freshness_debounce_hysteresis_restart_and_recovery(tmp_path):
    r=module.Recorder(tmp_path); monitor=r.alert_monitor; settings=r.settings
    observe=lambda offset,pressure,age=0:monitor.observe('car',NOW+offset*1000,NOW+(offset+age)*1000,signals(pressure),settings)
    observe(0,180,301) # old point does not establish a candidate
    observe(0,180);observe(0,180)
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
    observe(60,180);observe(120,180)
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
    observe(180,205);observe(240,205) # 10 kPa hysteresis
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
    r.store.close()
    db=Store(tmp_path/'tracks.sqlite3');monitor=AlertMonitor(db)
    observe(300,230);observe(360,230);observe(420,230)
    rows=db.db.execute('SELECT text FROM outbox ORDER BY created_ms').fetchall()
    assert len(rows)==2 and '恢复至自设范围' in rows[-1]['text']
    db.close()


def test_missing_wrong_unit_and_unknown_enums_do_not_claim_fault(tmp_path):
    db=Store(tmp_path/'t.db');monitor=AlertMonitor(db)
    r=module.Recorder(tmp_path/'r');settings=r.settings;r.store.close()
    monitor.observe('car',NOW,NOW,signals(raw='0'),settings)
    for offset in (60,120):
        s=signals(pressure=1,raw='2');s['2101001']['unit']='bar'
        monitor.observe('car',NOW+offset*1000,NOW+offset*1000,s,settings)
    rows=db.db.execute('SELECT text FROM outbox').fetchall()
    assert len(rows)==1
    assert '枚举未校准' in rows[0]['text'] and '超出自设范围' not in rows[0]['text']
    db.close()


def test_parked_apertures_require_owner_baseline_fresh_continuity_and_persist(tmp_path):
    r=module.Recorder(tmp_path)
    r.settings.update(aperture_baseline={'2206002':'0','2210001':'0','2210005':'3'},aperture_baseline_vehicle='car')
    def observe(offset,door='1',window='1',sunroof='4',age=0,lat=30.):
        s={**signals(),'2206002':{'value':door},'2210001':{'value':window},'2210005':{'value':sunroof}}
        r.alert_monitor.observe('car',NOW+offset*1000,NOW+(offset+age)*1000,s,r.settings,lat,120.)
    for offset in range(0,301,60):observe(offset)
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
    observe(360);observe(420)
    rows=r.store.db.execute('SELECT text FROM outbox').fetchall()
    assert len(rows)==1 and all(x in rows[0]['text'] for x in ('左前门','左前窗','天窗','疑似未关'))
    observe(480,door='0',window='0',sunroof='3',age=301) # stale does not clear warning
    observe(480,door='0',window='0',sunroof='3');observe(540,door='0',window='0',sunroof='3')
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==2
    # Unknown codes are never treated as open or closed.
    for offset in (600,660):observe(offset,door='99',window='99',sunroof='99')
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==2
    r.store.close()


def test_parking_after_gap_or_motion_requires_new_five_minute_sequence(tmp_path):
    r=module.Recorder(tmp_path)
    r.settings.update(aperture_baseline={'2206002':'0'},aperture_baseline_vehicle='car')
    s={**signals(),'2206002':{'value':'1'}}
    for offset,lat in ((0,30.),(60,30.),(120,30.001),(180,30.001),(240,30.001),(300,30.001),(360,30.001),(1200,30.001)):
        r.alert_monitor.observe('car',NOW+offset*1000,NOW+offset*1000,s,r.settings,lat,120.)
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
    r.store.close()


@pytest.mark.asyncio
async def test_owner_confirmed_baseline_is_scoped_to_vehicle_and_persisted(tmp_path):
    r=module.Recorder(tmp_path)
    r.selected=lambda:SimpleNamespace(identifier=SimpleNamespace(value='SYNTHETIC-CAR'))
    r.latest={'source_ms':NOW,'metrics':[{'code':'2210001','value':'0'},{'code':'2210005','value':'3'}]}
    r.latest_vehicle_key='other'
    result=await r.action('confirm_apertures_closed',{'confirmed_closed':True})
    assert result.get('error') and not r.settings['aperture_baseline']
    r.latest_vehicle_key=hashlib.sha256(b'SYNTHETIC-CAR').hexdigest()[:20]
    # This isolated test does not need a synthetic cloud vehicle list in snapshot.
    r.snapshot=lambda:{'ok':True}
    result=await r.action('confirm_apertures_closed',{'confirmed_closed':True})
    assert result=={'ok':True}
    assert r.settings['aperture_baseline']=={'2210001':'0','2210005':'3'}
    assert json.loads(r.settings_file.read_text())['aperture_baseline_vehicle']==r.latest_vehicle_key
    await r.close()


def test_cloud_failure_and_recovery_are_persistent_and_not_parked_staleness(tmp_path):
    db=Store(tmp_path/'t.db');monitor=AlertMonitor(db)
    for i in range(5):monitor.cloud(False,NOW+i*60000)
    assert db.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
    db.close();db=Store(tmp_path/'t.db');monitor=AlertMonitor(db)
    monitor.cloud(False,NOW+360000);monitor.cloud(True,NOW+420000);monitor.cloud(True,NOW+480000)
    assert db.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==2
    db.close()
