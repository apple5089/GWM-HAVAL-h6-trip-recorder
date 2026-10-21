import json
from datetime import datetime
import pytest
from trip_weather import TripWeather,parse_weather,BJT
from trip_details import TripDetails,category
from test_trip_details import Response,Session,TRIP,POINTS,CONFIG,SETTINGS

def fixture(when):
    day=when.strftime('%Y-%m-%d')
    return {'timezone':'Asia/Shanghai','utc_offset_seconds':28800,'latitude':37.51234,
            'daily':{'time':[day],'temperature_2m_min':[10.1],'temperature_2m_max':[19.4],'weather_code':[95]},
            'hourly':{'time':[f'{day}T{h:02}:00' for h in range(24)],'weather_code':[2]*24,'is_day':[0]*6+[1]*12+[0]*6}}

def test_hour_weather_is_not_daily_worst_weather_and_does_not_mix_dates():
    when=datetime(2026,10,19,7,25,tzinfo=BJT);data=fixture(when)
    result=parse_weather(data,when)
    assert result['condition']=='多云' and result['high_c']==19.4 and result['low_c']==10.1 and result['is_day']
    assert result['date']=='2026-10-19'
    with pytest.raises(ValueError):parse_weather(data,when.replace(day=18))
    data['utc_offset_seconds']=0
    with pytest.raises(ValueError):parse_weather(data,when)

@pytest.mark.asyncio
async def test_weather_sends_only_coarse_grid_and_correct_beijing_date_then_uses_cache(tmp_path):
    when=datetime.now(BJT).replace(hour=7,minute=25);data=fixture(when)
    class WeatherSession(Session):
        def get(self,url,**kwargs):self.calls.append((url,kwargs));return Response(data)
    session=WeatherSession();weather=TripWeather(tmp_path,session)
    trip={'end_ms':int(when.timestamp()*1000)};point={'lat':37.51234,'lon':121.31234}
    first=await weather.get(trip,point);second=await TripWeather(tmp_path,session).get(trip,point)
    assert first==second and first['available'] and len(session.calls)==1
    url,kwargs=session.calls[0];params=kwargs['params']
    assert url=='https://api.open-meteo.com/v1/forecast' and kwargs['allow_redirects'] is False
    assert (params['latitude'],params['longitude'])==(37.5,121.3)
    assert params['start_date']==params['end_date']==when.strftime('%Y-%m-%d') and params['timezone']=='Asia/Shanghai'
    cached=next(tmp_path.glob('weather-*.json')).read_text(encoding='utf-8')
    assert '37.51234' not in cached and 'latitude' not in cached

@pytest.mark.asyncio
async def test_bad_weather_does_not_invent_data_and_retries_are_bounded(tmp_path):
    session=Session();session.fail=True;weather=TripWeather(tmp_path,session)
    trip={'end_ms':int(datetime.now(BJT).timestamp()*1000)};point={'lat':37.5,'lon':121.3}
    assert not (await weather.get(trip,point))['available']
    assert not (await weather.get(trip,point))['available'] and len(session.calls)==1
    assert 'secret' not in weather.status
    assert not (await weather.get(trip,point,False))['available'] and len(session.calls)==1

@pytest.mark.asyncio
async def test_school_and_weather_reach_kimi_but_names_and_coordinates_stay_local(tmp_path):
    class SchoolSession(Session):
        def get(self,url,**kwargs):
            if 'translate' in url:return super().get(url,**kwargs)
            self.calls.append((url,kwargs))
            return Response({'status':0,'result':{'address_component':{'city':'示例市','district':'示例区','street':'私有道路'},
                'pois':[{'title':'私有实验中学','category':'教育学校:中学','_distance':8}]}})
    class Weather:
        async def get(self,*args):return {'available':True,'condition':'多云','high_c':19.4,'low_c':10.1,'is_day':True}
    session=SchoolSession();details=TripDetails(tmp_path,session);details.weather=Weather()
    result=await details.get(TRIP,POINTS,CONFIG,{**SETTINGS,'weather_enabled':True})
    assert result['end_category']=='学校' and '私有实验中学' in result['end']
    request=session.calls[-1][1]['json'];payload=json.loads(request['messages'][-1]['content'])
    assert payload['end_category']=='学校' and payload['destination_weather']['condition']=='多云'
    encoded=json.dumps(request,ensure_ascii=False)
    assert all(x not in encoded for x in ('私有实验中学','私有道路','120.001'))
    assert '赛车游戏' in request['messages'][0]['content']
    assert category('小学')=='学校' and category('完全未知')=='普通地点'

@pytest.mark.asyncio
async def test_weather_failure_still_generates_category_title_and_png(tmp_path):
    from test_recorder import module
    from test_notifications import trip
    from route_image import render_trip
    class Weather:
        async def get(self,*args):return {'available':False}
    session=Session();details=TripDetails(tmp_path/'details',session);details.weather=Weather()
    result=await details.get(TRIP,POINTS,CONFIG,{**SETTINGS,'weather_enabled':True})
    assert result['source']=='kimi' and not result['weather']['available']
    payload=json.loads(session.calls[-1][1]['json']['messages'][-1]['content'])
    assert 'destination_weather' not in payload
    recorder=module.Recorder(tmp_path/'recorder');t=trip(recorder.store)
    assert render_trip(t,recorder.store.points(t['id']),details=result).startswith(b'\x89PNG')
    await recorder.close()
