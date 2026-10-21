import json
import pytest
from test_recorder import NOW,ingest
from store import Store
from route_image import remaining_fuel_stat,estimated_fuel_stat

def make_trip(tmp_path,fuels,odos,unit='L'):
    db=Store(tmp_path/'fuel.sqlite3')
    for i,(fuel,odo) in enumerate(zip(fuels,odos)):
        signals={'2017002':{'value':fuel,'unit':unit},'2103010':{'value':odo,'unit':'km'}}
        ingest(db,i*60,lon=120+i*.002,signals=signals)
    return db,db.list_trips()[0]['id']

def test_recent_gauge_average_survives_unchanged_fuel_on_final_short_leg(tmp_path):
    db,tid=make_trip(tmp_path,[30,29,28,28],[1000,1006,1012,1018])
    summary=db.fuel_summary(tid)
    assert summary['fuel_remaining_l']==28 and summary['fuel_estimate_drop_l']==2
    assert summary['fuel_estimate_distance_km']==18 and summary['estimated_fuel_l_per_100km']==11.1
    assert remaining_fuel_stat(summary)==('剩余油量 · 结束时','28','L')
    assert estimated_fuel_stat(summary)==('预估油耗 · 近18km油表估算','≈11.1','L/100 km')
    db.close()

def test_historical_image_does_not_use_future_or_other_vehicle_fuel(tmp_path):
    db,tid=make_trip(tmp_path,[30,28],[1000,1018]);before=db.fuel_summary(tid)
    signal=json.dumps({'2017002':{'value':10,'unit':'L'},'2103010':{'value':1100,'unit':'km'}})
    db.db.executemany('INSERT INTO points(vehicle,source_ms,received_ms,lat,lon,signals) VALUES(?,?,?,?,?,?)',
        [('vehicle',NOW+120000,NOW+120000,30,120,signal),('another-car',NOW+60000,NOW+60000,30,120,signal)])
    db.db.commit()
    assert db.fuel_summary(tid)==before
    db.close()

@pytest.mark.parametrize('fuel,odo,expected_distance,expected_rate',[
    ([30,29,40,39],[1000,1012,1024,1036],12,8.3),
    ([30,29,28,27],[1000,1012,2,14],12,8.3),
    ([30,30],[1000,1018],18,None),
    ([30,29],[1000,1004],4,None),
    ([30,29],[1000,1501],0,None),
    ([30,29,28],[1000,None,1018],0,None),
])
def test_refills_odometer_resets_short_windows_and_gauge_resolution(tmp_path,fuel,odo,expected_distance,expected_rate):
    db,tid=make_trip(tmp_path,fuel,odo);summary=db.fuel_summary(tid)
    assert summary['fuel_estimate_distance_km']==expected_distance
    assert summary['estimated_fuel_l_per_100km']==expected_rate
    if expected_rate is None:assert estimated_fuel_stat(summary)[1]=='数据积累中'
    db.close()

def test_missing_units_do_not_turn_percent_into_litres_and_zero_litres_is_valid(tmp_path):
    db,tid=make_trip(tmp_path,[20,10],[1000,1018],unit='%')
    assert db.fuel_summary(tid)['fuel_remaining_l'] is None
    assert db.fuel_summary(tid)['estimated_fuel_l_per_100km'] is None
    db.close()
    assert remaining_fuel_stat({'fuel_remaining_l':0})==('剩余油量 · 结束时','0','L')
    assert remaining_fuel_stat({'fuel_remaining_l':float('nan')})[1]=='暂无数据'
    assert estimated_fuel_stat({'estimated_fuel_l_per_100km':0,'fuel_estimate_distance_km':18})[1]=='暂无数据'
