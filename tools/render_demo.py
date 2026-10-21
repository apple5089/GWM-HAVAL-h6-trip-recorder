"""Generate a shareable example with synthetic data and no network access."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import sys
import math
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from route_image import render_trip

start=int(datetime(2026,1,1,10,tzinfo=timezone(timedelta(hours=8))).timestamp()*1000)
points=[{'lat':30.+i*.0007+math.sin(i*.9)*.001,'lon':120.+i*.001,'source_ms':start+i*60000} for i in range(13)]
trip={'id':1,'closed':True,'end_reason':'parked_engine_off','start_ms':start,'end_ms':start+720000,'observed_span_seconds':720,'odometer_delta':5,'average_speed_kmh':25,'gap_count':0,'fuel_remaining_l':22,'fuel_gauge_drop_l':1,'estimated_fuel_l_per_100km':13.3,'fuel_estimate_distance_km':45}
details={'title':'云影巡航·城市穿行','start':'合成起点 · 示例街区','end':'合成终点 · 示例广场','end_category':'普通地点','source':'local','weather':{'available':True,'condition':'多云','icon':'partly','is_day':True,'high_c':20,'low_c':10},'weather_effects':False}
output=ROOT/'docs/demo.png'
output.parent.mkdir(exist_ok=True)
output.write_bytes(render_trip(trip,points,details=details))
print('Generated docs/demo.png from synthetic data (no network)')
