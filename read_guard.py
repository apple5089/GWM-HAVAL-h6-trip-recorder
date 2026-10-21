"""The validation app exposes authentication and vehicle reads only."""
from urllib.parse import urlsplit

READ_OPERATIONS = frozenset({
    "request_verification", "login", "refresh_token", "initialize_bean_tech",
    "initialize_auto_ai", "acquire_vehicles", "get_last_status",
})
ORIGINS = frozenset({
    ("gapp-api.gwmapp-h.com", 443), ("gw-app-gateway.gwmapp-h.com", 443),
    ("ti.gwm.com.cn", 8443), ("apgdm.gwmcloudcn.com", 443),
})


def permitted(operation, method, url):
    try:
        parsed = urlsplit(url)
        return (
            operation in READ_OPERATIONS and method in {"GET", "POST"}
            and parsed.scheme == "https" and not parsed.username and not parsed.password
            and (parsed.hostname, parsed.port or 443) in ORIGINS
        )
    except (ValueError, TypeError):
        return False


class ReadOnlyTransport:
    def __init__(self, inner, request_type, denied_error):
        self.inner = inner
        self.request_type = request_type
        self.denied_error = denied_error
        self.operations = []

    async def execute(self, request, **kwargs):
        if type(request) is not self.request_type or not permitted(request.operation, request.method, request.url):
            raise self.denied_error()
        if len(self.operations) < 100:
            self.operations.append(request.operation)
        return await self.inner.execute(request, **kwargs)

    async def aclose(self):
        await self.inner.aclose()
