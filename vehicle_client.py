"""Load a separately supplied vehicle client only when live access is needed."""
from importlib import import_module,util

class VehicleClientUnavailable(RuntimeError):
    pass

def available():
    return util.find_spec('gwm_client') is not None

def __getattr__(name):
    if not available():
        raise VehicleClientUnavailable('Configure a compatible vehicle client; see docs/VEHICLE_CLIENT.md')
    modules={
        'ChinaAiohttpTransport':'gwm_client.china_transport',
        '_ChinaTransportRequest':'gwm_client.china_transport',
        'GwmRoutePolicyError':'gwm_client.errors',
        'GwmAuthenticationError':'gwm_client.errors',
        'DiagnosticChinaClient':'discovery_compat',
    }
    value=getattr(import_module(modules.get(name,'gwm_client.china_client')),name)
    globals()[name]=value
    return value
