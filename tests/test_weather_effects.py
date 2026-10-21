import pytest
from PIL import Image,ImageDraw,ImageChops
from weather_effects import apply_map_weather,draw_frame_weather

@pytest.mark.parametrize('kind',['cloud','rain','sun','snow','fog','storm','partly'])
def test_weather_changes_quiet_edges_but_preserves_route_labels_copyright_and_centre(kind):
    base=Image.new('RGB',(1312,720),'#f2f3f4');draw=ImageDraw.Draw(base)
    draw.rectangle((0,680,1311,719),fill='#285078')
    draw.rectangle((20,180,90,210),fill='#293f55')  # raster place name / symbol
    line=[(28,300),(105,390),(250,450)]
    weather={'available':True,'icon':kind,'is_day':True}
    result=apply_map_weather(base,weather,[line],seed=4).convert('RGB')
    assert ImageChops.difference(base,result).getbbox() is not None
    for box in [(0,0,1312,115),(0,675,1312,720),(16,176,95,215),(10,285,48,315),(400,220,900,500)]:
        assert result.crop(box).tobytes()==base.crop(box).tobytes()
    assert apply_map_weather(base,weather,[line],4).tobytes()==apply_map_weather(base,weather,[line],4).tobytes()

def test_unavailable_weather_is_not_invented_and_frame_accents_never_change_map():
    base=Image.new('RGB',(1440,1410),'#051020')
    for weather in ({},{'available':False,'icon':'rain'},{'available':True,'icon':'unknown'}):
        assert apply_map_weather(base,weather,[]).tobytes()==base.tobytes()
    result=base.copy();draw_frame_weather(result,{'available':True,'icon':'rain'},(64,620,1312,720))
    assert ImageChops.difference(base,result).getbbox() is not None
    assert result.crop((64,620,1376,1340)).tobytes()==base.crop((64,620,1376,1340)).tobytes()
