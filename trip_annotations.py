"""Observed dwell clusters and bounded detail pages. No route interpolation."""
import math
from store import distance

def valid(p):
    return all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('lat','lon','source_ms')) and -85<=p['lat']<=85 and -180<=p['lon']<=180 and (p['lat'],p['lon'])!=(0,0)

def continuous(a,b):
    return not b.get('break_before') and 0<b['source_ms']-a['source_ms']<=180000

def detect_stops(points):
    result=[];group=[];previous=None
    for p in points:
        if not valid(p):group=[];previous=None;continue
        if previous is not None and not continuous(previous,p):group=[]
        if group:
            anchor=group[0]
            spatial=distance((anchor['lat'],anchor['lon']),(p['lat'],p['lon']))<=35
            odos=[v.get('odometer_km') for v in (anchor,p)]
            stable_odo=not all(type(v) in (int,float) for v in odos) or abs(odos[0]-odos[1])<=.1
            if not spatial or not stable_odo:
                duration=(group[-1]['source_ms']-anchor['source_ms'])/1000
                # A subsequent continuous movement is required: never count final parking.
                if duration>=180 and len(group)>=4:
                    result.append({'index':len(result)+1,'lat':anchor['lat'],'lon':anchor['lon'],
                        'start_ms':anchor['source_ms'],'end_ms':group[-1]['source_ms'],'duration_seconds':duration,'label':'附近地点暂未取得'})
                group=[]
        if not group:group=[p]
        else:group.append(p)
        previous=p
    return result

def image_plan(trip,points):
    observed=[p for p in points if p['source_ms']<=trip['end_ms']]
    stops=detect_stops(observed)
    # Preserve chronology while displaying only the four longest stops in one image.
    shown=sorted(sorted(stops,key=lambda p:-p['duration_seconds'])[:4],key=lambda p:p['start_ms'])
    pages=[];group=[];meters=0;travel=0;previous=None
    def finish():
        if len(group)>=2:
            pages.append({'number':len(pages)+1,'points':list(group),'start_ms':group[0]['source_ms'],'end_ms':group[-1]['source_ms']})
    for p in observed:
        if not valid(p):finish();group=[];meters=0;previous=None;continue
        if previous is not None and not continuous(previous,p):finish();group=[];meters=0
        if group:
            step=distance((group[-1]['lat'],group[-1]['lon']),(p['lat'],p['lon']))
            meters+=step;travel+=step
        group.append(p)
        if meters>=5000:
            finish();group=[p];meters=0
        previous=p
    finish()
    if len(pages)>500:
        stride=math.ceil(len(pages)/500);compact=[]
        for offset in range(0,len(pages),stride):
            merged={p['source_ms']:p for page in pages[offset:offset+stride] for p in page['points']}
            selected=sorted(merged.values(),key=lambda p:p['source_ms'])
            compact.append({'number':len(compact)+1,'points':selected,'start_ms':selected[0]['source_ms'],'end_ms':selected[-1]['source_ms']})
        pages=compact
    km=trip.get('odometer_delta')
    is_long=(type(km) in (int,float) and km>=50) or travel>=50000
    if not is_long:pages=[]
    chosen=[]
    if pages:
        middle=(len(pages)-1)//2
        if stops:
            longest=max(stops,key=lambda p:p['duration_seconds'])
            middle=next((i for i,p in enumerate(pages) if p['start_ms']<=longest['end_ms']<=p['end_ms']),middle)
        chosen=sorted({1,middle+1,len(pages)})
    return {'points':observed,'stops':stops,'shown_stops':shown,'pages':pages,'auto_pages':chosen}
