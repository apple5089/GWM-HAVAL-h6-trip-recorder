import json
import math
import pytest
from road_map import viewport,pixel,rounded_bbox,query_for,sanitize,RoadCache,ENDPOINT

POINTS=[{'lat':30.,'lon':120.},{'lat':30.01,'lon':120.02}]
DATA={'elements':[{'type':'way','tags':{'highway':'residential','name':'测试路','unneeded_private_tag':'ignored'},
                    'geometry':[{'lat':30.,'lon':120.},{'lat':30.01,'lon':120.02}]}]}

def test_projection_fits_route_and_coarsens_only_query_area():
    view=viewport(POINTS)
    for p in POINTS:
        x,y=pixel(p['lat'],p['lon'],view)
        assert 0<x<view['width'] and 0<y<view['height']
    s,w,n,e=rounded_bbox(view)
    assert s<=30 and n>=30.01 and w<=120 and e>=120.02
    query=query_for([s,w,n,e])
    assert 'out geom' in query and '[timeout:10]' in query
    assert all(str(p['lat']) not in query for p in [{'lat':30.001234}])
    assert pixel(30.01,120,view)[1]<pixel(30,120,view)[1]

def test_schema_filter_rejects_partial_timeout_and_invalid_geometry():
    clean=sanitize(DATA)
    assert len(clean)==1 and 'unneeded_private_tag' not in clean[0]['tags']
    assert not sanitize({'elements':[{'type':'way','tags':{'highway':'residential'},'geometry':[{'lat':float('nan'),'lon':120.},{'lat':30.,'lon':120.}]}]})
    with pytest.raises(ValueError):sanitize({'remark':'timeout','elements':DATA['elements']})

class Content:
    def __init__(self,data):self.data=data
    async def iter_chunked(self,size):yield json.dumps(self.data).encode()

class Response:
    def __init__(self,status,data):self.status=status;self.content=Content(data)
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass

class Session:
    def __init__(self,status=200):self.status=status;self.calls=[]
    def __call__(self,**kwargs):return self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def get(self,url,**kwargs):self.calls.append((url,kwargs));return Response(self.status,DATA)

@pytest.mark.asyncio
async def test_cache_prevents_repeat_requests_and_sends_only_fixed_area_query(tmp_path):
    session=Session();cache=RoadCache(tmp_path,session)
    first=await cache.get(POINTS);second=await cache.get(POINTS)
    assert first['available'] and second['cache_hit'] and len(session.calls)==1
    url,args=session.calls[0]
    assert url==ENDPOINT and args['allow_redirects'] is False
    assert set(args['params'])=={'data'} and 'H6PrivateTripRecorder' in args['headers']['User-Agent']
    assert 'source_ms' not in args['params']['data']
    # A second cache instance reuses the same private disk cache after restart.
    third=await RoadCache(tmp_path,Session(500)).get(POINTS)
    assert third['available'] and third['cache_hit']

@pytest.mark.asyncio
async def test_unavailable_service_cools_down_without_blocking_local_image(tmp_path):
    session=Session(503);cache=RoadCache(tmp_path,session)
    assert not (await cache.get(POINTS))['available']
    again=await cache.get(POINTS)
    assert again['reason']=='cooldown' and len(session.calls)==1

@pytest.mark.asyncio
async def test_daily_budget_blocks_queries_and_large_regions_are_not_downloaded(tmp_path):
    import time
    session=Session();cache=RoadCache(tmp_path,session)
    cache.write(tmp_path/'budget.json',{'day':int(time.time()//86400),'requests':8,'bytes':0,'next_at':0})
    assert (await cache.get(POINTS))['reason']=='cooldown'
    assert (await cache.get([{'lat':30.,'lon':120.},{'lat':40.,'lon':125.}]))['reason']=='range'
    assert not session.calls
