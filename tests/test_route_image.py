import io
import stat
from PIL import Image
import pytest
from test_recorder import module,NOW
from test_notifications import trip
from route_image import render_trip,segments,fuel_stat,SIZE

def test_night_map_preserves_copyright_pixels_and_source_image():
    from racing_ui import night_map
    source=Image.new('RGB',(100,100),'white')
    source.putpixel((20,99),(17,89,230))
    themed=night_map(source)
    assert themed.crop((0,55,100,100)).tobytes()==source.crop((0,55,100,100)).tobytes()
    assert themed.getpixel((10,10))!=source.getpixel((10,10))
    assert source.getpixel((10,10))==(255,255,255)

def test_fuel_display_distinguishes_drop_unchanged_refuel_and_missing():
    assert fuel_stat({'fuel_gauge_drop_l':2})==('本次用油 · 油表估算','≈2','L')
    assert fuel_stat({'fuel_gauge_drop_l':0})==('本次用油 · 油表估算','油表未变','')
    assert fuel_stat({'fuel_gauge_drop_l':-5})==('油量回升 · 加油或波动','+5','L')
    assert fuel_stat({})==('本次用油 · 油表估算','暂无数据','')
    assert fuel_stat({'fuel_gauge_drop_l':float('nan')})[-2:] == ('暂无数据','')

def test_segment_breaks_and_invalid_points_do_not_draw_connecting_lines():
    points=[{'lat':30.,'lon':120.,'source_ms':NOW},
            {'lat':30.,'lon':120.001,'source_ms':NOW+60000},
            {'lat':31.,'lon':121.,'source_ms':NOW+3600000},
            {'lat':float('nan'),'lon':121.,'source_ms':NOW+3660000},
            {'lat':31.,'lon':121.001,'source_ms':NOW+3720000}]
    assert [len(g) for g in segments(points)]==[2,1,1]

@pytest.mark.asyncio
async def test_real_geometry_layer_changes_png_and_supports_chinese_road_labels(tmp_path):
    r=module.Recorder(tmp_path);r.settings['road_basemap']=False;t=trip(r.store);points=r.store.points(t['id'])
    roads={'available':True,'features':[{'geometry':[[30.,120.],[30.,120.01]],'tags':{'highway':'primary','name':'测试道路'}}]}
    with_map=render_trip(t,points,roads)
    without_map=render_trip(t,points)
    assert with_map!=without_map
    with Image.open(io.BytesIO(with_map)) as image:assert image.size==SIZE
    await r.close()

@pytest.mark.asyncio
async def test_png_size_no_coordinates_metadata_and_private_file(tmp_path):
    r=module.Recorder(tmp_path);r.settings['road_basemap']=False;t=trip(r.store)
    data=await r.trip_png(t['id'])
    assert data.startswith(b'\x89PNG') and len(data)<500000
    with Image.open(io.BytesIO(data)) as image:
        assert image.size==SIZE and image.format=='PNG'
        assert not image.info
    assert (tmp_path/'images/trip-1.png').read_bytes()==data
    await r.close()

@pytest.mark.asyncio
async def test_uploaded_image_key_survives_retry_without_uploading_twice(tmp_path):
    r=module.Recorder(tmp_path);r.settings['road_basemap']=False;t=trip(r.store)
    r.settings.update(notify=True,notify_images=True);r.vault['feishu_app']={'synthetic':True}
    class Sender:
        def __init__(self):self.uploads=0;self.sends=0
        async def upload_png(self,config,png):self.uploads+=1;return 'img_SYNTHETIC123'
        async def send_image(self,config,key,event_key=None):
            self.sends+=1
            if self.sends==1:raise ValueError('synthetic failure after upload')
    sender=Sender();r.app_sender=sender
    r.queue_trip_image(1,0);r.queue_trip_image(1,0)
    await r.flush_notifications()
    row=r.store.db.execute("SELECT * FROM outbox WHERE kind='trip_image'").fetchone()
    assert row['image_key']=='img_SYNTHETIC123' and row['status']=='pending'
    r.store.db.execute('UPDATE outbox SET next_ms=0');r.store.db.commit()
    await r.flush_notifications();await r.flush_notifications()
    assert sender.uploads==1 and sender.sends==2
    assert r.store.db.execute('SELECT status FROM outbox').fetchone()[0]=='sent'
    await r.close()

@pytest.mark.asyncio
async def test_webhook_mode_cannot_upload_or_change_image_recipient(tmp_path):
    r=module.Recorder(tmp_path);r.settings['road_basemap']=False;trip(r.store)
    r.settings.update(notify=True,notify_images=True);r.vault['webhook']='https://open.feishu.cn/open-apis/bot/v2/hook/synthetic'
    result=await r.action('send_trip_image',{'trip_id':1})
    assert result.get('error') and r.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
    await r.close()

@pytest.mark.asyncio
async def test_image_api_is_png_and_unknown_trip_is_404(tmp_path):
    from aiohttp.test_utils import TestClient,TestServer
    r=module.Recorder(tmp_path);r.settings['road_basemap']=False;trip(r.store)
    async with TestClient(TestServer(module.build_app(r)),headers={'Host':module.urlsplit(module.ORIGIN).netloc}) as client:
        response=await client.get('/api/trip-image.png?trip=1')
        assert response.status==200 and response.headers['Content-Type']=='image/png'
        assert (await response.read()).startswith(b'\x89PNG')
        assert (await client.get('/api/trip-image.png?trip=999')).status==404
        assert (await client.get('/api/trip-image.png?trip=../secrets')).status==400


@pytest.mark.asyncio
async def test_permission_failure_pauses_only_images_until_explicit_retry(tmp_path):
    from feishu_app import FeishuDeliveryError
    r=module.Recorder(tmp_path);r.settings['road_basemap']=False;trip(r.store)
    r.settings.update(notify=True,notify_images=True);r.vault['feishu_app']={'synthetic':True}
    class Sender:
        token=None
        allowed=False
        uploads=0
        async def upload_png(self,config,png):
            self.uploads+=1
            if not self.allowed:raise FeishuDeliveryError(99991672)
            return 'img_SYNTHETIC123'
        async def send_image(self,config,key,event_key=None):pass
    sender=Sender();r.app_sender=sender;r.queue_trip_image(1,0)
    await r.flush_notifications();await r.flush_notifications()
    assert sender.uploads==1 and r.settings['image_permission_required']
    assert r.store.db.execute('SELECT status FROM outbox').fetchone()[0]=='permission_required'
    sender.allowed=True
    await r.action('resume_trip_images',{})
    await r.flush_notifications()
    assert sender.uploads==2 and not r.settings['image_permission_required']
    assert r.store.db.execute('SELECT status FROM outbox').fetchone()[0]=='sent'
    await r.close()
