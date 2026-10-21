import io,json
from PIL import Image
import pytest
from test_recorder import module,NOW
from test_trip_details import Session,CONFIG,SETTINGS
from trip_details import TripDetails
from trip_annotations import detect_stops,image_plan
from route_image import render_trip,SIZE,segment_hits_rect

def point(seconds,lon=120.,lat=30.,**extra):
    return {'source_ms':NOW+seconds*1000,'lat':lat,'lon':lon,'break_before':False,**extra}

def dwell_points():
    return [point(0,119.998)]+[point(t) for t in (60,120,180,240)]+[point(300,120.003),point(360,120.005)]

def long_points():
    return [point(i*60,120+i*.01) for i in range(81)]

def trip_data(points,km=60):
    return {'id':1,'closed':True,'end_reason':'parked_engine_off','start_ms':points[0]['source_ms'],
        'end_ms':points[-1]['source_ms'],'observed_span_seconds':(points[-1]['source_ms']-points[0]['source_ms'])/1000,
        'odometer_delta':km,'route_distance_km':km,'average_speed_kmh':45,'gap_count':0,'fuel_gauge_drop_l':2}

def seed_store(store,points):
    t=trip_data(points)
    store.db.execute("INSERT INTO trips(id,vehicle,start_ms,end_ms,closed,end_reason) VALUES(1,'synthetic',?,?,1,'parked_engine_off')",(t['start_ms'],t['end_ms']))
    store.db.executemany('INSERT INTO points(vehicle,source_ms,received_ms,lat,lon,signals,trip_id,break_before) VALUES(?,?,?,?,?,?,1,?)',
        [('synthetic',p['source_ms'],p['source_ms'],p['lat'],p['lon'],json.dumps({'2103010':{'value':1000+i,'unit':'km'}}),int(p.get('break_before',False))) for i,p in enumerate(points)])
    store.db.commit()

def test_dwell_requires_continuous_observation_then_resumed_movement():
    points=dwell_points();stops=detect_stops(points)
    assert len(stops)==1 and stops[0]['duration_seconds']==180 and stops[0]['start_ms']==NOW+60000
    assert detect_stops(points[:-2])==[]  # final parking is not an en-route stop
    assert len(detect_stops([point(0),point(180),point(360),point(540),point(600,120.003)]))==1
    broken=[dict(p) for p in points];broken[3]['break_before']=True
    assert detect_stops(broken)==[]
    gapped=[point(0),point(60),point(600),point(660),point(720,120.003)]
    assert detect_stops(gapped)==[]
    moving=[point(t,odometer_km=1000+i) for i,t in enumerate((0,60,120,180,240))]+[point(300,120.003)]
    assert detect_stops(moving)==[]

def test_long_plan_covers_chronology_without_bridging_gaps_and_short_trip_has_no_extras():
    points=long_points();plan=image_plan(trip_data(points),points)
    assert len(plan['pages'])>3 and len(plan['auto_pages'])==3
    assert plan['pages'][0]['points'][0]==points[0] and plan['pages'][-1]['points'][-1]==points[-1]
    assert plan['auto_pages'][0]==1 and plan['auto_pages'][-1]==len(plan['pages'])
    assert not image_plan(trip_data(points[:7],km=6),points[:7])['pages']
    split=[*points[:30],*[{**p,'source_ms':p['source_ms']+3600000,'break_before':i==0} for i,p in enumerate(points[30:])]]
    other=image_plan(trip_data(split),split)
    for page in other['pages']:
        assert all(b['source_ms']-a['source_ms']<=180000 for a,b in zip(page['points'],page['points'][1:]))

def test_stop_footer_grows_canvas_and_segment_title_does_not_claim_final_parking():
    points=dwell_points();t=trip_data(points,km=6);stops=detect_stops(points)
    stops[0]['label']='测试学校附近'
    png=render_trip(t,points,details={'stops':stops,'stop_count':1})
    with Image.open(io.BytesIO(png)) as image:assert image.height>SIZE[1]
    assert segment_hits_rect((0,0),(100,100),(45,40,55,60))
    assert not segment_hits_rect((0,0),(100,0),(45,40,55,60))

def test_many_pages_remain_bounded_and_keep_last_observed_point():
    points=[point(i*60,100+i*.075) for i in range(1001)]
    plan=image_plan(trip_data(points,km=2000),points)
    assert len(plan['pages'])<=500 and len(plan['auto_pages'])==3
    assert plan['pages'][-1]['points'][-1]==points[-1]

def test_stop_on_page_boundary_selects_the_page_including_its_end():
    points=[]
    for i in range(81):
        points.append(point((i+(5 if i>30 else 0))*60,120+i*.01))
        if i==30:points.extend(point((i+j)*60,120+i*.01) for j in range(1,6))
    plan=image_plan(trip_data(points),points);stop=plan['stops'][0]
    page=plan['pages'][plan['auto_pages'][1]-1]
    assert page['start_ms']<=stop['end_ms']<=page['end_ms']

@pytest.mark.asyncio
async def test_stop_names_use_only_tencent_and_cache_without_kimi(tmp_path):
    client=Session();details=TripDetails(tmp_path,client);stops=detect_stops(dwell_points())
    names=await details.name_stops(stops,CONFIG,SETTINGS)
    assert '私有楼栋' in names[0]['label'] and len(client.calls)==2
    await details.name_stops(stops,CONFIG,SETTINGS)
    assert len(client.calls)==2 and all(url.startswith('https://apis.map.qq.com/') for url,_ in client.calls)

@pytest.mark.asyncio
async def test_stop_geocoding_failure_keeps_number_and_duration(tmp_path):
    client=Session();client.fail=True;details=TripDetails(tmp_path,client);stops=detect_stops(dwell_points())
    result=await details.name_stops(stops,CONFIG,SETTINGS)
    assert result==stops and result[0]['duration_seconds']==180

@pytest.mark.asyncio
async def test_detail_queue_starts_after_overview_and_retries_each_upload_independently(tmp_path):
    r=module.Recorder(tmp_path);seed_store(r.store,long_points())
    r.settings.update(notify=True,notify_images=True,road_basemap=False);r.vault['feishu_app']={'synthetic':True}
    async def png(trip_id,require_basemap=False,segment=0):return b'PNG'+bytes([segment])
    r.trip_png=png
    class Sender:
        token=None
        def __init__(self):self.uploads=[];self.keys=[];self.failed=False
        async def upload_png(self,config,png):self.uploads.append(png);return 'img_'+str(len(self.uploads))
        async def send_image(self,config,key,event_key=None):
            self.keys.append(event_key)
            if ':part:' in event_key and not self.failed:self.failed=True;raise ValueError('synthetic send failure')
    sender=Sender();r.app_sender=sender;r.queue_trip_image(1,0)
    assert r.store.db.execute("SELECT count(*) FROM outbox WHERE kind='trip_image'").fetchone()[0]==1
    await r.flush_notifications()
    chosen=r.image_plan(1)[1]['auto_pages']
    assert r.store.db.execute("SELECT count(*) FROM outbox WHERE kind='trip_image'").fetchone()[0]==1+len(chosen)
    for _ in range(10):
        r.store.db.execute("UPDATE outbox SET next_ms=0 WHERE status='pending'");r.store.db.commit();await r.flush_notifications()
    assert len(sender.uploads)==1+len(chosen) and len(sender.keys)==2+len(chosen)
    assert r.store.db.execute("SELECT count(*) FROM outbox WHERE status='sent'").fetchone()[0]==1+len(chosen)
    r.queue_trip_image(1,0);await r.flush_notifications()
    assert len(sender.uploads)==1+len(chosen)
    await r.close()

@pytest.mark.asyncio
async def test_segment_manifest_and_png_api_validate_page_without_new_provider_calls(tmp_path):
    from aiohttp.test_utils import TestClient,TestServer
    r=module.Recorder(tmp_path);seed_store(r.store,long_points());r.settings['road_basemap']=False
    async with TestClient(TestServer(module.build_app(r)),headers={'Host':module.urlsplit(module.ORIGIN).netloc}) as client:
        response=await client.get('/api/trip-pages?trip=1');data=await response.json()
        assert response.status==200 and len(data['auto_pages'])==3 and len(data['pages'])>3
        assert all('lat' not in page and 'points' not in page for page in data['pages'])
        response=await client.get('/api/trip-image.png?trip=1&segment=1')
        assert response.status==200 and (await response.read()).startswith(b'\x89PNG')
        assert (await client.get('/api/trip-image.png?trip=1&segment=-1')).status==400
        assert (await client.get('/api/trip-image.png?trip=1&segment=500')).status==404
