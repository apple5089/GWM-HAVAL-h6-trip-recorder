import io,json,time
from PIL import Image
import pytest
from tencent_map import TencentMapCache,prepare,gcj02
from road_map import MAP_SIZE,pixel
from test_trip_details import Response

POINTS=[{'lat':30.,'lon':120.,'source_ms':0},{'lat':30.01,'lon':120.02,'source_ms':60000}]
CONFIG={'tencent_key':'synthetic-key','tencent_sk':'synthetic-secret'}

class ImageResponse(Response):
    async def iter_chunked(self,size):yield self.data

class Session:
    def __init__(self,body=None):
        if body is None:
            out=io.BytesIO();Image.new('RGB',MAP_SIZE,'white').save(out,format='PNG');body=out.getvalue()
        self.body=body;self.calls=[]
    def __call__(self,**kwargs):return self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def get(self,url,**kwargs):self.calls.append((url,kwargs));return ImageResponse(self.body)

def test_local_conversion_and_long_trip_fit_without_sending_track():
    assert gcj02(51.,0.)==(51.,0.)
    assert gcj02(30.,120.)!=(30.,120.)
    for points in (POINTS,[{'lat':30.,'lon':110.},{'lat':37.,'lon':117.}]):
        converted,view,params=prepare(points)
        for p in converted:
            x,y=pixel(p['lat'],p['lon'],view)
            assert 0<x<MAP_SIZE[0] and 70<y<MAP_SIZE[1]-50
        assert set(params)=={'center','zoom','size','scale','format','maptype'}
        assert params['size']=='1312*720'

@pytest.mark.asyncio
async def test_static_map_private_cache_and_no_route_in_provider_request(tmp_path):
    session=Session();cache=TencentMapCache(tmp_path,session)
    first=await cache.get(POINTS,CONFIG);second=await cache.get(POINTS,CONFIG)
    assert first['available'] and second['cache_hit'] and len(session.calls)==1
    url,kwargs=session.calls[0]
    assert url=='https://apis.map.qq.com/ws/staticmap/v2/' and kwargs['allow_redirects'] is False
    assert 'path' not in kwargs['params'] and 'locations' not in kwargs['params'] and 'sig' in kwargs['params']
    assert 'synthetic-secret' not in json.dumps(kwargs)
    assert (await TencentMapCache(tmp_path,Session(b'bad')).get(POINTS,CONFIG))['cache_hit']

@pytest.mark.asyncio
async def test_provider_failure_cools_down_and_daily_cap_blocks_network(tmp_path):
    session=Session(b'{"status":111,"message":"secret must not be logged"}');cache=TencentMapCache(tmp_path,session)
    assert not (await cache.get(POINTS,CONFIG))['available']
    assert cache.last_error=='tencent_code_111'
    assert json.loads((tmp_path/'budget.json').read_text())['last_error']=='tencent_code_111'
    assert not (await cache.get(POINTS,CONFIG))['available'] and len(session.calls)==1
    cache.write(tmp_path/'budget.json',json.dumps({'day':int(time.time()//86400),'requests':100,'next_at':0}).encode())
    assert not (await cache.get(POINTS,CONFIG))['available'] and len(session.calls)==1

@pytest.mark.asyncio
async def test_delivery_waits_for_basemap_instead_of_permanently_sending_bare_track(tmp_path):
    from test_recorder import module
    from test_notifications import trip
    r=module.Recorder(tmp_path);trip(r.store)
    class Missing:
        async def get(self,*args):return {'available':False}
    r.road_cache=Missing()
    with pytest.raises(ValueError,match='basemap_pending'):await r.trip_png(1,require_basemap=True)
    assert not (tmp_path/'images/trip-1.png').exists()
    await r.close()

@pytest.mark.asyncio
async def test_exhausted_basemap_retries_raise_one_notice_without_uploading_bare_image(tmp_path):
    from test_recorder import module
    from test_notifications import trip
    r=module.Recorder(tmp_path);trip(r.store);r.settings['notify']=True;r.vault['feishu_app']={'synthetic':True}
    class Missing:
        async def get(self,*args):return {'available':False}
    class Sender:
        async def upload_png(self,*args):raise AssertionError('a bare image must not be uploaded')
    r.road_cache=Missing();r.app_sender=Sender();r.queue_trip_image(1,0)
    for _ in range(5):
        r.store.db.execute("UPDATE outbox SET next_ms=0 WHERE kind='trip_image'");r.store.db.commit()
        await r.flush_notifications()
    assert r.store.db.execute("SELECT status FROM outbox WHERE kind='trip_image'").fetchone()[0]=='failed'
    assert r.store.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='alert'").fetchone()[0]==1
    assert not (tmp_path/'images/trip-1.png').exists()
    await r.close()

@pytest.mark.asyncio
async def test_provider_copyright_strip_is_preserved(tmp_path):
    from test_recorder import module
    from test_notifications import trip
    from route_image import render_trip,MAP_ORIGIN
    r=module.Recorder(tmp_path);t=trip(r.store);pts=r.store.points(t['id'])
    base=Image.new('RGB',MAP_SIZE,'white')
    for y in range(MAP_SIZE[1]-30,MAP_SIZE[1]):
        for x in range(MAP_SIZE[0]):base.putpixel((x,y),(40,80,120))
    out=io.BytesIO();base.save(out,format='PNG');session=Session(out.getvalue())
    roads=await TencentMapCache(tmp_path/'maps',session).get(pts,CONFIG)
    image=Image.open(io.BytesIO(render_trip(t,pts,roads)))
    assert image.crop((MAP_ORIGIN[0],MAP_ORIGIN[1]+MAP_SIZE[1]-30,MAP_ORIGIN[0]+MAP_SIZE[0],MAP_ORIGIN[1]+MAP_SIZE[1])).tobytes()==base.crop((0,MAP_SIZE[1]-30,*MAP_SIZE)).tobytes()
    await r.close()
