"""Private NAS H6 recorder. No control routes and no secrets in application logs."""
import asyncio
import base64
import csv
import dataclasses
import hashlib
import hmac
import json
import io
import logging
import os
from pathlib import Path
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from aiohttp import web, ClientSession, ClientTimeout
from cryptography.fernet import Fernet
import vehicle_client as vehicle_backend
from read_guard import ReadOnlyTransport
from store import Store, LABELS
from feishu_app import AppSender, FeishuDeliveryError
from alerts import AlertMonitor, APERTURES
from route_image import render_trip
from tencent_map import TencentMapCache,gcj02
from trip_details import TripDetails
from trip_annotations import image_plan as build_image_plan

os.umask(0o077)
DATA = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent / "data")))
ORIGIN = os.environ.get("PUBLIC_ORIGIN", "http://localhost:18770").rstrip("/")
ROOT = Path(__file__).resolve().parent
IMAGE_VERSION='v11'


class Recorder:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        private = self.directory / ".secrets"
        private.mkdir(mode=0o700, exist_ok=True)
        private.chmod(0o700)
        keyfile = private / "master.key"
        if not keyfile.exists():
            keyfile.write_bytes(Fernet.generate_key())
        keyfile.chmod(0o600)
        self.cipher = Fernet(keyfile.read_bytes())
        self.vault_file = private / "account.enc"
        self.vault = {}
        self.vault_error = False
        if self.vault_file.exists():
            try:
                self.vault = json.loads(self.cipher.decrypt(self.vault_file.read_bytes()))
            except Exception:
                self.vault_error = True
        self.settings_file = self.directory / "settings.json"
        self.settings = {"recording":False,"interval":60,"notify":False,"alerts":True,
                         "tire_low_kpa":200,"tire_high_kpa":320,"tire_high_c":85,"fuel_low_l":8,"parked_apertures":True,
                         "aperture_baseline":{},"aperture_baseline_vehicle":None,"aperture_baseline_source_ms":None,"aperture_baseline_confirmed_ms":None}
        self.settings['parking_profiles'] = {}
        self.settings['notify_images'] = True
        self.settings['image_permission_required'] = False
        self.settings['road_basemap'] = True
        self.settings.update(tencent_enabled=True,kimi_enabled=True,weather_enabled=True,weather_effects=True,tencent_coord_mode='gps',kimi_model='kimi-k2.6')
        if self.settings_file.exists():
            self.settings.update(json.loads(self.settings_file.read_text()))
        self.store = Store(self.directory / "tracks.sqlite3")
        self.road_cache = TencentMapCache(self.directory/'tencent-map-cache')
        self.trip_details = TripDetails(self.directory/'trip-details')
        self.image_lock=asyncio.Lock()
        self.alert_monitor = AlertMonitor(self.store)
        self.client = self.transport = self.credentials = self.pending = None
        self.vehicles = ()
        self.latest = None
        self.latest_vehicle_key = None
        self.stage = "awaiting_login"
        self.message = "请在此页面完成长城账号短信登录。" if vehicle_backend.available() else "记录器已就绪；实时采集需先接入车辆客户端，见 docs/VEHICLE_CLIENT.md。"
        self.failure = None
        self.busy = False
        self.last_poll = 0
        self.last_sms = 0
        self.error_streak = 0
        self.backoff_until = 0
        self.stop = asyncio.Event()
        self.task = None
        self.delivery_task = None
        self.app_sender = AppSender()
        self.notification_status = "已配置" if self.notification_mode() != "none" else "未配置"

    def notification_mode(self):
        return "webhook" if self.vault.get("webhook") else "existing_app" if self.vault.get("feishu_app") else "none"

    def save_vault(self):
        if self.pending and self.credentials:
            state = dataclasses.asdict(self.pending)
            if state.get("verification_requested_at"):
                state["verification_requested_at"] = state["verification_requested_at"].isoformat()
            self.vault.update({"phone":self.credentials.phone,"device_id":self.credentials.device_id,"state":state})
        temporary = self.vault_file.with_suffix(".tmp")
        temporary.write_bytes(self.cipher.encrypt(json.dumps(self.vault).encode()))
        temporary.chmod(0o600)
        temporary.replace(self.vault_file)

    def save_settings(self):
        temporary = self.settings_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.settings), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.settings_file)

    async def create(self):
        if self.client is None:
            inner = vehicle_backend.ChinaAiohttpTransport.create_owned()
            self.transport = ReadOnlyTransport(inner, vehicle_backend._ChinaTransportRequest, vehicle_backend.GwmRoutePolicyError)
            self.client = vehicle_backend.DiagnosticChinaClient(vehicle_backend.ChinaClientConfig(), transport=self.transport)

    async def startup(self):
        if self.vault_error:
            self.message = "登录文件无法解密，请检查数据卷和密钥。未尝试云端登录。"
        elif self.vault.get("state"):
            try:
                await self.create()
                self.credentials = vehicle_backend.ChinaCredentials(self.vault["phone"], self.vault["device_id"])
                raw = dict(self.vault["state"])
                if raw.get("verification_requested_at"):
                    raw["verification_requested_at"] = datetime.fromisoformat(raw["verification_requested_at"])
                self.pending = vehicle_backend.ChinaAuthState(**raw)
                # Restart never sends an SMS automatically.
                await self.accept(await self.client.authenticate(self.credentials, state=self.pending, allow_sms_login=False))
            except Exception as error:
                self.failure = type(error).__name__
                self.message = "会话恢复未完成，请在页面重新验证或重试服务初始化。"
        self.task = asyncio.create_task(self.run())
        self.delivery_task = asyncio.create_task(self.run_delivery())

    async def accept(self, result):
        self.pending = result.state
        self.failure = None
        if type(result) is vehicle_backend.ChinaAuthenticated:
            self.vehicles = await self.client.acquire_vehicles()
            self.stage = "authenticated"
            self.message = f"账号已接通，发现 {len(self.vehicles)} 台车辆。"
            selected = self.vault.get("selected")
            if len(self.vehicles) == 1 and not selected:
                self.vault["selected"] = self.vehicles[0].identifier.value
            self.save_vault()
        elif type(result) is vehicle_backend.ChinaVerificationRequired:
            self.stage = "awaiting_code"
            self.message = "验证码未通过，请重新输入。" if result.code_rejected else "验证码已请求，请查看短信。"
        elif type(result) is vehicle_backend.ChinaInitializationRequired:
            self.stage = "initialization_required"
            self.failure = ";".join(result.failures)
            self.message = "账号验证通过，车辆服务初始化未完成，可重试。"
            self.save_vault()
        elif type(result) is vehicle_backend.ChinaRiskControlRequired:
            self.stage = "risk_control"
            self.message = "请在官方 App 完成额外验证后重试。"

    def selected(self):
        return next((v for v in self.vehicles if v.identifier.value == self.vault.get("selected")), None)

    async def poll(self):
        vehicle = self.selected()
        if not vehicle or not self.client or not self.client.authenticated:
            raise ValueError("not_authenticated")
        self.last_poll = time.monotonic()
        try:
            status = await self.client.get_last_status(vehicle.identifier)
        except vehicle_backend.GwmAuthenticationError:
            result = await self.client.authenticate(self.credentials, state=self.pending, allow_sms_login=False)
            await self.accept(result)
            if type(result) is not vehicle_backend.ChinaAuthenticated:
                if self.settings['alerts'] and self.notification_mode() != 'none':
                    now = round(time.time()*1000)
                    self.store.enqueue('auth-required:'+str(now//86400000),'alert',
                        'H6 采集需要重新验证：车辆云端登录未能自动续期。请打开 NAS 页面完成验证，期间可能缺失轨迹。',now)
                return
            status = await self.client.get_last_status(vehicle.identifier)
        now = round(time.time()*1000)
        stamp = status.acquisition_time_ms or status.update_time_ms
        signals = {s.code:{"value":s.value,"unit":s.unit} for s in status.items}
        lat, lon = status.latitude, status.longitude
        valid = all(type(v) in (int,float) and __import__("math").isfinite(v) for v in (lat,lon))
        valid = valid and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat,lon) != (0,0)
        self.latest = {"latitude":lat if valid else None,"longitude":lon if valid else None,
            "has_location":bool(valid),"source_ms":stamp,"received_ms":now,
            "age_seconds":round((now-stamp)/1000) if stamp else None,
            "metrics":[{"code":code,"label":LABELS.get(code, "状态 "+code),"value":s["value"],"unit":s["unit"]} for code,s in signals.items()]}
        vehicle_key = hashlib.sha256(vehicle.identifier.value.encode()).hexdigest()[:20]
        self.latest_vehicle_key = vehicle_key
        if self.settings['alerts'] and self.notification_mode() != 'none':
            self.alert_monitor.observe(vehicle_key,stamp,now,signals,self.settings,lat,lon)
            self.alert_monitor.cloud(True,now)
        if self.settings["recording"]:
            result = self.store.ingest(vehicle_key,stamp,now,lat,lon,signals,self.settings['parking_profiles'].get(vehicle_key))
            self.latest["storage"] = result["reason"]
            for trip_id in result.get("completed",[]):
                await self.notify_trip(trip_id)
        self.failure = None
        self.error_streak = 0
        self.backoff_until = 0
        self.message = "已读取车辆状态；陈旧定位不会写入轨迹。"

    async def action(self, name, payload):
        if self.busy:
            return {"error":"正在处理上一项请求。"}
        self.busy = True
        try:
            if name == "sms":
                if not vehicle_backend.available():
                    return {"error":"尚未接入车辆客户端，请按 docs/VEHICLE_CLIENT.md 配置后重启。"}
                phone = payload.get("phone", "")
                if not isinstance(phone,str) or not re.fullmatch(r"1[3-9]\d{9}",phone):
                    return {"error":"请输入有效的 11 位手机号。"}
                if self.credentials and self.credentials.phone != phone:
                    return {"error":"当前已绑定账号。如需更换，请先停止记录并联系管理员清理账号文件。"}
                if time.monotonic()-self.last_sms < 60:
                    return {"error":"请求验证码请至少间隔 60 秒。"}
                await self.create()
                if not self.credentials:
                    self.credentials = vehicle_backend.ChinaCredentials(phone,secrets.token_hex(16))
                self.last_sms = time.monotonic()
                await self.accept(await self.client.authenticate(self.credentials,state=self.pending))
            elif name == "login":
                code = payload.get("code", "")
                if not self.credentials or self.pending is None:
                    return {"error":"请先请求验证码。"}
                if not isinstance(code,str) or not re.fullmatch(r"\d{4,8}",code):
                    return {"error":"验证码格式不正确。"}
                await self.accept(await self.client.authenticate(self.credentials,state=self.pending,verification_code=code))
            elif name == "retry":
                if not self.credentials or not self.pending:
                    return {"error":"请先完成登录。"}
                await self.accept(await self.client.authenticate(self.credentials,state=self.pending,allow_sms_login=False))
            elif name == "read":
                if time.monotonic()-self.last_poll < 10:
                    return {"error":"读取请至少间隔 10 秒。"}
                await self.poll()
            elif name == "settings":
                interval = payload.get("interval",self.settings["interval"])
                if type(interval) is not int or not 60 <= interval <= 3600:
                    return {"error":"采样间隔应为 60–3600 秒。"}
                index = payload.get("index")
                if index is not None:
                    if type(index) is not int or not 0 <= index < len(self.vehicles):
                        return {"error":"请选择本人车辆。"}
                    self.vault["selected"] = self.vehicles[index].identifier.value
                for key in ("recording","notify","alerts","parked_apertures","notify_images","road_basemap"):
                    if key in payload and type(payload[key]) is not bool:
                        return {"error":"设置格式错误。"}
                limits = {'tire_low_kpa':(100,280),'tire_high_kpa':(290,450),'tire_high_c':(60,110),'fuel_low_l':(1,30)}
                for key,(low,high) in limits.items():
                    if key in payload and (type(payload[key]) is not int or not low<=payload[key]<=high):
                        return {"error":"异常提醒阈值超出可设置范围。"}
                if payload.get("recording") and not self.selected():
                    return {"error":"请先登录并选择车辆。"}
                hook = payload.get("webhook", "")
                secret = payload.get("sign_secret", "")
                if hook:
                    parsed = urlsplit(hook)
                    if parsed.scheme != "https" or parsed.hostname != "open.feishu.cn" or parsed.port not in (None,443) or parsed.username or parsed.password or not re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9-]+",parsed.path) or parsed.query or parsed.fragment:
                        return {"error":"请输入飞书官方机器人 HTTPS Webhook 地址。"}
                    self.vault["webhook"] = hook
                if secret:
                    if not isinstance(secret,str) or len(secret)>512:
                        return {"error":"签名密钥格式错误。"}
                    self.vault["sign_secret"] = secret
                self.settings["interval"] = interval
                for key in ("recording","notify","alerts","parked_apertures","notify_images","road_basemap",*limits):
                    if key in payload:
                        self.settings[key] = payload[key]
                self.save_vault()
                self.save_settings()
                self.message = "设置已保存。记录仅从启用后累积，不补造过去轨迹。"
            elif name == 'feishu_app':
                config=dict(self.vault.get('feishu_app',{}))
                for key in ('app_id','app_secret','receive_id'):
                    value=payload.get(key,'')
                    if not isinstance(value,str) or len(value)>512:
                        return {'error':'请检查飞书应用配置。'}
                    if value:config[key]=value
                config['receive_id_type']='chat_id'
                try:self.app_sender.validate(config)
                except FeishuDeliveryError:return {'error':'请填写 App ID、App Secret 和 oc_ 开头的群聊 chat_id。'}
                self.vault['feishu_app']=config
                self.app_sender.token=None
                self.save_vault()
                self.message='飞书应用配置已加密保存。开启通知后，可点击通道测试验证。'
            elif name == 'trip_services':
                enabled={key:payload.get(key,self.settings[key]) for key in ('tencent_enabled','kimi_enabled','weather_enabled','weather_effects')}
                mode=payload.get('tencent_coord_mode',self.settings['tencent_coord_mode'])
                model=payload.get('kimi_model',self.settings['kimi_model'])
                if any(type(v) is not bool for v in enabled.values()) or mode not in ('gps','gcj02') or not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',model):
                    return {'error':'请检查服务开关、坐标类型和模型名称。'}
                config=dict(self.vault.get('trip_services',{}))
                for key in ('tencent_key','tencent_sk','kimi_key'):
                    value=payload.get(key,'')
                    if not isinstance(value,str) or len(value)>512 or value and (len(value)<16 or not re.fullmatch(r'[A-Za-z0-9_.-]+',value)):
                        return {'error':'API Key 格式不正确。'}
                    if value:config[key]=value
                self.vault['trip_services']=config;self.settings.update(enabled,tencent_coord_mode=mode,kimi_model=model)
                self.save_vault();self.save_settings();self.trip_details.cooldown.clear()
                self.message='地名与趣味标题配置已加密保存；预览行程图片即可验证。'
            elif name == 'send_trip_image':
                if not self.settings['notify'] or not self.settings['notify_images']:
                    return {'error':'请先开启行程通知与轨迹图片推送。'}
                if self.notification_mode()!='existing_app':
                    return {'error':'图片需要飞书应用机器人；当前 Webhook 模式仅推送文字。'}
                trip_id = payload.get('trip_id')
                if type(trip_id) is not int or trip_id<=0:
                    return {'error':'请选择一个已结束的行程。'}
                trips=self.store.list_trips(trip_id)
                if not trips or not trips[0]['closed'] or not self.store.points(trip_id):
                    return {'error':'此行程未结束或没有可用轨迹点。'}
                self.queue_trip_image(trip_id,round(time.time()*1000))
                self.settings['image_permission_required']=False
                self.app_sender.token=None
                self.store.db.execute("UPDATE outbox SET status='pending',attempts=0,next_ms=0 WHERE event_key=? AND status IN ('pending','permission_required','failed')",(f'trip-image:{trip_id}:{IMAGE_VERSION}',))
                self.store.db.execute("UPDATE outbox SET status='pending',attempts=0,next_ms=0 WHERE event_key GLOB ? AND status IN ('pending','permission_required','failed')",(f'trip-image:{trip_id}:{IMAGE_VERSION}:part:*',))
                self.store.db.commit()
                self.save_settings()
                self.message='此行程轨迹图已发送过或已排队；相同行程不会重复自动推送。'
            elif name == 'resume_trip_images':
                if self.notification_mode()!='existing_app':
                    return {'error':'图片需要现有飞书应用机器人。'}
                self.settings['image_permission_required']=False
                self.app_sender.token=None
                self.store.db.execute("UPDATE outbox SET status='pending',attempts=0,next_ms=0 WHERE kind='trip_image' AND status IN ('pending','permission_required')")
                self.store.db.commit();self.save_settings()
                self.message='已重新检查待发轨迹图；若飞书权限已生效，会继续自动发送。'
            elif name == 'calibrate_parking_from_trip':
                trip_id = payload.get('trip_id')
                if payload.get('confirmed_parked') is not True or type(trip_id) is not int or not self.selected():
                    return {'error':'需要本人确认该行程已经停车。'}
                vehicle_key = hashlib.sha256(self.selected().identifier.value.encode()).hexdigest()[:20]
                profile = self.store.parking_profile_from_trip(trip_id,vehicle_key)
                if not profile:
                    return {'error':'行程缺少足够的行驶、停车状态和稳定里程证据，保留未确认状态。'}
                self.settings['parking_profiles'][vehicle_key] = profile
                self.save_settings()
                corrected = self.store.reclassify_parking(trip_id,profile)
                if corrected and self.settings['notify']:
                    trip = self.store.list_trips(trip_id)[0]
                    text = f'H6 行程 #{trip_id} 停车识别更正\n原“观测中断”已更正为“已停车（本车发动机状态与定位联合推定）”。\n末段位置和里程稳定，停车状态有至少 3 次新采样、持续 2 分钟支持。\n原始采样和既有里程保留；停车时刻位于最后移动样本与首个稳定停车样本之间。\n后续使用此车辆专属规则识别。查看：'+ORIGIN+'/?trip='+str(trip_id)
                    self.store.enqueue(f'trip:{trip_id}:parking-v1','trip',text,round(time.time()*1000))
                self.message = '已用本人确认停车的行程校准本车联合停车识别；历史原始采样保留。'
            elif name == 'confirm_apertures_closed':
                if payload.get('confirmed_closed') is not True or not self.latest or not self.selected():
                    return {'error':'请先确认本人车辆门窗全部关闭，并读取一次云端状态。'}
                vehicle_key = hashlib.sha256(self.selected().identifier.value.encode()).hexdigest()[:20]
                if self.latest_vehicle_key != vehicle_key:
                    return {'error':'当前显示的状态不属于所选车辆，请先重新读取状态。'}
                baseline = {}
                for metric in self.latest['metrics']:
                    code,value = metric['code'],metric['value']
                    value = str(value) if type(value) in (str,int) else None
                    if code in APERTURES and value is not None and value.isdigit() and int(value)<=15 and (code=='2210005' or value in ('0','1')):
                        baseline[code] = value
                if not baseline:
                    return {'error':'云端未提供可用门窗状态，暂时无法建立基准。'}
                self.settings.update(aperture_baseline=baseline,aperture_baseline_vehicle=vehicle_key,aperture_baseline_source_ms=self.latest['source_ms'],
                                     aperture_baseline_confirmed_ms=round(time.time()*1000))
                stamp,state = self.alert_monitor.load(vehicle_key)
                state = {key:value for key,value in state.items() if not key.startswith('aperture:')}
                self.alert_monitor.save(vehicle_key,stamp,state)
                self.save_settings()
                self.message = '已按你确认的全部关闭状态建立门窗基准；旧采样来源时间保留，提醒只检查后续新采样。'
            elif name == "test_notification":
                await self.send_feishu("H6 通知功能更新测试（非真实行程或故障）\n支持超过 1 分钟的行程结束摘要：定位推定时长、轨迹折线里程、表显里程差、油量表变化及轨迹链接。\n支持胎压/胎温/低油量、停车门窗疑似未关、告警相关状态变化与采集异常提醒。\n真实行程油耗尚无可靠字段。本测试不含车辆位置。")
            else:
                return {"error":"不支持的操作。"}
        except Exception as error:
            self.failure = type(error).__name__
            self.message = "请求未完成（"+self.failure+"），账号和响应原文未写入日志。"
        finally:
            payload.clear()
            self.busy = False
        return self.snapshot()

    async def run(self):
        while not self.stop.is_set():
            if not self.busy:
                for trip_id in self.store.close_stale(round(time.time()*1000)):
                    await self.notify_trip(trip_id)
                # Recover a closure interrupted between the DB commit and enqueue.
                if self.settings['notify']:
                    for row in self.store.db.execute('SELECT id FROM trips WHERE closed=1 AND NOT EXISTS(SELECT 1 FROM notices WHERE trip_id=trips.id) ORDER BY id DESC LIMIT 5').fetchall():
                        await self.notify_trip(row['id'])
            if self.settings["recording"] and self.selected() and self.client.authenticated and not self.busy and time.monotonic() >= self.backoff_until and time.monotonic()-self.last_poll >= self.settings["interval"]:
                self.busy = True
                try:
                    await self.poll()
                except Exception as error:
                    self.failure = type(error).__name__
                    self.message = "后台读取未完成；保留断档，稍后重试。"
                    self.last_poll = time.monotonic()
                    self.error_streak += 1
                    if self.settings['alerts'] and self.notification_mode() != 'none':
                        self.alert_monitor.cloud(False,round(time.time()*1000))
                    retry_after = getattr(error,"retry_after_seconds",None)
                    delay = min(900,60*2**min(self.error_streak,4))
                    if isinstance(retry_after,int) and retry_after > delay:
                        delay = min(86400,retry_after)
                    self.backoff_until = time.monotonic()+delay
                finally:
                    self.busy = False
            try:
                await asyncio.wait_for(self.stop.wait(),timeout=2)
            except TimeoutError:
                pass

    async def run_delivery(self):
        # Map and language-model latency must not delay the 60-second sampling loop.
        while not self.stop.is_set():
            if not self.busy:await self.flush_notifications()
            try:await asyncio.wait_for(self.stop.wait(),timeout=2)
            except TimeoutError:pass

    async def send_feishu(self, text, event_key=None):
        hook = self.vault.get("webhook")
        if not hook:
            if not self.vault.get("feishu_app"):
                raise ValueError("notification_not_configured")
            try:
                await self.app_sender.send(self.vault["feishu_app"],text,event_key=event_key)
            except FeishuDeliveryError as error:
                self.notification_status = "应用机器人发送未完成（"+error.code+"）"
                raise
            self.notification_status = "最近一次发送成功（沿用 NAS 日报机器人）"
            return
        body = {"msg_type":"text","content":{"text":text}}
        if self.vault.get("sign_secret"):
            timestamp = str(int(time.time()))
            key = (timestamp+"\n"+self.vault["sign_secret"]).encode()
            body.update(timestamp=timestamp,sign=base64.b64encode(hmac.new(key,b"",hashlib.sha256).digest()).decode())
        async with ClientSession(timeout=ClientTimeout(total=15),trust_env=False) as session:
            async with session.post(hook,json=body,allow_redirects=False) as response:
                if response.status != 200:
                    self.notification_status = "发送失败（HTTP）"
                    raise ValueError("notification_http_error")
                data = await response.json()
                if data.get("code",data.get("StatusCode")) != 0:
                    self.notification_status = "机器人拒绝，请检查关键字或签名设置"
                    raise ValueError("notification_rejected")
        self.notification_status = "最近一次发送成功"

    async def notify_trip(self, trip_id):
        if not self.settings["notify"] or self.notification_mode() == "none":
            return
        if self.store.db.execute("SELECT 1 FROM notices WHERE trip_id=?",(trip_id,)).fetchone():
            return
        trip = self.store.list_trips(trip_id)[0]
        if not trip['closed']:
            return
        now = round(time.time()*1000)
        if trip['moving_observed_seconds'] <= 60:
            self.store.db.execute('INSERT INTO notices VALUES(?,?,?)',(trip_id,now,'skipped_short'))
            self.store.db.commit()
            return
        minutes = round(trip["observed_span_seconds"]/60,1)
        heading = "定位未更新，停车状态待确认" if trip["end_reason"]=="data_gap" else "已停车（本车发动机状态与定位联合推定）" if trip['end_reason']=='parked_engine_off' else "已停车（位置稳定推定）"
        local_time = lambda stamp: datetime.fromtimestamp(stamp/1000,timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S')
        distance_text=f"{trip['odometer_delta']:g} km" if trip['odometer_delta'] is not None else '暂无数据'
        speed_text=f"{trip['average_speed_kmh']:g} km/h" if trip['average_speed_kmh'] is not None else '暂无数据'
        text = f"H6 行程 #{trip_id} {heading}\n时间：{local_time(trip['start_ms'])} → {local_time(trip['end_ms'])}（北京时间）\n行程用时（起止推定）：{minutes} 分钟\n行驶距离：{distance_text}\n平均速度（估算）：{speed_text}\n采样点：{trip['point_count']}；定位断档：{trip['gap_count']}\n"
        if trip['stop_ms']:
            text += f"推定停车时间窗：{local_time(trip['end_ms'])}–{local_time(trip['stop_ms'])}；确认采样：{local_time(trip['stop_confirmed_ms'])}（北京时间）\n"
        fuel = trip['fuel_gauge_drop_l']
        if fuel is None:
            text += '油量表变化：缺少有效起终点油量\n'
        elif fuel < 0:
            text += f'油量表读数增加：{-fuel:g} L（可能加油或读数波动）\n'
        else:
            text += f'油量表读数下降：{fuel:g} L（仅参考，含精度和波动影响）\n'
        text += '真实行程油耗：当前接口未确认，无法可靠提供。\n低频定位推定，非精确驾驶计时；轨迹坐标系待校准。\n查看轨迹（需连家中网络）：'+ORIGIN+'/?trip='+str(trip_id)
        self.store.db.execute('INSERT INTO notices VALUES(?,?,?)',(trip_id,now,'queued'))
        self.store.db.execute('INSERT OR IGNORE INTO outbox(event_key,kind,text,created_ms,next_ms) VALUES(?,?,?,?,?)',
                              (f'trip:{trip_id}','trip',text,now,now))
        if self.settings['notify_images'] and self.notification_mode()=='existing_app':
            self.store.db.execute('INSERT OR IGNORE INTO outbox(event_key,kind,text,created_ms,next_ms,status) VALUES(?,?,?,?,?,?)',
                                  (f'trip-image:{trip_id}:{IMAGE_VERSION}','trip_image',str(trip_id),now,now,'permission_required' if self.settings['image_permission_required'] else 'pending'))
        self.store.db.commit()

    def queue_trip_image(self, trip_id, now):
        self.store.enqueue(f'trip-image:{trip_id}:{IMAGE_VERSION}','trip_image',str(trip_id),now)
        if self.settings['image_permission_required']:
            self.store.db.execute("UPDATE outbox SET status='permission_required' WHERE event_key=? AND status='pending'",(f'trip-image:{trip_id}:{IMAGE_VERSION}',))
            self.store.db.commit()
        row=self.store.db.execute('SELECT status FROM outbox WHERE event_key=?',(f'trip-image:{trip_id}:{IMAGE_VERSION}',)).fetchone()
        if row and row['status']=='sent':self.queue_detail_images(trip_id,now)

    def image_plan(self,trip_id):
        trips=self.store.list_trips(trip_id)
        if not trips:raise ValueError('trip_not_found')
        return trips[0],build_image_plan(trips[0],self.store.observations(trip_id))

    def queue_detail_images(self,trip_id,now):
        _,plan=self.image_plan(trip_id)
        for number in plan['auto_pages']:
            key=f'trip-image:{trip_id}:{IMAGE_VERSION}:part:{number}'
            self.store.enqueue(key,'trip_image',f'{trip_id}:{number}',now)
            if self.settings['image_permission_required']:
                self.store.db.execute("UPDATE outbox SET status='permission_required' WHERE event_key=? AND status='pending'",(key,))
        self.store.db.commit()

    async def trip_png(self, trip_id,require_basemap=False,segment=0):
        async with self.image_lock:
            return await self.render_png(trip_id,require_basemap,segment)

    async def render_png(self, trip_id,require_basemap=False,segment=0):
        trip,plan=self.image_plan(trip_id)
        trip.update(self.store.fuel_summary(trip_id))
        section=next((p for p in plan['pages'] if p['number']==segment),None) if segment else None
        if segment and not section:raise ValueError('segment_not_found')
        points=section['points'] if section else plan['points']
        if not points:raise ValueError('no_route_points')
        config=dict(self.vault.get('trip_services',{}));mode=self.settings.get('tencent_coord_mode','gps')
        async def road_layer():
            return await self.road_cache.get(points,config,mode) if self.settings['road_basemap'] else None
        roads,details=await asyncio.gather(road_layer(),self.trip_details.get(trip,plan['points'],config,dict(self.settings)))
        details['weather_effects']=self.settings['weather_effects']
        if require_basemap and self.settings['road_basemap'] and not (roads and roads.get('available')):
            raise ValueError('basemap_pending')
        if section:
            details['weather']=await self.trip_details.weather.get({'end_ms':section['end_ms']},points[-1],bool(self.settings['weather_enabled'] and config.get('tencent_key')))
        stops=[p for p in plan['stops'] if not section or section['start_ms']<=p['end_ms']<=section['end_ms']]
        shown=sorted(sorted(stops,key=lambda p:-p['duration_seconds'])[:4],key=lambda p:p['start_ms'])
        details['stops']=await self.trip_details.name_stops(shown,config,dict(self.settings))
        if roads and roads.get('source')=='Tencent' and mode=='gps':
            for p in details['stops']:p['lat'],p['lon']=gcj02(p['lat'],p['lon'])
        details['stop_count']=len(stops)
        details['detail_count']=len(plan['auto_pages'])
        if section:details['segment']={k:section[k] for k in ('number','start_ms','end_ms')};details['segment']['total']=len(plan['pages'])
        png=await asyncio.to_thread(render_trip,trip,points,roads,details)
        directory=self.directory/'images';directory.mkdir(mode=0o700,exist_ok=True);directory.chmod(0o700)
        target=directory/(f'trip-{trip_id}-segment-{segment}.png' if segment else f'trip-{trip_id}.png');temporary=target.with_suffix('.tmp')
        temporary.write_bytes(png);temporary.chmod(0o600);temporary.replace(target)
        return png

    async def deliver_trip_image(self, row):
        config=self.vault['feishu_app'];key=row['image_key']
        parts=row['text'].split(':');trip_id=int(parts[0]);segment=int(parts[1]) if len(parts)>1 else 0
        if not key:
            png=await self.trip_png(trip_id,require_basemap=True,segment=segment)
            key=await self.app_sender.upload_png(config,png)
            self.store.db.execute('UPDATE outbox SET image_key=? WHERE event_key=?',(key,row['event_key']))
            self.store.db.commit()
        await self.app_sender.send_image(config,key,event_key=row['event_key'])
        if not segment:self.queue_detail_images(trip_id,round(time.time()*1000))
        self.notification_status='轨迹图片发送成功（沿用 NAS 日报机器人）'

    async def flush_notifications(self):
        if self.notification_mode() == 'none':
            return
        now = round(time.time()*1000)
        images_enabled=self.settings['notify'] and self.settings['notify_images'] and self.notification_mode()=='existing_app'
        rows = self.store.db.execute("SELECT * FROM outbox WHERE status='pending' AND next_ms<=? AND ((kind='trip' AND ?) OR (kind='alert' AND ?) OR (kind='trip_image' AND ?)) ORDER BY created_ms,rowid LIMIT 1",
                                     (now,int(self.settings['notify']),int(self.settings['alerts']),int(images_enabled))).fetchall()
        for row in rows:
            attempts = row['attempts']+1
            try:
                async with asyncio.timeout(70 if row['kind']=='trip_image' else 30):
                    if row['kind']=='trip_image':
                        await self.deliver_trip_image(row)
                    else:
                        await self.send_feishu(row['text'],event_key=row['event_key'])
                state = 'sent'
            except Exception as error:
                state = 'failed' if attempts>=5 else 'pending'
                self.notification_status = '通知暂未送达；自动重试中' if state=='pending' else '通知重试 5 次未送达，请检查飞书通道'
                if row['kind']=='trip_image' and type(error) is ValueError and str(error)=='basemap_pending':
                    self.notification_status='道路底图暂未取得，轨迹图等待自动重试' if state=='pending' else '道路底图重试 5 次未取得，轨迹已保存，请稍后补发图片'
                    if state=='failed':
                        self.store.enqueue('map-failed:'+row['event_key'],'alert',f"H6 行程 #{row['text']} 道路底图获取连续失败，图片尚未发送。车辆采集和轨迹记录继续，可稍后在 NAS 页面预览并补发。",now)
                if isinstance(error,FeishuDeliveryError):
                    self.notification_status += '（飞书代码 '+error.code+'）'
                    if row['kind']=='trip_image' and error.code=='99991672':
                        state='permission_required'
                        self.settings['image_permission_required']=True;self.save_settings()
                        self.store.db.execute("UPDATE outbox SET status='permission_required' WHERE kind='trip_image' AND status='pending'")
                        self.notification_status='图片上传需在飞书开通 im:resource 权限（99991672）；图片队列已暂停，文字通知正常。'
            self.store.db.execute('UPDATE outbox SET status=?,attempts=?,next_ms=? WHERE event_key=?',
                                  (state,attempts,now+min(900000,60000*2**(attempts-1)),row['event_key']))
            if row['kind']=='trip':
                self.store.db.execute('UPDATE notices SET status=? WHERE trip_id=?',(state,int(row['event_key'].split(':')[1])))
            self.store.db.commit()

    def snapshot(self):
        return {"vehicle_client_available":vehicle_backend.available(),"stage":self.stage,"message":self.message,"failure":self.failure,"busy":self.busy,
            "vehicles":[{"index":i,"label":f"车辆 {i+1} · {v.model_name or v.brand_name or 'GWM'}","platform":v.platform,"selected":v.identifier.value==self.vault.get('selected')} for i,v in enumerate(self.vehicles)],
            "settings":self.settings,"latest":self.latest,"point_count":self.store.count(),"trips":self.store.list_trips(),
            "webhook_configured":bool(self.vault.get("webhook")),"notification_mode":self.notification_mode(),"notification_status":self.notification_status,
            "notification_counts":{row['status']:row['n'] for row in self.store.db.execute('SELECT status,COUNT(*) AS n FROM outbox GROUP BY status')},
            "image_notification_counts":{row['status']:row['n'] for row in self.store.db.execute("SELECT status,COUNT(*) AS n FROM outbox WHERE kind='trip_image' GROUP BY status")},
            "road_map_status":self.road_cache.last_status,
            "trip_services_status":self.trip_details.status,
            "weather_status":self.trip_details.weather.status,
            "trip_services_configured":{k:bool(self.vault.get('trip_services',{}).get(k)) for k in ('tencent_key','tencent_sk','kimi_key')},
            "discovery_diagnostic":self.client.discovery_diagnostic if self.client else {}}

    def public(self):
        result = {"stage":self.stage,"failure":self.failure,"vehicle_count":len(self.vehicles),
                  "recording":self.settings["recording"],"point_count":self.store.count(),"webhook_configured":bool(self.vault.get("webhook")),"notification_mode":self.notification_mode()}
        if self.latest:
            result.update(has_location=self.latest["has_location"],age_seconds=self.latest["age_seconds"],
                          metric_codes=[m["code"] for m in self.latest["metrics"]])
        return result

    async def close(self):
        self.stop.set()
        if self.task:
            await self.task
        if self.delivery_task:
            await self.delivery_task
        if self.client:
            await self.client.aclose()
        if self.transport:
            await self.transport.aclose()
        self.store.close()


def build_app(recorder):
    @web.middleware
    async def security(request, handler):
        allowed_hosts = {urlsplit(ORIGIN).netloc}
        if request.path == "/health":
            allowed_hosts.update({"127.0.0.1:18770","localhost:18770"})
        if request.host not in allowed_hosts:
            return web.json_response({"error":"host"},status=403)
        if request.path.startswith("/api/") and request.method == "POST" and request.headers.get("Origin") != ORIGIN:
            return web.json_response({"error":"origin"},status=403)
        try:
            result = await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            result = web.json_response({"error":"请求未完成。"},status=400)
        result.headers.update({"Cache-Control":"public, max-age=604800" if request.path.startswith('/static/leaflet/') else "no-store","X-Content-Type-Options":"nosniff",
            "Referrer-Policy":"strict-origin-when-cross-origin",
            "Content-Security-Policy":"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"})
        return result

    app = web.Application(middlewares=[security],client_max_size=8192)
    async def page(request):
        return web.Response(text=(ROOT/"index.html").read_text(encoding="utf-8"),content_type="text/html")
    async def health(request):
        return web.json_response({"service":"h6-recorder","ready":True})
    async def state(request):
        return web.json_response(recorder.snapshot())
    async def action(request):
        data = await request.json()
        if not isinstance(data,dict):
            return web.json_response({"error":"格式错误。"},status=400)
        return web.json_response(await recorder.action(request.match_info["name"],data))
    async def points(request):
        query = request.query.get("trip")
        return web.json_response(recorder.store.points(int(query) if query else None))
    async def trip_image(request):
        query=request.query.get('trip','')
        if not query.isdigit() or not 0<int(query)<2147483647:
            return web.json_response({'error':'请选择有效行程。'},status=400)
        trip_id=int(query)
        if not recorder.store.list_trips(trip_id):
            return web.json_response({'error':'行程不存在。'},status=404)
        part=request.query.get('segment','0')
        if not part.isdigit() or not 0<=int(part)<=500:return web.json_response({'error':'分段编号无效。'},status=400)
        segment=int(part)
        if segment and not any(p['number']==segment for p in recorder.image_plan(trip_id)[1]['pages']):
            return web.json_response({'error':'分段不存在。'},status=404)
        png=await recorder.trip_png(trip_id,segment=segment)
        return web.Response(body=png,content_type='image/png',headers={'Content-Disposition':f'inline; filename="h6-trip-{trip_id}.png"'})
    async def trip_pages(request):
        query=request.query.get('trip','')
        if not query.isdigit() or not 0<int(query)<2147483647:return web.json_response({'error':'请选择有效行程。'},status=400)
        trip_id=int(query)
        if not recorder.store.list_trips(trip_id):return web.json_response({'error':'行程不存在。'},status=404)
        _,plan=recorder.image_plan(trip_id)
        return web.json_response({'stop_count':len(plan['stops']),'auto_pages':plan['auto_pages'],
            'pages':[{k:p[k] for k in ('number','start_ms','end_ms')} for p in plan['pages']]})
    async def export(request):
        query = request.query.get("trip")
        points = recorder.store.points(int(query) if query else None)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["source_time_utc","latitude_raw","longitude_raw","break_before","coordinate_system"])
        for p in points:
            writer.writerow([datetime.fromtimestamp(p["source_ms"]/1000,timezone.utc).isoformat(),p["lat"],p["lon"],int(p["break_before"]),"unknown"])
        result = web.Response(body=output.getvalue().encode("utf-8-sig"),content_type="text/csv")
        result.headers["Content-Disposition"] = 'attachment; filename="h6-raw-recorded-points.csv"'
        return result
    async def startup(app):
        await recorder.startup()
    async def cleanup(app):
        await recorder.close()
    app.router.add_get("/",page)
    app.router.add_get("/health",health)
    app.router.add_static("/static/",ROOT/"static")
    app.router.add_get("/api/state",state)
    app.router.add_get("/api/points",points)
    app.router.add_get('/api/trip-image.png',trip_image)
    app.router.add_get('/api/trip-pages',trip_pages)
    app.router.add_get("/api/export.csv",export)
    app.router.add_post("/api/{name}",action)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    recorder = Recorder(DATA)
    web.run_app(build_app(recorder),host=os.environ.get("BIND_HOST","127.0.0.1"),port=int(os.environ.get("PORT","18770")),access_log=None,print=None)
