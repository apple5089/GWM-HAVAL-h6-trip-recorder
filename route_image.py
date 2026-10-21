"""Generate a private trip PNG locally. No map tiles, geocoding or network."""
from datetime import datetime,timezone,timedelta
from functools import lru_cache
import io
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import journey_icons
import weather_effects
import racing_ui
from trip_details import destination_label

FONT=Path(__file__).resolve().parent/'static/fonts/H6RouteSC.ttf'
SIZE=(1440,1530)
MAP_ORIGIN=(64,730)

@lru_cache(maxsize=12)
def font(size):
    return ImageFont.truetype(str(FONT),size)

def segments(points):
    groups=[];group=[];previous=None
    for p in points:
        lat,lon=p.get('lat'),p.get('lon')
        valid=all(type(v) in (float,int) and math.isfinite(v) for v in (lat,lon)) and -90<=lat<=90 and -180<=lon<=180 and (lat,lon)!=(0,0)
        if not valid or p.get('break_before') or previous is not None and p['source_ms']-previous['source_ms']>180000:
            if group:groups.append(group)
            group=[]
        if valid:group.append(p)
        previous=p if valid else None
    if group:groups.append(group)
    return groups

@lru_cache(maxsize=8)
def road_font(size):
    return ImageFont.truetype(str(FONT.parent/'H6MapLabelsSC.ttf'),size)

def road_label(value):
    if not isinstance(value,str):return ''
    safe=''.join(c for c in value if c.isascii() and c.isalnum() or 0x4e00<=ord(c)<=0x9fff or c in ' -·()（）')
    return safe[:16]

def fuel_stat(trip):
    drop=trip.get('fuel_gauge_drop_l')
    if type(drop) not in (int,float) or not math.isfinite(drop):
        return ('本次用油 · 油表估算','暂无数据','')
    if drop<0:
        return ('油量回升 · 加油或波动',f'+{-drop:g}','L')
    if drop==0:
        return ('本次用油 · 油表估算','油表未变','')
    return ('本次用油 · 油表估算',f'≈{drop:g}','L')

def remaining_fuel_stat(trip):
    value=trip.get('fuel_remaining_l')
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=200:
        return ('剩余油量 · 结束时','暂无数据','')
    return ('剩余油量 · 结束时',f'{value:g}','L')

def estimated_fuel_stat(trip):
    value=trip.get('estimated_fuel_l_per_100km');distance=trip.get('fuel_estimate_distance_km')
    if (type(value) in (int,float) and math.isfinite(value) and value>0 and
        type(distance) in (int,float) and math.isfinite(distance) and distance>=10):
        return (f'预估油耗 · 近{distance:g}km油表估算',f'≈{value:g}','L/100 km')
    return ('预估油耗 · 油表估算','数据积累中' if trip.get('fuel_estimate_status')=='collecting' else '暂无数据','')

def segment_hits_rect(a,b,rect):
    low,high=0.,1.
    for origin,delta,minimum,maximum in ((a[0],b[0]-a[0],rect[0],rect[2]),(a[1],b[1]-a[1],rect[1],rect[3])):
        if abs(delta)<1e-9:
            if not minimum<=origin<=maximum:return False
        else:
            left,right=sorted(((minimum-origin)/delta,(maximum-origin)/delta))
            low=max(low,left);high=min(high,right)
            if low>high:return False
    return True

def render_trip(trip,points,roads=None,details=None):
    from road_map import viewport,pixel,unproject,MAP_SIZE
    from PIL import ImageFilter
    tencent=bool(roads and roads.get('available') and roads.get('source')=='Tencent')
    if tencent:points=roads['points']
    groups=segments(points);flat=[p for g in groups for p in g]
    if not flat:raise ValueError('no_route_points')
    view=roads['view'] if tencent else viewport(flat)
    available=bool(roads and roads.get('available') and (roads.get('features') or tencent))
    details=details or {};section=details.get('segment');stops=details.get('stops') or []
    extra=60+72*math.ceil(len(stops)/2) if stops else 0
    image=racing_ui.background((SIZE[0],SIZE[1]+extra));draw=ImageDraw.Draw(image)
    ink='#eff8ff';muted='#b8d5eb';cyan='#00eaff';green='#25ffc3';orange='#ffd063';pink='#ff329f'
    def text(x,y,value,size=22,color=ink):ImageDraw.Draw(image).text((x,y),str(value),font=font(size),fill=color)
    def time(ms):return datetime.fromtimestamp(ms/1000,timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S')
    racing_ui.panel(image,(26,16,361,72),cyan)
    racing_ui.lettering(image,(54,32),f"H6 / JOURNEY #{trip['id']}",25,282,cyan)
    racing_ui.checks(draw,386,23,4,3,14)
    text(465,30,('全程 ' if section else '')+time(trip['start_ms'])+' → '+time(trip['end_ms'])+'（北京时间）',18,muted)
    # The result badge follows actual trip state, including interrupted observation.
    badge=('SECTOR','FOCUS') if section else ('JOURNEY','COMPLETE') if trip['closed'] and trip['end_reason']!='data_gap' else ('SIGNAL','PAUSED') if trip['end_reason']=='data_gap' else ('JOURNEY','LIVE')
    racing_ui.lettering(image,(1000,28),badge[0],58,390)
    racing_ui.lettering(image,(978,88),badge[1],63,422)
    racing_ui.lettering(image,(1100,159),'DRIVE RESULTS',24,284,pink)
    racing_ui.checks(draw,1334,18,4,5,17,'#3c446b')
    title=details.get('title') or '自由巡航 · 旅程存档'
    journey_icons.trophy(draw,54,96,size=62,color=orange)
    racing_ui.lettering(image,(137,103),title,53,810)
    stopped=trip['closed'] and trip['end_reason']!='data_gap' and not section
    state=f"局部放大 {section['number']}/{section['total']}" if section else '已停车 · 联合推定' if trip['end_reason']=='parked_engine_off' else '已停车 · 位置推定' if stopped else '停车待确认' if trip['end_reason']=='data_gap' else '行程记录中'
    racing_ui.panel(image,(1092,205,1409,258),green if stopped else orange,fill='#052322',cut=9)
    draw.ellipse((1114,224,1128,238),fill=green if stopped else orange);text(1143,216,state,20,green if stopped else orange)
    for y,prefix,value,color in ((169,'全程起点' if section else '起点',details.get('start') or '采样起点',green),(212,'全程末次位置' if section and trip['end_reason']=='data_gap' else '全程终点' if section else '末次位置' if trip['end_reason']=='data_gap' else '终点',details.get('end') or '最后采样位置',pink)):
        journey_icons.destination(draw,59,y+3,color=color)
        label=prefix+' · '+value
        while draw.textlength(label,font=road_font(23))>939 and len(label)>8:label=label[:-2]+'…'
        draw.text((115,y),label,font=road_font(23),fill=muted)
    stats=[('行驶距离',f"{trip['odometer_delta']:g}" if trip['odometer_delta'] is not None else '未提供','km' if trip['odometer_delta'] is not None else '',cyan),
           ('行程用时 · 推定',f"{trip['observed_span_seconds']/60:.1f}",'分钟',ink),
           ('平均速度 · 估算',f"{trip['average_speed_kmh']:g}" if trip.get('average_speed_kmh') is not None else '暂无数据','km/h' if trip.get('average_speed_kmh') is not None else '',ink),
           (*remaining_fuel_stat(trip),green),
           (*fuel_stat(trip),orange),
           (*estimated_fuel_stat(trip),orange)]
    for i,(label,value,unit,color) in enumerate(stats):
        if section and i<3:label=('全程距离','全程用时 · 推定','全程均速 · 估算')[i]
        x=28+(i%3)*466;y=280+(i//3)*134
        racing_ui.panel(image,(x,y,x+450,y+120),pink if i==5 else cyan)
        racing_ui.icon(draw,x+24,y+32,('road','clock','speed','fuel','fuel','fuel')[i],green if i==3 else pink if i>3 else ink)
        # Long fuel windows and missing values must still fit on small screens.
        label_size=21
        while draw.textlength(label,font=font(label_size))>330 and label_size>15:label_size-=1
        text(x+109,y+14,label,label_size,muted)
        unit_width=draw.textlength(unit,font=font(24)) if unit else 0
        value_width,_=racing_ui.lettering(image,(x+108,y+54),value,55,max(135,315-unit_width),color)
        text(x+119+value_width,y+75,unit,24,muted)
        draw.line((x+226,y+111,x+343,y+111),fill=pink if i%2==0 else cyan,width=3)
    # A compact weather / destination row; all icons are local vector drawings.
    racing_ui.panel(image,(28,550,915,660),cyan)
    racing_ui.panel(image,(933,550,1410,660),green,fill='#032526')
    racing_ui.skyline(draw,(1190,566,1390,646))
    weather=details.get('weather') or {}
    if weather.get('available'):
        journey_icons.weather(draw,93,606,weather['icon'],weather.get('is_day',True))
        text(150,566,'本段附近 · 时段天气' if section else '终点周边 · 行程时段天气',20,muted)
        racing_ui.lettering(image,(150,605),weather['condition']+(' · 日间' if weather.get('is_day',True) else ' · 夜间'),34,365)
        for x in (545,729):draw.line((x,573,x,640),fill='#1a4860',width=1)
        text(567,564,'当天最高',20,muted);text(751,564,'当天最低',20,muted)
        text(567,601,f"{weather['high_c']:g}°C",34,orange);text(751,601,f"{weather['low_c']:g}°C",34,cyan)
    else:
        journey_icons.weather(draw,93,606,'cloud')
        text(150,570,'天气暂不可用',26,muted);text(150,614,'行程记录已保留',20,muted)
    end_category=details.get('end_category','普通地点')
    journey_icons.destination(draw,960,585,color=green)
    text(1027,564,'全程终点附近' if section else '终点附近',20,green)
    draw.text((1027,605),destination_label(end_category),font=road_font(28),fill=ink,stroke_width=1)
    racing_ui.panel(image,(28,677,1410,721),cyan,cut=10)
    if section:
        clock=lambda ms:datetime.fromtimestamp(ms/1000,timezone(timedelta(hours=8))).strftime('%H:%M')
        draw.text((58,685),f"第 {section['number']}/{section['total']} 段放大 · {clock(section['start_ms'])}–{clock(section['end_ms'])}",font=road_font(22),fill=muted)
    else:racing_ui.lettering(image,(58,687),'路网底图与采样轨迹' if available else '道路暂不可用 · 采样轨迹',24,480)
    racing_ui.checks(draw,426,680,5,2,17,'#067fae')
    text(650,686,f"途中停留 {details.get('stop_count',0)} 处（≥3分钟）",20,muted)
    effects=bool(available and details.get('weather_effects',True) and weather_effects.enabled(weather))
    if effects:draw.text((1170,686),'天气氛围 · '+weather['condition'],font=road_font(20),fill=muted)
    scene=racing_ui.night_map(Image.open(io.BytesIO(roads['image']))) if tencent else Image.new('RGB',MAP_SIZE,'#081627')
    sd=ImageDraw.Draw(scene)
    def xy(p):return pixel(p['lat'],p['lon'],view)
    # Real OSM geometry is kept separate from the user's recorded polyline.
    features=roads.get('features',[]) if available else []
    for f in features:
        tags=f['tags'];line=[pixel(lat,lon,view) for lat,lon in f['geometry']]
        if len(line)>=4 and f['geometry'][0]==f['geometry'][-1]:
            if tags.get('natural')=='water':sd.polygon(line,fill='#07364a',outline='#12465b')
            elif tags.get('leisure')=='park' or tags.get('landuse') in ('forest','grass','recreation_ground'):sd.polygon(line,fill='#0a2a2a',outline='#123738')
    if not tencent:
        for x in range(0,MAP_SIZE[0],82):sd.line((x,0,x,MAP_SIZE[1]),fill='#0d2032',width=1)
        for y in range(0,MAP_SIZE[1],72):sd.line((0,y,MAP_SIZE[0],y),fill='#0d2032',width=1)
    widths={'motorway':9,'trunk':8,'primary':7,'secondary':6,'tertiary':5,'unclassified':4,'residential':3,'living_street':3,'service':2}
    road_features=[f for f in features if 'highway' in f['tags']]
    for f in sorted(road_features,key=lambda f:widths.get(f['tags']['highway'].replace('_link',''),2)):
        line=[pixel(lat,lon,view) for lat,lon in f['geometry']];kind=f['tags']['highway'].replace('_link','');width=widths.get(kind,2)
        sd.line(line,fill='#12283c',width=width+4,joint='curve')
        sd.line(line,fill='#43637d' if width>=6 else '#2c4960',width=width,joint='curve')
    occupied=[];names=set()
    for f in sorted(road_features,key=lambda f:-widths.get(f['tags']['highway'].replace('_link',''),2)):
        label=road_label(f['tags'].get('name:zh') or f['tags'].get('name') or f['tags'].get('ref'))
        if not label or label in names or len(occupied)>=14:continue
        line=[pixel(lat,lon,view) for lat,lon in f['geometry']]
        spans=[(math.hypot(b[0]-a[0],b[1]-a[1]),a,b) for a,b in zip(line,line[1:]) if 50<a[0]<1262 and 70<a[1]<640 and 50<b[0]<1262 and 70<b[1]<640]
        if not spans:continue
        length,a,b=max(spans,key=lambda item:item[0]);x,y=(a[0]+b[0])/2,(a[1]+b[1])/2
        label_font=road_font(22);width=sd.textlength(label,font=label_font);rect=(x-width/2-7,y-15,x+width/2+7,y+18)
        if rect[0]<15 or rect[2]>MAP_SIZE[0]-15 or any(not(rect[2]<o[0] or rect[0]>o[2] or rect[3]<o[1] or rect[1]>o[3]) for o in occupied):continue
        sd.text((x-width/2,y-14),label,font=label_font,fill='#a7c4d7',stroke_width=3,stroke_fill='#081627');occupied.append(rect);names.add(label)
    if effects:scene=weather_effects.apply_map_weather(scene,weather,[[xy(p) for p in group] for group in groups],trip['id'])
    glow=Image.new('RGBA',MAP_SIZE,(0,0,0,0));gd=ImageDraw.Draw(glow)
    for group in groups:
        line=[xy(p) for p in group]
        if len(line)>1:gd.line(line,fill=(30,222,255,140),width=22,joint='curve')
    scene=Image.alpha_composite(scene.convert('RGBA'),glow.filter(ImageFilter.GaussianBlur(9)))
    sd=ImageDraw.Draw(scene)
    for group in groups:
        line=[xy(p) for p in group]
        if len(line)>1:
            if tencent:sd.line(line,fill='#083345',width=15,joint='curve')
            sd.line(line,fill='#21cbe6',width=10,joint='curve');sd.line(line,fill='#d9fbff',width=3,joint='curve')
        for x,y in line[::max(1,len(line)//100)]:sd.ellipse((x-3,y-3,x+3,y+3),fill='#d0faff')
        for a,b in list(zip(line,line[1:]))[::max(1,len(line)//16)]:
            length=math.hypot(b[0]-a[0],b[1]-a[1])
            if length<45:continue
            mx,my=(a[0]+b[0])/2,(a[1]+b[1])/2;ux,uy=(b[0]-a[0])/length,(b[1]-a[1])/length
            sd.polygon([(mx+10*ux,my+10*uy),(mx-8*ux-6*uy,my-8*uy+6*ux),(mx-8*ux+6*uy,my-8*uy-6*ux)],fill='#ffffff')
    start,end=xy(flat[0]),xy(flat[-1]);close=math.hypot(start[0]-end[0],start[1]-end[1])<65
    for index,((x,y),color,label,letter) in enumerate(((start,green,'分段起点' if section else '起点','S'),(end,pink,'分段终点' if section else '停车点' if stopped else '终点','P' if stopped else 'E'))):
        sd.ellipse((x-20,y-20,x+20,y+20),outline=color,width=2)
        sd.ellipse((x-13,y-13,x+13,y+13),fill='#071421',outline=color,width=3)
        sd.text((x-6,y-13),letter,font=font(18),fill=color)
        for dx,dy in ((-27,0),(27,0),(0,-27),(0,27)):
            sd.line((x+dx*.8,y+dy*.8,x+dx,y+dy),fill=color,width=2)
        lx=x-140 if close and index==0 else x+29;ly=y-60 if y>MAP_SIZE[1]-150 else y+18
        label_width=sd.textlength(label,font=font(22))+36
        lx=max(10,min(MAP_SIZE[0]-label_width-10,lx));ly=max(80,min(MAP_SIZE[1]-115,ly))
        sd.line((x,y,lx+5,ly+17),fill=color,width=1)
        sd.rounded_rectangle((lx,ly,lx+label_width,ly+37),radius=7,fill='#0a1b2d',outline=color)
        sd.text((lx+18,ly+3),label,font=font(22),fill=color)
        occupied.append((lx,ly,lx+label_width,ly+37))
    route_lines=[(xy(a),xy(b)) for group in groups for a,b in zip(group,group[1:])]
    occupied.extend([(0,0,MAP_SIZE[0],80),(MAP_SIZE[0]-112,0,MAP_SIZE[0],150),(0,MAP_SIZE[1]-45,*MAP_SIZE)])
    for stop in stops:
        x,y=xy(stop)
        if not 0<=x<MAP_SIZE[0] or not 80<y<MAP_SIZE[1]-45:continue
        label=f"停{stop['index']} · {stop['duration_seconds']/60:.1f}分钟"
        width=sd.textlength(label,font=font(19))+24
        for dx,dy in ((30,-53),(-width-30,-53),(30,27),(-width-30,27)):
            lx=max(12,min(MAP_SIZE[0]-width-12,x+dx));ly=max(82,min(MAP_SIZE[1]-90,y+dy));rect=(lx,ly,lx+width,ly+32)
            if any(not(rect[2]+6<o[0] or rect[0]-6>o[2] or rect[3]+6<o[1] or rect[1]-6>o[3]) for o in occupied):continue
            if any(segment_hits_rect(a,b,(rect[0]-8,rect[1]-8,rect[2]+8,rect[3]+8)) for a,b in route_lines):continue
            sd.line((x,y,lx+width/2,ly+16),fill=orange,width=1)
            sd.rounded_rectangle(rect,radius=6,fill='#202416',outline=orange)
            sd.text((lx+12,ly+2),label,font=font(19),fill=orange);occupied.append(rect);break
        if min(math.hypot(x-start[0],y-start[1]),math.hypot(x-end[0],y-end[1]))>=45:
            sd.ellipse((x-13,y-13,x+13,y+13),fill='#15202c',outline=orange,width=2)
            number=str(stop['index']);nw=sd.textlength(number,font=font(16));sd.text((x-nw/2,y-10),number,font=font(16),fill=orange)
    # North marker and a ground-distance scale are derived from the projection.
    sd.text((1257,24),'N',font=font(20),fill='#d9eef8');sd.line((1266,55,1266,99),fill='#91b6ca',width=2)
    sd.polygon([(1266,46),(1260,61),(1272,61)],fill=cyan)
    latitude,_=unproject(view['cx'],view['cy']);meters_per_pixel=math.cos(math.radians(latitude))/view['scale'];target=120*meters_per_pixel
    power=10**math.floor(math.log10(max(target,.01)));meters=max(v*power for v in (1,2,5) if v*power<=target) if target>=power else power
    bar=meters/meters_per_pixel
    legend_bottom=70 if tencent else MAP_SIZE[1]-10
    sd.rounded_rectangle((12,legend_bottom-55,MAP_SIZE[0]-12,legend_bottom),radius=8,fill='#081423')
    sd.line((30,legend_bottom-17,30+bar,legend_bottom-17),fill='#b4d5e7',width=3)
    for x in (30,30+bar):sd.line((x,legend_bottom-24,x,legend_bottom-13),fill='#b4d5e7',width=2)
    sd.text((35,legend_bottom-51),f'{meters/1000:g} km' if meters>=1000 else f'{meters:g} m',font=font(18),fill='#acc7d8')
    legend='分段起点 S / 分段终点 E' if section else '起点 S / 停车 P' if stopped else '起点 S / 终点 E'
    sd.text((335,legend_bottom-39),legend,font=font(20),fill='#c1d8e6')
    sd.text((885,legend_bottom-39),f"{len(flat)} 点 / {trip['gap_count']} 处断档",font=font(20),fill='#94adc4')
    if tencent:
        sd.text((1257,24),'N',font=font(20),fill='#d9eef8')
        sd.polygon([(1266,46),(1260,61),(1272,61)],fill=cyan)
    # Keep Tencent's full copyright / map-approval strip unobscured.
    mask=Image.new('L',MAP_SIZE,255 if tencent else 0)
    if not tencent:ImageDraw.Draw(mask).rounded_rectangle((0,0,MAP_SIZE[0],MAP_SIZE[1]),radius=15,fill=255)
    image.paste(scene.convert('RGB'),MAP_ORIGIN,mask);draw=ImageDraw.Draw(image)
    if effects:weather_effects.draw_frame_weather(image,weather,(*MAP_ORIGIN,*MAP_SIZE))
    frame=[(52,750),(74,723),(1368,723),(1388,745),(1388,1443),(1368,1462),(74,1462),(52,1440),(52,750)]
    draw.line(frame,fill=cyan,width=2)
    draw.line([(43,787),(43,742),(65,716),(1381,716),(1398,739),(1398,1435)],fill='#1d5485',width=2)
    if tencent:text(65,1484,'道路底图：腾讯地图',18,muted)
    elif available:text(65,1484,'© OpenStreetMap contributors · ODbL · openstreetmap.org/copyright',15,muted)
    else:text(65,1484,'道路服务暂不可用 · 真实采样轨迹',18,orange)
    if weather.get('available'):text(650,1484,'天气模型：Open-Meteo.com / CC BY 4.0',18,muted)
    if details.get('source') in ('tencent','kimi'):text(1190,1484,'地名：腾讯地图',18,muted)
    if stops:
        text(58,1542,'途中停留 · 位置推定',22,ink)
        if details.get('stop_count',len(stops))>len(stops):text(900,1544,f"共 {details['stop_count']} 处 · 标出较长 {len(stops)} 处",18,muted)
        for i,stop in enumerate(stops):
            x=58+(i%2)*672;y=1582+(i//2)*72
            text(x,y,f"停{stop['index']} · {stop['duration_seconds']/60:.1f}分钟",22,orange)
            begin=datetime.fromtimestamp(stop['start_ms']/1000,timezone(timedelta(hours=8))).strftime('%H:%M')
            end_time=datetime.fromtimestamp(stop['end_ms']/1000,timezone(timedelta(hours=8))).strftime('%H:%M')
            text(x+262,y+3,begin+'–'+end_time,18,muted)
            label=stop['label']
            while draw.textlength(label,font=road_font(20))>610:label=label[:-2]+'…'
            draw.text((x,y+32),label,font=road_font(20),fill=muted)
    output=io.BytesIO();image.save(output,format='PNG',optimize=True)
    return output.getvalue()
