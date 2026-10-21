import asyncio
import json
from types import SimpleNamespace
import pytest
from test_recorder import module,NOW,ingest
from trip_details import TripDetails,place,signed_params,fallback_title,destination_label

SETTINGS={'tencent_enabled':True,'kimi_enabled':True,'tencent_coord_mode':'gps','kimi_model':'kimi-k2.6'}
CONFIG={'tencent_key':'a'*32,'tencent_sk':'c'*32,'kimi_key':'sk-'+'b'*32}
POINTS=[{'lat':30.,'lon':120.},{'lat':30.001,'lon':120.001}]
TRIP={'id':1,'end_reason':'parked_engine_off','odometer_delta':4,'observed_span_seconds':480}

class Response:
    status=200
    def __init__(self,data):self.data=data;self.content=self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def iter_chunked(self,size):yield json.dumps(self.data).encode()

class Session:
    def __init__(self):self.calls=[];self.fail=False
    def __call__(self,**kwargs):return self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if self.fail:raise ValueError('secret URL must never escape')
        if 'translate' in url:
            lat,lon=map(float,kwargs['params']['locations'].split(','));return Response({'status':0,'locations':[{'lat':lat,'lng':lon}]})
        return Response({'status':0,'result':{'address_component':{'city':'示例市','district':'示例区','street':'隐私街道'},'address_reference':{'business_area':{'title':'中心商圈'}},'pois':[{'title':'私有楼栋','_distance':10}]}})
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs));return Response({'choices':[{'message':{'content':'云影巡航·城市穿行'}}]})

@pytest.mark.asyncio
@pytest.mark.parametrize('bad_title',['云幕归巢补给完成知识据点住宅区域阴天','阴天的一段路\n说明这次抵达了住宅区域','超速追逐·冠军归家','少云，从学校开到小区附近'])
async def test_invented_claims_are_replaced_by_game_title_and_cached(tmp_path,bad_title):
    class Jargon(Session):
        def post(self,url,**kwargs):
            self.calls.append((url,kwargs));return Response({'choices':[{'message':{'content':bad_title}}]})
    session=Jargon();details=TripDetails(tmp_path,session)
    result=await details.get(TRIP,POINTS,CONFIG,SETTINGS)
    assert result['title']=='自由巡航·旅程存档' and result['source']=='tencent'
    assert (await TripDetails(tmp_path,session).get(TRIP,POINTS,CONFIG,SETTINGS))==result
    assert len(session.calls)==5

def test_residential_area_is_not_assumed_to_be_home_and_labels_are_readable():
    title=fallback_title('住宅区域',{'available':True,'condition':'阴','is_day':True})
    assert title=='阴云漫游·街区抵达'
    assert destination_label('住宅区域')=='小区附近'
    assert '家' not in title and all(word not in title for word in ('据点','补给','归巢','住宅区域'))
    assert fallback_title('学校',{'available':False})=='自由巡航·学园抵达'
    assert fallback_title('普通地点',{'available':False})=='自由巡航·旅程存档'

@pytest.mark.asyncio
async def test_kimi_receives_only_coarse_places_and_metrics_and_caches(tmp_path):
    session=Session();details=TripDetails(tmp_path,session)
    first=await details.get(TRIP,POINTS,CONFIG,SETTINGS)
    assert first['source']=='kimi' and '私有楼栋' in first['start']
    assert len(session.calls)==5
    url,kwargs=session.calls[-1]
    assert url=='https://api.moonshot.cn/v1/chat/completions' and kwargs['allow_redirects'] is False
    data=json.loads(kwargs['json']['messages'][-1]['content'])
    assert set(data)=={'start_area','end_area','start_category','end_category','distance_km','duration_minutes'}
    encoded=json.dumps(kwargs['json'],ensure_ascii=False)
    assert all(v not in encoded for v in ('隐私街道','私有楼栋','120.001','tencent_key','source_ms'))
    assert kwargs['json']['thinking']=={'type':'disabled'}
    second=await TripDetails(tmp_path,session).get(TRIP,POINTS,CONFIG,SETTINGS)
    assert first==second and len(session.calls)==5

@pytest.mark.asyncio
async def test_provider_failure_falls_back_and_never_exposes_raw_error(tmp_path):
    session=Session();session.fail=True;details=TripDetails(tmp_path,session)
    result=await details.get(TRIP,POINTS,CONFIG,SETTINGS)
    assert result['source']=='local' and 'secret' not in details.status
    await details.get(TRIP,POINTS,CONFIG,SETTINGS)
    assert len(session.calls)==1

@pytest.mark.asyncio
async def test_missing_or_disabled_provider_never_transmits_points(tmp_path):
    session=Session();details=TripDetails(tmp_path,session)
    assert (await details.get(TRIP,POINTS,{},SETTINGS))['source']=='local'
    assert (await details.get(TRIP,POINTS,CONFIG,{**SETTINGS,'tencent_enabled':False}))['source']=='local'
    assert not session.calls

@pytest.mark.asyncio
async def test_gcj_mode_skips_conversion_and_unconfirmed_end_has_no_parking_claim(tmp_path):
    session=Session();details=TripDetails(tmp_path,session)
    result=await details.get({**TRIP,'end_reason':'data_gap'},POINTS,CONFIG,{**SETTINGS,'tencent_coord_mode':'gcj02','kimi_enabled':False})
    assert result['source']=='tencent' and len(session.calls)==2
    assert all('/ws/geocoder/v1/' in url for url,_ in session.calls)
    assert all(kwargs['params']['get_poi']=='1' and 'sig' in kwargs['params'] for _,kwargs in session.calls)

@pytest.mark.asyncio
async def test_daily_provider_budget_does_not_make_billable_calls_after_limit(tmp_path):
    import time
    session=Session();details=TripDetails(tmp_path,session)
    details.write(tmp_path/'budget.json',{'day':int(time.time()//86400),'tencent':64,'kimi':20})
    assert (await details.get(TRIP,POINTS,CONFIG,SETTINGS))['source']=='local'
    assert not session.calls and 'daily_limit' in details.status

@pytest.mark.asyncio
async def test_service_keys_encrypted_and_not_returned_to_browser(tmp_path):
    r=module.Recorder(tmp_path)
    result=await r.action('trip_services',{**CONFIG,**SETTINGS})
    assert 'error' not in result
    assert result['trip_services_configured']=={'tencent_key':True,'tencent_sk':True,'kimi_key':True}
    assert all(key not in json.dumps(result) and key.encode() not in r.vault_file.read_bytes() for key in CONFIG.values())
    assert (await r.action('trip_services',{'kimi_model':'https://evil.test'})).get('error')
    assert (await r.action('trip_services',{'tencent_key':123})).get('error')
    await r.close()

def test_tencent_signature_uses_raw_values_and_exact_path():
    path='/ws/geocoder/v1/'
    first=signed_params(path,{'location':'30,120','get_poi':'1'},'synthetic','secret')
    reordered=signed_params(path,{'get_poi':'1','location':'30,120'},'synthetic','secret')
    assert first['sig']==reordered['sig']
    assert first['sig']!=signed_params(path.rstrip('/'),{'location':'30,120','get_poi':'1'},'synthetic','secret')['sig']
    assert first['sig']!=signed_params(path,{'location':'30%2C120','get_poi':'1'},'synthetic','secret')['sig']
    assert 'secret' not in json.dumps(first)

def test_average_speed_includes_intermediate_stops_and_has_no_route_fallback(tmp_path):
    r=module.Recorder(tmp_path)
    for seconds,lon,odo in [(0,120.,1000.),(60,120.001,1000.1),(120,120.001,1000.1),(180,120.002,1000.2)]:
        ingest(r.store,seconds,lon=lon,signals={'2103010':{'value':str(odo),'unit':'km'}})
    t=r.store.list_trips()[0]
    assert t['moving_observed_seconds']==120 and t['observed_span_seconds']==180
    assert t['odometer_delta']==.2 and t['average_speed_kmh']==4
    ingest(r.store,240,lon=120.003,signals={})
    assert r.store.list_trips()[0]['average_speed_kmh'] is None
    r.store.close()

@pytest.mark.asyncio
async def test_slow_image_delivery_does_not_block_vehicle_sampling(tmp_path):
    r=module.Recorder(tmp_path);delivery_started=asyncio.Event();release=asyncio.Event();sampled=asyncio.Event()
    async def flush():delivery_started.set();await release.wait()
    async def poll():r.last_poll=__import__('time').monotonic();sampled.set()
    r.flush_notifications=flush;r.poll=poll;r.settings['recording']=True
    r.selected=lambda:object();r.client=SimpleNamespace(authenticated=True)
    worker=asyncio.create_task(r.run_delivery());await delivery_started.wait()
    sampler=asyncio.create_task(r.run())
    try:await asyncio.wait_for(sampled.wait(),1)
    finally:
        r.stop.set();release.set();await asyncio.gather(worker,sampler);r.client=None;await r.close()
