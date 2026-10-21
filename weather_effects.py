"""Local decorative weather, confined to quiet map edges and the outer frame."""
import hashlib
import math
import random
from PIL import Image, ImageChops, ImageDraw, ImageFilter

KINDS={'sun','partly','cloud','rain','snow','fog','storm'}

def enabled(weather):
    return bool(weather and weather.get('available') and weather.get('icon') in KINDS)

def quiet_mask(scene,lines):
    """Protect raster ink/colour, route corridor, endpoint labels and attribution."""
    width,height=scene.size
    red,green,blue=scene.convert('RGB').split()
    minimum=ImageChops.darker(ImageChops.darker(red,green),blue)
    maximum=ImageChops.lighter(ImageChops.lighter(red,green),blue)
    light=minimum.point(lambda v:255 if v>=228 else 0)
    neutral=ImageChops.subtract(maximum,minimum).point(lambda v:255 if v<=42 else 0)
    safe=ImageChops.multiply(light,neutral).filter(ImageFilter.MinFilter(9))
    edge=Image.new('L',scene.size,0);draw=ImageDraw.Draw(edge)
    draw.rectangle((0,115,148,height-45),fill=255)
    draw.rectangle((width-148,115,width,height-45),fill=255)
    draw.rectangle((0,height-125,width,height-45),fill=180)
    edge=edge.filter(ImageFilter.GaussianBlur(9))
    safe=ImageChops.multiply(safe,edge);draw=ImageDraw.Draw(safe)
    for line in lines:
        if len(line)>1:draw.line(line,fill=0,width=100,joint='curve')
        for x,y in line:draw.ellipse((x-50,y-50,x+50,y+50),fill=0)
    flat=[point for line in lines for point in line]
    if flat:
        for x,y in (flat[0],flat[-1]):draw.ellipse((x-180,y-180,x+180,y+180),fill=0)
    draw.rectangle((0,0,width,115),fill=0)
    draw.rectangle((0,height-45,width,height),fill=0)
    draw.rectangle((width-115,0,width,150),fill=0)
    return safe

def apply_map_weather(scene,weather,lines,seed=0):
    if not enabled(weather):return scene
    kind=weather['icon'];width,height=scene.size
    number=int.from_bytes(hashlib.sha256(f'{seed}:{kind}'.encode()).digest()[:8],'big')
    rng=random.Random(number)
    layer=Image.new('RGBA',scene.size,(0,0,0,0));draw=ImageDraw.Draw(layer)
    if kind in ('cloud','partly','fog'):
        for side in (0,1):
            for i in range(3):
                x=-95 if side==0 else width-165;y=140+i*165+side*55
                draw.ellipse((x,y,x+260,y+90),fill=(100,146,179,15),outline=(83,135,173,43),width=2)
                draw.arc((x+45,y-24,x+165,y+68),175,350,fill=(83,135,173,43),width=2)
        for i in range(5):
            y=height-112+i*13
            draw.line((70+i*135,y,190+i*135,y),fill=(90,135,168,22),width=2)
    if kind in ('sun','partly'):
        colour=(255,193,89) if weather.get('is_day',True) else (117,163,232)
        halo=Image.new('RGBA',scene.size,(0,0,0,0));hd=ImageDraw.Draw(halo)
        for radius in range(160,20,-12):
            hd.ellipse((-radius,120-radius,radius,120+radius),fill=(*colour,round(28*(1-radius/190))))
        layer=Image.alpha_composite(layer,halo.filter(ImageFilter.GaussianBlur(12)));draw=ImageDraw.Draw(layer)
    if kind in ('rain','storm','snow'):
        for i in range(65 if kind!='snow' else 40):
            x=rng.randint(12,140) if i%2==0 else rng.randint(width-140,width-12)
            y=rng.randint(122,height-65)
            if kind=='snow':
                draw.line((x-3,y,x+3,y),fill=(75,136,181,85),width=1)
                draw.line((x,y-3,x,y+3),fill=(75,136,181,85),width=1)
            else:
                length=rng.randint(9,22);draw.line((x,y,x-5,y+length),fill=(54,122,174,75),width=1)
    alpha=ImageChops.multiply(layer.getchannel('A'),quiet_mask(scene,lines));layer.putalpha(alpha)
    return Image.alpha_composite(scene.convert('RGBA'),layer)

def draw_frame_weather(image,weather,box):
    """Visible weather accents live completely outside the map rectangle."""
    if not enabled(weather):return
    x,y,width,height=box;kind=weather['icon'];draw=ImageDraw.Draw(image)
    for cx,cy in ((x-33,y+165),(x+width+33,y+height-175)):
        colour='#718da5'
        if kind in ('sun','partly') and weather.get('is_day',True):
            draw.ellipse((cx-8,cy-8,cx+8,cy+8),outline='#bc9a60',width=2)
            for i in range(8):
                a=i*math.pi/4;draw.line((cx+12*math.cos(a),cy+12*math.sin(a),cx+17*math.cos(a),cy+17*math.sin(a)),fill='#806f51',width=1)
        elif kind=='sun':
            draw.arc((cx-12,cy-12,cx+12,cy+12),35,300,fill='#809cc7',width=2)
        else:
            draw.arc((cx-16,cy-6,cx+3,cy+11),80,260,fill=colour,width=2)
            draw.arc((cx-8,cy-15,cx+13,cy+7),180,355,fill=colour,width=2)
            draw.arc((cx+3,cy-5,cx+17,cy+11),270,90,fill=colour,width=2)
            draw.line((cx-7,cy+11,cx+10,cy+11),fill=colour,width=2)
            if kind in ('rain','storm'):
                for dx in (-9,0,9):draw.line((cx+dx,cy+17,cx+dx-3,cy+24),fill='#537e9b',width=2)
            elif kind=='snow':
                for dx in (-9,9):
                    draw.line((cx+dx-3,cy+20,cx+dx+3,cy+20),fill=colour,width=1)
                    draw.line((cx+dx,cy+17,cx+dx,cy+23),fill=colour,width=1)
            else:
                draw.line((cx-14,cy+19,cx+9,cy+19),fill='#405d76',width=1)
                draw.line((cx-7,cy+24,cx+15,cy+24),fill='#405d76',width=1)
