import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from store import Store
spec = importlib.util.spec_from_file_location("nas_app",HERE/"app.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from aiohttp.test_utils import TestClient,TestServer

NOW = 1800000000000


def ingest(store, offset=0, lat=30., lon=120., age=0, signals=None):
    stamp = NOW + offset*1000
    return store.ingest("vehicle",stamp,stamp+age*1000,lat,lon,signals or {})


def test_stale_duplicate_invalid_and_out_of_order_cannot_create_points(tmp_path):
    db = Store(tmp_path/"test.db")
    assert not ingest(db,age=301)["accepted"]
    assert not ingest(db,age=-31)["accepted"]
    assert not ingest(db,lat=float('nan'))["accepted"]
    assert ingest(db)["accepted"]
    assert not ingest(db)["accepted"]
    assert not ingest(db,offset=-1)["accepted"]
    assert db.count()==1
    db.close()


def test_stationary_fresh_points_do_not_invent_a_trip(tmp_path):
    db=Store(tmp_path/"test.db")
    for n in range(20):
        ingest(db,n*60)
    assert db.count()==20
    assert not db.list_trips()
    db.close()


def test_motion_trip_closes_after_stationary_samples_and_preserves_meter_readings(tmp_path):
    db=Store(tmp_path/"test.db")
    ingest(db,signals={"2103010":{"value":"1000","unit":"km"}})
    ingest(db,60,lon=120.001,signals={"2103010":{"value":"1000.1","unit":"km"}})
    result=None
    for offset in range(120,421,60):
        result=ingest(db,offset,lon=120.001,signals={"2103010":{"value":"1000.1","unit":"km"}})
    trip=db.list_trips()[0]
    assert trip["closed"]
    assert trip["basis"]=="motion_inferred"
    assert trip["observed_span_seconds"]==60
    assert trip["odometer_delta"]==0.1
    assert trip["gap_count"]==0
    db.close()


def test_large_gap_is_not_connected_as_a_journey(tmp_path):
    db=Store(tmp_path/"test.db")
    ingest(db)
    ingest(db,3600,lon=121)
    assert not db.list_trips()
    assert db.points()[-1]["break_before"]
    db.close()


def test_lost_telemetry_closes_observation_without_claiming_a_stop(tmp_path):
    db=Store(tmp_path/"test.db")
    ingest(db)
    ingest(db,60,lon=120.001)
    assert db.close_stale(NOW+1200000)==[1]
    trip=db.list_trips()[0]
    assert trip["end_reason"]=="data_gap"
    assert trip["end_ms"]==NOW+60000
    db.close()


def test_impossible_fresh_location_jump_is_rejected(tmp_path):
    db=Store(tmp_path/"test.db")
    ingest(db)
    assert ingest(db,60,lon=130)["reason"]=="implausible_location_jump"
    assert db.count()==1
    db.close()


@pytest.mark.asyncio
async def test_access_origin_and_vault_secrecy(tmp_path):
    recorder=module.Recorder(tmp_path)
    async with TestClient(TestServer(module.build_app(recorder)),headers={"Host":module.urlsplit(module.ORIGIN).netloc}) as client:
        assert (await client.get('/api/state')).status==200
        assert (await client.get('/api/points')).status==200
        assert (await client.get('/api/export.csv')).status==200
        assert (await client.get('/api/state',headers={"Host":"untrusted.example"})).status==403
        assert (await client.post('/api/settings',json={"interval":60})).status==403
        result=await client.post('/api/settings',json={"interval":60},headers={"Origin":module.ORIGIN})
        assert result.status==200
        asset=await client.get('/static/leaflet/leaflet.js')
        assert asset.status==200
        assert "max-age" in asset.headers['Cache-Control']
        assert "https://tile.openstreetmap.org" in asset.headers['Content-Security-Policy']
        assert (await client.get('/static/../app.py')).status!=200
        result=await client.post('/api/settings',json={"interval":60},headers={"Origin":"https://untrusted.example"})
        assert result.status==403
        private="PRIVATE-SENTINEL-MUST-NOT-LEAK"
        recorder.vault={"phone":private,"webhook":private}
        recorder.save_vault()
        assert private.encode() not in recorder.vault_file.read_bytes()
        assert private not in json.dumps(recorder.snapshot())
        assert private not in json.dumps(recorder.public())
        recorder.vault={"feishu_app":{"app_id":private,"app_secret":private,"receive_id":private}}
        assert recorder.snapshot()["notification_mode"]=="existing_app"
        assert private not in json.dumps(recorder.snapshot())
        assert private not in json.dumps(recorder.public())


@pytest.mark.asyncio
@pytest.mark.parametrize("hook",["http://open.feishu.cn/open-apis/bot/v2/hook/test","https://evil.test/test","https://open.feishu.cn.evil.test/open-apis/bot/v2/hook/test","https://u:p@open.feishu.cn/open-apis/bot/v2/hook/test"])
async def test_webhook_cannot_target_other_origins(tmp_path,hook):
    recorder=module.Recorder(tmp_path)
    try:
        result=await recorder.action("settings",{"webhook":hook})
        assert result.get("error")
        assert not recorder.vault.get("webhook")
    finally:
        await recorder.close()
