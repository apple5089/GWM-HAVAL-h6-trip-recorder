from collections import deque
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from feishu_app import AppSender,FeishuDeliveryError,BASE

CONFIG={"app_id":"SYNTHETIC-APP","app_secret":"PRIVATE-SYNTHETIC-SECRET","receive_id_type":"chat_id","receive_id":"oc_SYNTHETIC123"}


class FakeResponse:
    status=200
    def __init__(self,data):self.data=data
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def json(self):return self.data


class FakeSession:
    def __init__(self,responses,calls):self.responses=deque(responses);self.calls=calls
    def __call__(self,**kwargs):return self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return FakeResponse(self.responses.popleft())


@pytest.mark.asyncio
async def test_fixed_destination_token_cache_and_text_payload():
    calls=[]
    transport=FakeSession([{"code":0,"tenant_access_token":"SYNTHETIC-TOKEN","expire":7200},{"code":0},{"code":0}],calls)
    sender=AppSender(transport)
    await sender.send(CONFIG,"H6 test one")
    await sender.send(CONFIG,"H6 test two")
    assert len(calls)==3
    assert calls[0][0]==BASE+'/auth/v3/tenant_access_token/internal'
    for url,request in calls[1:]:
        assert url==BASE+'/im/v1/messages?receive_id_type=chat_id'
        assert request['json']['receive_id']==CONFIG['receive_id']
        assert request['json']['msg_type']=='text'
        assert json.loads(request['json']['content'])['text'].startswith('H6 test')
        assert request['allow_redirects'] is False
        assert CONFIG['app_secret'] not in json.dumps(request)


@pytest.mark.asyncio
async def test_arbitrary_error_text_and_secrets_are_not_in_exception():
    transport=FakeSession([{"code":42,"msg":"PRIVATE-SERVER-MESSAGE"}],[])
    with pytest.raises(FeishuDeliveryError) as error:
        await AppSender(transport).send(CONFIG,"test")
    assert str(error.value)=='42'
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.asyncio
async def test_unknown_recipient_type_stops_before_network():
    calls=[]
    with pytest.raises(FeishuDeliveryError):
        await AppSender(FakeSession([],calls)).send({**CONFIG,"receive_id_type":"email"},"test")
    assert not calls


@pytest.mark.asyncio
async def test_retry_has_stable_uuid_and_other_events_have_distinct_uuid():
    calls=[]
    transport=FakeSession([{'code':0,'tenant_access_token':'SYNTHETIC','expire':7200}, {'code':0},{'code':0},{'code':0}],calls)
    sender=AppSender(transport)
    await sender.send(CONFIG,'synthetic',event_key='trip:1')
    await sender.send(CONFIG,'synthetic',event_key='trip:1')
    await sender.send(CONFIG,'synthetic',event_key='trip:2')
    uuids=[request['json']['uuid'] for _,request in calls[1:]]
    assert uuids[0]==uuids[1] and uuids[1]!=uuids[2]


@pytest.mark.asyncio
async def test_upload_and_image_message_reuse_token_and_fixed_recipient():
    calls=[]
    transport=FakeSession([{'code':0,'tenant_access_token':'SYNTHETIC','expire':7200},
                           {'code':0,'data':{'image_key':'img_SYNTHETIC123'}},{'code':0}],calls)
    sender=AppSender(transport)
    key=await sender.upload_png(CONFIG,b'\x89PNG\r\n\x1a\nSYNTHETIC')
    await sender.send_image(CONFIG,key,event_key='trip-image:1:v1')
    assert len(calls)==3 and calls[1][0]==BASE+'/im/v1/images'
    fields=calls[1][1]['data']._fields
    assert fields[0][0]['name']=='image_type' and fields[0][2]=='message'
    assert fields[1][0]['name']=='image' and fields[1][1]['Content-Type']=='image/png'
    assert calls[1][1]['allow_redirects'] is False
    body=calls[2][1]['json']
    assert body['receive_id']==CONFIG['receive_id'] and body['msg_type']=='image'
    assert json.loads(body['content'])=={'image_key':key}


@pytest.mark.asyncio
async def test_image_rejects_unexpected_key_and_empty_or_non_png_before_network():
    calls=[];sender=AppSender(FakeSession([],calls))
    for png in (b'',b'not-a-png'):
        with pytest.raises(FeishuDeliveryError):await sender.upload_png(CONFIG,png)
    with pytest.raises(FeishuDeliveryError):await sender.send_image(CONFIG,'https://unexpected.example/image')
    assert not calls


@pytest.mark.asyncio
async def test_upload_permission_error_exposes_only_numeric_code():
    class PermissionSession(FakeSession):
        def post(self,url,**kwargs):
            response=super().post(url,**kwargs)
            response.status=400 if url.endswith('/images') else 200
            return response
    transport=PermissionSession([{'code':0,'tenant_access_token':'SYNTHETIC','expire':7200},
                                 {'code':99991672,'msg':'PRIVATE SERVER CONTENT'}],[])
    with pytest.raises(FeishuDeliveryError) as error:
        await AppSender(transport).upload_png(CONFIG,b'\x89PNG\r\n\x1a\nSYNTHETIC')
    assert str(error.value)=='99991672' and 'PRIVATE' not in str(error.value)
