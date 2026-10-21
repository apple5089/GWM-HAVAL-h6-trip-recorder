"""Small, cached OSM vector extracts for a personal NAS. No tile scraping."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
import time
from aiohttp import ClientSession,ClientTimeout

ENDPOINT='https://overpass.private.coffee/api/interpreter'
USER_AGENT='H6PrivateTripRecorder/0.7 (personal NAS; cached low-volume road extracts)'
R=6378137
MAP_SIZE=(1312,720)

def project(lat,lon):
    lat=max(-85.,min(85.,lat))
    return (R*math.radians(lon),R*math.log(math.tan(math.pi/4+math.radians(lat)/2)))

def unproject(x,y):
    return (math.degrees(2*math.atan(math.exp(y/R))-math.pi/2),math.degrees(x/R))

def viewport(points,width=MAP_SIZE[0],height=MAP_SIZE[1]):
    coords=[project(p['lat'],p['lon']) for p in points if all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('lat','lon')) and -85<=p['lat']<=85 and -180<=p['lon']<=180]
    if not coords:raise ValueError('no_valid_map_points')
    xs,ys=zip(*coords);cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
    scale=min(width*.80/max(max(xs)-min(xs),200),height*.72/max(max(ys)-min(ys),200))
    south,west=unproject(cx-width/2/scale,cy-height/2/scale)
    north,east=unproject(cx+width/2/scale,cy+height/2/scale)
    return {'cx':cx,'cy':cy,'scale':scale,'width':width,'height':height,'bbox':[south,west,north,east]}

def pixel(lat,lon,view):
    x,y=project(lat,lon)
    return (view['width']/2+(x-view['cx'])*view['scale'],view['height']/2-(y-view['cy'])*view['scale'])

def rounded_bbox(view):
    south,west,north,east=view['bbox']
    return [math.floor(south*100)/100,math.floor(west*100)/100,math.ceil(north*100)/100,math.ceil(east*100)/100]

def area_km2(bbox):
    s,w,n,e=bbox
    return (n-s)*(e-w)*111.195**2*max(.05,math.cos(math.radians((s+n)/2)))

def query_for(bbox):
    box=','.join(f'{v:.2f}' for v in bbox)
    area=area_km2(bbox)
    highway='motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service' if area<250 else 'motorway|trunk|primary|secondary' if area<2500 else 'motorway|trunk'
    land=f'way["natural"="water"]({box});way["leisure"="park"]({box});way["landuse"~"^(forest|grass|recreation_ground)$"]({box});' if area<250 else ''
    return f'[out:json][timeout:10][maxsize:67108864];(way["highway"~"^({highway})(_link)?$"]({box});{land});out geom;'

def sanitize(data):
    if not isinstance(data,dict) or data.get('remark') or not isinstance(data.get('elements'),list):
        raise ValueError('road_schema')
    result=[];vertices=0
    for element in data['elements'][:6000]:
        if not isinstance(element,dict) or element.get('type')!='way':continue
        geometry=element.get('geometry');tags=element.get('tags',{})
        if not isinstance(geometry,list) or not isinstance(tags,dict):continue
        line=[]
        for p in geometry:
            if not isinstance(p,dict) or not all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('lat','lon')) or not -85<=p['lat']<=85 or not -180<=p['lon']<=180:
                line=[];break
            line.append([p['lat'],p['lon']])
        if len(line)<2 or len(line)>10000 or vertices+len(line)>120000:continue
        kept={k:v[:80] for k,v in tags.items() if k in ('highway','name','name:zh','ref','natural','landuse','leisure') and isinstance(v,str)}
        if not any(k in kept for k in ('highway','natural','landuse','leisure')):continue
        result.append({'geometry':line,'tags':kept});vertices+=len(line)
    return result

class RoadCache:
    def __init__(self,directory,session_factory=ClientSession):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700,parents=True,exist_ok=True);self.directory.chmod(0o700)
        self.session_factory=session_factory;self.lock=asyncio.Lock();self.last_status='道路数据尚未请求'
        self.last_error=None

    def write(self,path,data):
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8');temporary.chmod(0o600);temporary.replace(path)

    async def get(self,points):
        view=viewport(points);bbox=rounded_bbox(view);now=time.time()
        async with self.lock:
            if area_km2(bbox)>100000 or bbox[0]<-85 or bbox[2]>85 or bbox[1]<-180 or bbox[3]>180:
                self.last_status='行程范围过大，保留无底图轨迹';return {'available':False,'reason':'range','features':[]}
            key=hashlib.sha256(json.dumps(bbox).encode()).hexdigest()[:24]
            path=self.directory/(key+'.json')
            if path.exists():
                try:
                    cached=json.loads(path.read_text(encoding='utf-8'))
                    if now-cached['saved_at']<30*86400:
                        self.last_error=None
                        self.last_status='道路底图已加载（本地缓存）';return {**cached,'available':True,'cache_hit':True}
                except (ValueError,KeyError,TypeError):pass
            budget_path=self.directory/'budget.json'
            try:budget=json.loads(budget_path.read_text(encoding='utf-8'))
            except (OSError,ValueError):budget={}
            day=int(now//86400)
            if budget.get('day')!=day:budget={'day':day,'requests':0,'bytes':0,'next_at':0}
            if budget['requests']>=8 or budget['bytes']>=8*1024*1024 or now<budget['next_at']:
                self.last_status='道路服务冷却或当日额度已用完';return {'available':False,'reason':'cooldown','features':[]}
            budget['requests']+=1;budget['next_at']=now+60;self.write(budget_path,budget)
            try:
                async with self.session_factory(timeout=ClientTimeout(total=18),trust_env=False) as session:
                    async with session.get(ENDPOINT,params={'data':query_for(bbox)},headers={'User-Agent':USER_AGENT},allow_redirects=False) as response:
                        if response.status!=200:raise ValueError('road_http_'+str(response.status))
                        body=bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            body.extend(chunk)
                            budget['bytes']+=len(chunk)
                            if len(body)>4*1024*1024 or budget['bytes']>8*1024*1024:raise ValueError('road_size')
                        features=sanitize(json.loads(body))
                if not any('highway' in f['tags'] for f in features):raise ValueError('roads_empty')
                result={'saved_at':now,'bbox':bbox,'features':features,'source':'OpenStreetMap','provider':'Private.coffee Overpass'}
                self.write(path,result);self.write(budget_path,budget)
                self.last_error=None
                self.last_status='道路底图已加载并缓存';return {**result,'available':True,'cache_hit':False}
            except (Exception,asyncio.CancelledError) as error:
                self.last_error=str(error) if type(error) is ValueError and (str(error) in ('road_size','road_schema','roads_empty') or str(error).startswith('road_http_') and str(error)[10:].isdigit()) else type(error).__name__
                budget['next_at']=now+900;self.write(budget_path,budget)
                self.last_status='道路服务暂不可用，保留轨迹与本地绘图'
                if isinstance(error,asyncio.CancelledError):raise
                return {'available':False,'reason':'unavailable','features':[]}
