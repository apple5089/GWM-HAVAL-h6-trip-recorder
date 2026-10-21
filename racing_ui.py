"""Local vector artwork for the neon racing results card."""
import math
from pathlib import Path
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, ImageChops

CYAN='#00eaff'
PINK='#ff329f'
GREEN='#25ffc3'
FONT=Path(__file__).parent/'static/fonts/H6DisplaySC.ttf'

@lru_cache(maxsize=24)
def display_font(size):
    return ImageFont.truetype(str(FONT),size)

def lettering(image,xy,value,size,width,color='#f2f9ff',italic=True):
    """Fit the whole title; shear artwork rather than truncating the name."""
    face=display_font(size)
    bounds=face.getbbox(str(value))
    layer=Image.new('RGBA',(math.ceil(face.getlength(str(value)))+30,size*2))
    d=ImageDraw.Draw(layer)
    d.text((10,8-bounds[1]),str(value),font=face,fill=color)
    layer=layer.crop(layer.getbbox())
    if italic:
        w,h=layer.size
        layer=layer.transform((w+int(h*.22)+2,h),Image.Transform.AFFINE,(1,.22,-h*.22,0,1,0),Image.Resampling.BICUBIC)
    if layer.width>width:layer=layer.resize((width,layer.height),Image.Resampling.LANCZOS)
    shadow=Image.new('RGBA',layer.size)
    shadow.paste('#009ddd',(0,0,*layer.size),layer.getchannel('A'))
    image.paste(shadow,(int(xy[0])+1,int(xy[1])+2),shadow)
    image.paste(layer,tuple(map(int,xy)),layer)
    return layer.width,layer.height

def panel(image,rect,accent=CYAN,fill='#071827',cut=15):
    x,y,r,b=map(int,rect)
    pts=[(x+cut,y),(r-cut,y),(r,y+cut),(r,b-cut),(r-cut,b),(x+cut,b),(x,b-cut),(x,y+cut)]
    d=ImageDraw.Draw(image)
    d.polygon(pts,fill=fill)
    # Diagonal inlays give each panel a brushed carbon texture.
    for start in range(x+20,r-60,32):
        d.line((start,y+3,min(start+55,r-5),min(y+58,b-5)),fill='#0a1c2a',width=2)
    glow=Image.new('RGBA',image.size)
    gd=ImageDraw.Draw(glow);gd.line(pts+[pts[0]],fill=accent,width=6)
    glow=glow.filter(ImageFilter.GaussianBlur(5))
    image.paste(glow,(0,0),glow)
    d=ImageDraw.Draw(image);d.line(pts+[pts[0]],fill='#22627e',width=1)
    d.line([(x,y+cut+28),(x,y+cut),(x+cut,y),(x+100,y)],fill=accent,width=3)
    d.line([(r-105,b),(r-cut,b),(r,b-cut),(r,b-cut-20)],fill=accent,width=2)
    d.line((x+18,b-4,x+88,b-4),fill=accent,width=2)

def background(size):
    image=Image.new('RGB',size,'#030914');d=ImageDraw.Draw(image)
    for y in range(size[1]):
        tone=round(10*max(0,1-y/650))
        d.line((0,y,size[0],y),fill=(3,9+tone//2,17+tone))
    for x in range(-size[1],size[0],16):
        d.line((x,0,x+size[1],size[1]),fill='#08131e',width=2)
        d.line((x+5,0,x+size[1]+5,size[1]),fill='#060e18',width=2)
    glow=Image.new('RGBA',size);g=ImageDraw.Draw(glow)
    for pts,color in [([(0,254),(370,254),(955,128),(1440,28)],PINK), ([(0,76),(42,16),(905,16)],CYAN), ([(1440,250),(1412,226),(1120,226)],CYAN)]:
        g.line(pts,fill=color,width=10)
    glow=glow.filter(ImageFilter.GaussianBlur(9));image.paste(glow,(0,0),glow)
    d=ImageDraw.Draw(image)
    d.line([(0,254),(370,254),(955,128),(1440,28)],fill=PINK,width=2)
    return image

def checks(draw,x,y,cols=5,rows=3,size=16,color=CYAN):
    for row in range(rows):
        for col in range(cols):
            if (row+col)%2==0:
                left=x+col*size-row*6;top=y+row*size
                draw.polygon([(left+6,top),(left+size+6,top),(left+size,top+size),(left,top+size)],fill=color)

def icon(draw,x,y,kind,color=CYAN):
    w=4
    if kind=='road':
        draw.line((x+5,y+54,x+22,y+4),fill=color,width=w)
        draw.line((x+48,y+54,x+39,y+4),fill=color,width=w)
        for a,b in ((5,16),(24,35),(44,54)):draw.line((x+29,y+a,x+27,y+b),fill=color,width=3)
    elif kind=='clock':
        draw.ellipse((x+3,y+9,x+51,y+57),outline=color,width=w)
        draw.line((x+27,y+15,x+27,y+33,x+37,y+23),fill=color,width=w)
        draw.line((x+21,y+1,x+34,y+1),fill=color,width=w)
        draw.line((x+27,y+1,x+27,y+8),fill=color,width=w)
    elif kind=='speed':
        draw.arc((x+1,y+5,x+55,y+59),155,385,fill=color,width=w)
        for i in range(5):
            a=math.pi+(i*math.pi/4)
            draw.line((x+28+18*math.cos(a),y+32+18*math.sin(a),x+28+23*math.cos(a),y+32+23*math.sin(a)),fill=color,width=3)
        draw.line((x+28,y+33,x+41,y+17),fill=color,width=w)
        draw.ellipse((x+24,y+29,x+32,y+37),fill=color)
    else:
        draw.rounded_rectangle((x+6,y+5,x+34,y+54),radius=4,outline=color,width=w)
        draw.rectangle((x+12,y+12,x+28,y+27),fill=color)
        draw.line((x+2,y+55,x+39,y+55),fill=color,width=w)
        draw.line((x+35,y+12,x+47,y+24,x+47,y+43,x+42,y+46,x+39,y+41,x+39,y+32,x+34,y+29),fill=color,width=3)

def skyline(draw,rect):
    x,y,r,b=rect
    for i in range(9):
        left=x+i*24;top=b-20-(i*17)%65
        draw.rectangle((left,top,left+18,b),fill='#07504d',outline='#087369')
        for dy in range(top+7,b-6,10):draw.line((left+5,dy,left+10,dy),fill='#099d86',width=2)
    draw.line((x,b,r,b),fill=GREEN,width=2)

def night_map(source):
    """Tone cached road artwork locally; preserve all geometry and attribution."""
    source=source.convert('RGB')
    # Tencent's logo, legal text and approval strip retain their original pixels.
    legal_height=45
    content=source.crop((0,0,source.width,source.height-legal_height))
    gray=ImageOps.grayscale(content)
    themed=ImageOps.colorize(gray,'#d3e5f3','#11263b',mid='#819db7',midpoint=180)
    hue,saturation,_=content.convert('HSV').split()
    saturated=saturation.point(lambda value:255 if value>=28 else 0)
    for low,high,dark,light in ((35,115,'#66a68c','#064133'),(116,185,'#82c6e9','#073754'),(20,34,'#c1ba82','#5c6037')):
        mask=ImageChops.multiply(hue.point(lambda value:255 if low<=value<=high else 0),saturated)
        themed.paste(ImageOps.colorize(gray,dark,light),(0,0),mask)
    source.paste(themed,(0,0))
    return source
