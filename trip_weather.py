"""Coarse-area weather at the trip's Beijing date/hour, with private bounded cache."""
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import time
from aiohttp import ClientSession, ClientTimeout

BJT=timezone(timedelta(hours=8))
PROVIDER='https://api.open-meteo.com/v1/forecast'

def condition(code):
    if code==0:return '晴','sun'
    if code in (1,2):return '少云' if code==1 else '多云','partly'
    if code==3:return '阴','cloud'
    if code in (45,48):return '雾','fog'
    if code in (51,53,55,56,57):return '毛毛雨','rain'
    if code in (61,63,65,66,67,80,81,82):return '雨','rain'
    if code in (71,73,75,77,85,86):return '雪','snow'
    if code in (95,96,99):return '雷雨','storm'
    raise ValueError('weather_code')

def finite_temperature(v):
    return type(v) in (int,float) and math.isfinite(v) and -90<=v<=65

def parse_weather(data,when):
    date=when.strftime('%Y-%m-%d');hour=when.strftime('%Y-%m-%dT%H:00')
    if data.get('timezone')!='Asia/Shanghai' or data.get('utc_offset_seconds')!=28800:raise ValueError('weather_timezone')
    daily=data['daily'];hourly=data['hourly'];index=daily['time'].index(date);slot=hourly['time'].index(hour)
    low=daily['temperature_2m_min'][index];high=daily['temperature_2m_max'][index]
    if not all(finite_temperature(v) for v in (low,high)) or low>high:raise ValueError('weather_temperature')
    code=hourly['weather_code'][slot]
    if type(code) is not int:raise ValueError('weather_code')
    label,icon=condition(code)
    day=hourly.get('is_day',[1]*len(hourly['time']))[slot]
    return {'available':True,'condition':label,'icon':icon,'is_day':day==1,'high_c':round(high,1),'low_c':round(low,1),
            'date':date,'hour':when.hour,'source':'Open-Meteo','kind':'天气模型','attribution':'https://open-meteo.com/'}

class TripWeather:
    def __init__(self,directory,session_factory=ClientSession):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True,mode=0o700);self.directory.chmod(0o700)
        self.session_factory=session_factory;self.lock=asyncio.Lock();self.status='天气尚未查询'

    def read(self,path):
        try:return json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}

    def write(self,path,data):
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temp.chmod(0o600);temp.replace(path)

    async def get(self,trip,point,enabled=True):
        missing={'available':False}
        if not enabled:self.status='天气已关闭';return missing
        try:when=datetime.fromtimestamp(trip['end_ms']/1000,BJT)
        except (KeyError,TypeError,ValueError,OverflowError):return missing
        # Never send precise vehicle coordinates or the recorded polyline to weather services.
        lat,lon=round(point['lat'],1),round(point['lon'],1)
        if not (-85<=lat<=85 and -180<=lon<=180) or (lat,lon)==(0,0):return missing
        date=when.strftime('%Y-%m-%d');identity=json.dumps([date,lat,lon])
        path=self.directory/('weather-'+hashlib.sha256(identity.encode()).hexdigest()[:24]+'.json')
        async with self.lock:
            cached=self.read(path)
            # Keep a successful area/day snapshot stable for title, preview and delivery.
            if cached.get('data'):
                try:result=parse_weather(cached['data'],when);self.status='终点周边天气已就绪（缓存）';return result
                except (KeyError,ValueError,TypeError,IndexError):pass
            age=(datetime.now(BJT).date()-when.date()).days
            if not -15<=age<=92:self.status='该日期超出天气查询范围';return missing
            budget_path=self.directory/'budget.json';budget=self.read(budget_path);today=datetime.now(BJT).strftime('%Y-%m-%d')
            if budget.get('day')!=today:budget={'day':today,'requests':0,'next_at':0}
            if budget.get('next_at',0)>time.time() or budget.get('requests',0)>=64:self.status='天气等待重试';return missing
            budget['requests']=budget.get('requests',0)+1;self.write(budget_path,budget)
            params={'latitude':lat,'longitude':lon,'timezone':'Asia/Shanghai','start_date':date,'end_date':date,
                    'hourly':'weather_code,is_day','daily':'temperature_2m_max,temperature_2m_min'}
            try:
                async with self.session_factory(timeout=ClientTimeout(total=6),trust_env=False) as session:
                    async with session.get(PROVIDER,params=params,allow_redirects=False) as response:
                        if response.status!=200:raise ValueError('weather_http')
                        body=bytearray()
                        async for chunk in response.content.iter_chunked(16384):
                            body.extend(chunk)
                            if len(body)>128000:raise ValueError('weather_size')
                        data=json.loads(body)
                result=parse_weather(data,when)
                # Do not retain provider-returned coordinates, elevations or other unneeded fields.
                safe={k:data[k] for k in ('timezone','utc_offset_seconds','daily','hourly')}
                self.write(path,{'data':safe,'saved_at':time.time()});self.status='终点周边天气已就绪';return result
            except asyncio.CancelledError:raise
            except Exception:
                budget['next_at']=time.time()+900;self.write(budget_path,budget);self.status='天气暂不可用，稍后重试';return missing
