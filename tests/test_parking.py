import json
import pytest
from test_recorder import module,NOW
from store import Store

PROFILE={'off':'0','running':'2'}

def sample(db,offset,lon,engine='2',odo=100.,profile=PROFILE,age=0):
    return db.ingest('car',NOW+offset*1000,NOW+(offset+age)*1000,30.,lon,
                     {'2016001':{'value':engine},'2103010':{'value':odo,'unit':'km'}},profile)

def drive(db,profile=PROFILE):
    for offset,lon,odo in ((0,120.,100.),(60,120.001,100.1),(120,120.002,100.2),(180,120.003,100.3)):
        sample(db,offset,lon,odo=odo,profile=profile)

def test_calibrated_engine_and_stable_position_confirm_stop_in_two_minutes(tmp_path):
    db=Store(tmp_path/'t.db');drive(db)
    for offset,lon in ((240,120.00305),(300,120.003051)):
        sample(db,offset,lon,engine='0',odo=100.3)
    assert not db.list_trips()[0]['closed']
    sample(db,360,120.003052,engine='0',odo=100.3)
    trip=db.list_trips()[0]
    assert trip['closed'] and trip['end_reason']=='parked_engine_off'
    assert trip['stop_ms']==NOW+240000 and trip['stop_confirmed_ms']==NOW+360000
    assert trip['end_ms']==NOW+180000
    db.close()

@pytest.mark.parametrize('profile',[None,{'off':'0','running':'1'}])
def test_unknown_or_unseen_running_state_does_not_claim_fast_parking(tmp_path,profile):
    db=Store(tmp_path/'t.db');drive(db,profile)
    for offset in (240,300,360):sample(db,offset,120.003,engine='0',odo=100.3,profile=profile)
    assert not db.list_trips()[0]['closed']
    db.close()

def test_repeated_or_stale_samples_cannot_confirm_stop(tmp_path):
    db=Store(tmp_path/'t.db');drive(db)
    sample(db,240,120.003,engine='0',odo=100.3)
    for age in (60,120,180,360):sample(db,240,120.003,engine='0',odo=100.3,age=age)
    assert not db.list_trips()[0]['closed']
    assert db.close_stale(NOW+1200000)==[1]
    assert db.list_trips()[0]['end_reason']=='data_gap'
    db.close()

def test_gap_and_odometer_change_reset_confirmation(tmp_path):
    db=Store(tmp_path/'t.db');drive(db)
    sample(db,240,120.003,engine='0',odo=100.3)
    sample(db,300,120.003,engine='0',odo=100.4)
    sample(db,360,120.003,engine='0',odo=100.4)
    assert not db.list_trips()[0]['closed']
    sample(db,600,120.003,engine='0',odo=100.4)
    assert not db.list_trips()[0]['closed']
    sample(db,660,120.003,engine='0',odo=100.4)
    sample(db,720,120.003,engine='0',odo=100.4)
    assert db.list_trips()[0]['end_reason']=='parked_engine_off'
    db.close()

def test_idling_with_fresh_data_is_not_gap_or_confirmed_trip_end(tmp_path):
    db=Store(tmp_path/'t.db');drive(db)
    for offset in range(240,1501,60):sample(db,offset,120.003,engine='2',odo=100.3)
    assert not db.close_stale(NOW+1500000)
    assert not db.list_trips()[0]['closed']
    db.close()

def test_upgrade_backfills_latest_sample_instead_of_last_movement(tmp_path):
    path=tmp_path/'t.db';db=Store(path);drive(db)
    for offset in range(240,1501,60):sample(db,offset,120.003,engine='2',odo=100.3)
    db.db.execute('ALTER TABLE trips DROP COLUMN last_sample_ms');db.db.commit();db.close()
    db=Store(path)
    assert not db.close_stale(NOW+1500000)
    assert db.db.execute('SELECT last_sample_ms FROM trips').fetchone()[0]==NOW+1500000
    db.close()

def test_replay_corrects_short_stationary_tail_without_altering_raw_points(tmp_path):
    db=Store(tmp_path/'t.db');drive(db,None)
    for offset in (240,300,360,400):sample(db,offset,120.003,engine='0',odo=100.3,profile=None)
    assert db.close_stale(NOW+1500000)==[1]
    before=[tuple(row) for row in db.db.execute('SELECT * FROM points ORDER BY id')]
    profile=db.parking_profile_from_trip(1,'car')
    assert profile['running']=='2' and profile['off']=='0'
    assert db.parking_profile_from_trip(1,'wrong-vehicle') is None
    assert db.reclassify_parking(1,profile)
    assert db.list_trips()[0]['end_reason']=='parked_engine_off'
    assert [tuple(row) for row in db.db.execute('SELECT * FROM points ORDER BY id')]==before
    assert not db.reclassify_parking(1,profile)
    db.close()

@pytest.mark.asyncio
async def test_owner_confirmation_corrects_and_queues_only_one_revision(tmp_path):
    from types import SimpleNamespace
    import hashlib
    r=module.Recorder(tmp_path)
    vehicle=hashlib.sha256(b'SYNTHETIC-CAR').hexdigest()[:20]
    drive(r.store,None)
    for offset in (240,300,360,400):sample(r.store,offset,120.003,engine='0',odo=100.3,profile=None)
    r.store.close_stale(NOW+1500000)
    r.store.db.execute('UPDATE trips SET vehicle=?',(vehicle,));r.store.db.execute('UPDATE points SET vehicle=?',(vehicle,));r.store.db.commit()
    r.selected=lambda:SimpleNamespace(identifier=SimpleNamespace(value='SYNTHETIC-CAR'))
    r.snapshot=lambda:{'ok':True};r.settings['notify']=True
    assert (await r.action('calibrate_parking_from_trip',{'trip_id':1})).get('error')
    for _ in range(2):assert await r.action('calibrate_parking_from_trip',{'trip_id':1,'confirmed_parked':True})=={'ok':True}
    assert r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
    assert r.settings['parking_profiles'][vehicle]['off']=='0'
    await r.close()
