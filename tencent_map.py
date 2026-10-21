"""Tencent static basemaps; the recorded polyline stays on the NAS."""
import asyncio
import hashlib
import io
import json
import math
from pathlib import Path
import time
from aiohttp import ClientSession,ClientTimeout
from PIL import Image
from road_map import project,unproject,pixel,MAP_SIZE,R
from trip_details import signed_params

URL='https://apis.map.qq.com/ws/staticmap/v2/'

def gcj02(lat,lon):
    # Local WGS84 -> GCJ-02 approximation, verified against Tencent endpoint samples.
    if not (72.004<=lon<=137.8347 and .8293<=lat<=55.8271):return lat,lon
    x,y=lon-105,lat-35;pi=math.pi
    dlat=-100+2*x+3*y+.2*y*y+.1*x*y+.2*math.sqrt(abs(x))
    dlat+=(20*math.sin(6*x*pi)+20*math.sin(2*x*pi))*2/3
    dlat+=(20*math.sin(y*pi)+40*math.sin(y*pi/3))*2/3
    dlat+=(160*math.sin(y*pi/12)+320*math.sin(y*pi/30))*2/3
    dlon=300+x+2*y+.1*x*x+.1*x*y+.1*math.sqrt(abs(x))
    dlon+=(20*math.sin(6*x*pi)+20*math.sin(2*x*pi))*2/3
    dlon+=(20*math.sin(x*pi)+40*math.sin(x*pi/3))*2/3
    dlon+=(150*math.sin(x*pi/12)+300*math.sin(x*pi/30))*2/3
    rad=math.radians(lat);magic=1-.006693421622965943*math.sin(rad)**2;root=math.sqrt(magic)
    return lat+dlat*180/((6378245*(1-.006693421622965943))/(magic*root)*pi),lon+dlon*180/(6378245/root*math.cos(rad)*pi)

def prepare(points,mode='gps'):
    converted=[]
    for p in points:
        if not all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('lat','lon')) or not -85<=p['lat']<=85 or not -180<=p['lon']<=180 or (p['lat'],p['lon'])==(0,0):continue
        lat,lon=gcj02(p['lat'],p['lon']) if mode=='gps' else (p['lat'],p['lon'])
        converted.append({**p,'lat':lat,'lon':lon})
    if not converted:raise ValueError('no_map_points')
    projected=[project(p['lat'],p['lon']) for p in converted];xs,ys=zip(*projected)
    cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
    width,height=MAP_SIZE
    scale=min(width*.80/max(max(xs)-min(xs),100),height*.68/max(max(ys)-min(ys),100))
    zoom=max(4,min(18,math.floor(math.log2(scale*2*math.pi*R/256))))
    lat,lon=unproject(cx,cy);lat=round(lat,6);lon=round(lon,6);cx,cy=project(lat,lon)
    scale=256*2**zoom/(2*math.pi*R)
    view={'cx':cx,'cy':cy,'scale':scale,'width':width,'height':height}
    return converted,view,{'center':f'{lat:.6f},{lon:.6f}','zoom':str(zoom),'size':f'{width}*{height}','scale':'1','format':'png','maptype':'roadmap'}

class TencentMapCache:
    def __init__(self,directory,session_factory=ClientSession):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True,mode=0o700);self.directory.chmod(0o700)
        self.session_factory=session_factory;self.lock=asyncio.Lock();self.last_status='腾讯道路底图尚未请求';self.last_error=None

    def write(self,path,data):
        temp=path.with_suffix('.tmp');temp.write_bytes(data);temp.chmod(0o600);temp.replace(path)

    def budget(self):
        path=self.directory/'budget.json'
        try:data=json.loads(path.read_text())
        except (OSError,ValueError):data={}
        now=time.time();day=int(now//86400)
        if data.get('day')!=day:data={'day':day,'requests':0,'next_at':0}
        return path,data,now

    async def get(self,points,config,mode='gps'):
        converted,view,params=prepare(points,mode)
        missing={'available':False,'features':[],'reason':'unavailable'}
        if not config.get('tencent_key'):
            self.last_status='腾讯地图尚未配置';return missing
        async with self.lock:
            key=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()[:24]
            path=self.directory/(key+'.png');now=time.time()
            if path.exists() and now-path.stat().st_mtime<30*86400:
                self.last_error=None;self.last_status='腾讯道路底图已加载（本地缓存）'
                return {'available':True,'source':'Tencent','image':path.read_bytes(),'view':view,'points':converted,'cache_hit':True}
            budget_path,budget,now=self.budget()
            if budget['requests']>=100 or now<budget['next_at']:
                self.last_status='腾讯静态图冷却或已达本机每日 100 张上限';return {**missing,'reason':'cooldown'}
            budget['requests']+=1
            self.write(budget_path,json.dumps(budget).encode())
            try:
                signed=signed_params('/ws/staticmap/v2/',params,config['tencent_key'],config.get('tencent_sk',''))
                async with self.session_factory(timeout=ClientTimeout(total=12),trust_env=False) as session:
                    async with session.get(URL,params=signed,allow_redirects=False) as response:
                        if response.status!=200:raise ValueError('map_http_'+str(response.status))
                        body=bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            body.extend(chunk)
                            if len(body)>8*1024*1024:raise ValueError('map_size')
                if not body.startswith(b'\x89PNG\r\n\x1a\n'):
                    try:code=json.loads(body).get('status')
                    except (ValueError,AttributeError):code=None
                    raise ValueError('tencent_code_'+str(code) if type(code) is int and -10000<code<10000 else 'map_format')
                with Image.open(io.BytesIO(body)) as im:
                    if im.size!=MAP_SIZE:raise ValueError('map_dimensions')
                    im.verify()
                self.write(path,bytes(body));budget.update(next_at=0,last_error=None,last_ok=time.time());self.write(budget_path,json.dumps(budget).encode())
                self.last_error=None;self.last_status='腾讯道路底图已加载并缓存'
                return {'available':True,'source':'Tencent','image':bytes(body),'view':view,'points':converted,'cache_hit':False}
            except asyncio.CancelledError:raise
            except Exception as error:
                value=str(error)
                self.last_error=value if type(error) is ValueError and (value in ('map_size','map_format','map_dimensions') or value.startswith(('map_http_','tencent_code_')) and value.rsplit('_',1)[-1].isdigit()) else type(error).__name__
                budget.update(next_at=time.time()+45,last_error=self.last_error);self.write(budget_path,json.dumps(budget).encode())
                self.last_status='腾讯道路底图暂未取得，自动发图等待重试'
                return missing
