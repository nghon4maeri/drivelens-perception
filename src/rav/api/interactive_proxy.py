"""Render→Contabo verified HTTPS proxy; no ML imports or proposal DB writes (§21.26)."""
import os
import ssl
from urllib.parse import urlsplit

import httpx

from ..errors import ServiceError


class InteractiveProxy:
    def __init__(self):
        self.url = os.environ.get('RAV_INTERACTIVE_URL', '').rstrip('/')
        self.token = os.environ.get('RAV_INTERACTIVE_TOKEN', '')
        self.client = None

    def enabled(self):
        return bool(self.url)

    def call(self, path, body):
        if self.client is None:
            p = urlsplit(self.url)
            if not self.token or p.username or p.password or p.query or p.fragment or p.path:
                raise ServiceError('interactive_unconfigured', 'cấu hình interactive không hợp lệ', 503)
            if p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost')):
                raise ServiceError('interactive_unconfigured', 'interactive cần HTTPS', 503)
            tls = ssl.create_default_context()
            cert = os.environ.get('RAV_INTERACTIVE_CA_PEM', '')
            if cert:
                tls.load_verify_locations(cadata=cert.replace('\\n', '\n'))
            self.client = httpx.Client(verify=tls, timeout=60, limits=httpx.Limits(max_connections=8))
        try:
            r = self.client.post(self.url+path, json=body, headers={'Authorization': 'Bearer '+self.token})
        except httpx.RequestError:
            raise ServiceError('interactive_unavailable', 'không kết nối được Contabo; thử lại cùng request', 503) from None
        if not r.is_success:
            error = r.json().get('error', {})
            raise ServiceError(error.get('code', 'interactive_error'), error.get('message', 'interactive lỗi'), r.status_code,
                               {k: v for k, v in error.items() if k not in ('code', 'message')})
        return r.json()
