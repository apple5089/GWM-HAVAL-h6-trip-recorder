"""Send through the owner's existing Feishu application bot and fixed chat."""
import json
import re
import time
import uuid

from aiohttp import ClientSession, ClientTimeout, FormData

BASE = "https://open.feishu.cn/open-apis"


class FeishuDeliveryError(Exception):
    def __init__(self, code="request_failed"):
        self.code = str(code) if isinstance(code,int) else "request_failed"
        super().__init__(self.code)


class AppSender:
    def __init__(self, session_factory=ClientSession):
        self.session_factory = session_factory
        self.token = None
        self.expires = 0
        self.app_id = None

    def validate(self, config):
        app_id = config.get("app_id")
        secret = config.get("app_secret")
        recipient = config.get("receive_id")
        if not all(isinstance(v,str) and v for v in (app_id,secret,recipient)) or config.get("receive_id_type") != "chat_id" or not re.fullmatch(r"oc_[A-Za-z0-9_]+",recipient):
            raise FeishuDeliveryError()
        return app_id,secret,recipient

    async def ensure_token(self, session, config):
        app_id,secret,_ = self.validate(config)
        if not self.token or self.app_id != app_id or time.monotonic() >= self.expires:
            async with session.post(BASE+"/auth/v3/tenant_access_token/internal",json={"app_id":app_id,"app_secret":secret},allow_redirects=False) as response:
                if response.status != 200:
                    raise FeishuDeliveryError()
                data = await response.json()
            if data.get("code") != 0 or not isinstance(data.get("tenant_access_token"),str) or not data["tenant_access_token"]:
                raise FeishuDeliveryError(data.get("code"))
            self.token = data["tenant_access_token"]
            self.app_id = app_id
            ttl = data.get("expire",3600)
            self.expires = time.monotonic()+max(1,min(ttl if type(ttl) is int else 3600,7200)-60)

    async def send(self, config, text, event_key=None):
        await self.send_content(config,'text',{'text':text},event_key)

    async def send_image(self, config, image_key, event_key=None):
        if not isinstance(image_key,str) or not re.fullmatch(r'img_[A-Za-z0-9_-]{3,300}',image_key):
            raise FeishuDeliveryError()
        await self.send_content(config,'image',{'image_key':image_key},event_key)

    async def upload_png(self, config, png):
        self.validate(config)
        if not isinstance(png,bytes) or not png.startswith(b'\x89PNG\r\n\x1a\n') or not 8<len(png)<8*1024*1024:
            raise FeishuDeliveryError()
        async with self.session_factory(timeout=ClientTimeout(total=15),trust_env=False) as session:
            await self.ensure_token(session,config)
            body=FormData();body.add_field('image_type','message')
            body.add_field('image',png,filename='h6-trip.png',content_type='image/png')
            async with session.post(BASE+'/im/v1/images',data=body,headers={'Authorization':'Bearer '+self.token},allow_redirects=False) as response:
                try:result=await response.json()
                except Exception:raise FeishuDeliveryError(response.status) from None
                if response.status!=200 or result.get('code')!=0:
                    raise FeishuDeliveryError(result.get('code',response.status))
            key=result.get('data',{}).get('image_key')
            if not isinstance(key,str) or not re.fullmatch(r'img_[A-Za-z0-9_-]{3,300}',key):
                raise FeishuDeliveryError()
            return key

    async def send_content(self, config, message_type, content, event_key):
        _,_,recipient = self.validate(config)
        async with self.session_factory(timeout=ClientTimeout(total=15),trust_env=False) as session:
            await self.ensure_token(session,config)
            message_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL,'h6-recorder:'+event_key)) if event_key else str(uuid.uuid4())
            body = {"receive_id":recipient,"msg_type":message_type,"content":json.dumps(content,ensure_ascii=False),"uuid":message_uuid}
            async with session.post(BASE+"/im/v1/messages?receive_id_type=chat_id",json=body,
                                    headers={"Authorization":"Bearer "+self.token},allow_redirects=False) as response:
                if response.status != 200:
                    raise FeishuDeliveryError()
                result = await response.json()
            if result.get("code") != 0:
                self.token = None
                raise FeishuDeliveryError(result.get("code"))
