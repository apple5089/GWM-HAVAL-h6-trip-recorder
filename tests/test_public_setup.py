import json
from types import SimpleNamespace
import pytest
from test_recorder import module

@pytest.mark.asyncio
async def test_feishu_setup_encrypts_config_and_keeps_existing_secret(tmp_path):
    recorder=module.Recorder(tmp_path)
    try:
        config={'app_id':'SYNTHETIC_APP','app_secret':'SYNTHETIC_PRIVATE_SECRET','receive_id':'oc_SYNTHETIC_ROOM'}
        result=await recorder.action('feishu_app',dict(config))
        assert result['notification_mode']=='existing_app'
        assert all(value not in json.dumps(result) for value in config.values())
        assert all(value.encode() not in recorder.vault_file.read_bytes() for value in config.values())
        await recorder.action('feishu_app',{'app_secret':'','app_id':'','receive_id':'oc_SYNTHETIC_SECOND_ROOM'})
        assert recorder.vault['feishu_app']['app_secret']==config['app_secret']
        assert recorder.vault['feishu_app']['receive_id']=='oc_SYNTHETIC_SECOND_ROOM'
        assert (await recorder.action('feishu_app',{'receive_id':'https://invalid.example'})).get('error')
        assert recorder.vault['feishu_app']['receive_id']=='oc_SYNTHETIC_SECOND_ROOM'
    finally:await recorder.close()

@pytest.mark.asyncio
async def test_missing_vehicle_client_leaves_offline_app_usable(tmp_path,monkeypatch):
    monkeypatch.setattr(module.vehicle_backend,'available',lambda:False)
    recorder=module.Recorder(tmp_path)
    try:
        assert recorder.snapshot()['vehicle_client_available'] is False
        assert (await recorder.action('sms',{'phone':'13800000000'})).get('error')
        assert recorder.client is None and not recorder.vault.get('phone')
    finally:await recorder.close()

@pytest.mark.asyncio
async def test_poll_auth_failure_uses_backend_exception_type(tmp_path,monkeypatch):
    class AuthenticationError(Exception):pass
    class Client:
        authenticated=True
        async def get_last_status(self,identifier):raise AuthenticationError()
        async def authenticate(self,*args,**kwargs):raise RuntimeError('synthetic refresh failure')
        async def aclose(self):pass
    monkeypatch.setitem(module.vehicle_backend.__dict__,'GwmAuthenticationError',AuthenticationError)
    recorder=module.Recorder(tmp_path)
    recorder.client=Client()
    recorder.selected=lambda:SimpleNamespace(identifier='synthetic')
    try:
        with pytest.raises(RuntimeError,match='synthetic refresh failure'):
            await recorder.poll()
    finally:await recorder.close()
