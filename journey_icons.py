"""Small vector pictograms drawn locally; no external assets or requests."""
import math

def trophy(draw,x,y,size=44,color='#ffcb70'):
    s=size/44
    def box(a,b,c,d):return (x+a*s,y+b*s,x+c*s,y+d*s)
    draw.polygon([(x+10*s,y+3*s),(x+34*s,y+3*s),(x+30*s,y+23*s),(x+22*s,y+29*s),(x+14*s,y+23*s)],fill=color)
    draw.arc(box(1,3,22,26),70,270,fill=color,width=max(2,round(3*s)))
    draw.arc(box(22,3,43,26),270,110,fill=color,width=max(2,round(3*s)))
    draw.line((x+22*s,y+27*s,x+22*s,y+36*s),fill=color,width=max(2,round(4*s)))
    draw.rounded_rectangle(box(11,35,33,40),radius=2,fill=color)

def destination(draw,x,y,school=False,color='#65f4bc'):
    if school:
        draw.polygon([(x,y+10),(x+23,y),(x+46,y+10)],outline=color,width=2)
        draw.rectangle((x+5,y+12,x+41,y+37),outline=color,width=2)
        draw.rectangle((x+19,y+25,x+27,y+37),outline=color,width=2)
        for dx in (11,30):draw.rectangle((x+dx,y+17,x+dx+5,y+22),fill=color)
        draw.line((x,y+39,x+46,y+39),fill=color,width=2)
    else:
        draw.ellipse((x+7,y,x+39,y+32),outline=color,width=3)
        draw.polygon([(x+12,y+25),(x+23,y+43),(x+34,y+25)],fill=color)
        draw.ellipse((x+18,y+11,x+28,y+21),fill=color)

def weather(draw,cx,cy,kind,is_day=True):
    sun='#ffcc72';cloud='#b7d3e7';water='#59d9ff'
    if kind in ('sun','partly'):
        sx,sy=(cx,cy) if kind=='sun' else (cx-13,cy-12)
        if is_day:
            draw.ellipse((sx-13,sy-13,sx+13,sy+13),fill=sun)
            for i in range(8):
                a=i*math.pi/4
                draw.line((sx+19*math.cos(a),sy+19*math.sin(a),sx+25*math.cos(a),sy+25*math.sin(a)),fill=sun,width=3)
        else:
            draw.ellipse((sx-18,sy-18,sx+18,sy+18),fill='#c3d6ff')
            draw.ellipse((sx-8,sy-24,sx+24,sy+8),fill='#0d1e31')
    if kind!='sun':
        draw.ellipse((cx-25,cy-8,cx+1,cy+16),fill=cloud)
        draw.ellipse((cx-13,cy-23,cx+17,cy+12),fill=cloud)
        draw.ellipse((cx+5,cy-10,cx+30,cy+16),fill=cloud)
        draw.rectangle((cx-12,cy-1,cx+16,cy+16),fill=cloud)
    if kind in ('rain','storm'):
        for dx in (-16,1,18):draw.line((cx+dx,cy+23,cx+dx-5,cy+33),fill=water,width=3)
    if kind=='storm':draw.polygon([(cx+2,cy+9),(cx-7,cy+25),(cx+2,cy+25),(cx-2,cy+39),(cx+15,cy+19),(cx+5,cy+19)],fill=sun)
    if kind=='fog':
        for dy in (23,31):draw.line((cx-25,cy+dy,cx+25,cy+dy),fill=cloud,width=3)
    if kind=='snow':
        for dx in (-15,5,23):
            for angle in (0,math.pi/3,2*math.pi/3):
                vx,vy=4*math.cos(angle),4*math.sin(angle);draw.line((cx+dx-vx,cy+27-vy,cx+dx+vx,cy+27+vy),fill=water,width=2)
