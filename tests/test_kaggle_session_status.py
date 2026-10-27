"""Exact interactive session evidence, including empty logs and provider failures."""
import io
from types import SimpleNamespace

import pytest

from src.rav.pipeline.kaggle_gpu import KaggleGPUClient
from src.rav.worker.kaggle_adapter import KaggleSlotAdapter


@pytest.mark.parametrize('mime,body,expected', [
    ('text/event-stream', b'', 'RUNNING'),
    ('application/json', b'', 'COMPLETE'),
    ('application/json', b'[]', 'COMPLETE'),
    ('application/json', b'{"code":403}', 'UNKNOWN'),
    ('text/html', b'', 'UNKNOWN'),
])
def test_exact_session_headers(monkeypatch, mime, body, expected):
    class Response:
        status_code = 200
        headers = {'Content-Type': mime}
        raw = io.BytesIO(body)

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def prepare(service, method, req):
        assert req.kernel_session_id == 123 and method == 'GetKernelSessionLogsStream'
        return SimpleNamespace(url='https://provider.invalid')

    session = SimpleNamespace(merge_environment_settings=lambda *args: {'stream': True},
                              send=lambda *args, **kwargs: Response())
    p = object.__new__(KaggleGPUClient)
    p.kernels = SimpleNamespace(_client=SimpleNamespace(_init_session=lambda: None,
                                _prepare_request=prepare, _session=session))
    assert p.interactive_session_status(123) == expected


def test_interactive_never_trusts_notebook_status():
    seen = []
    p = SimpleNamespace(session_status=lambda slug: 'COMPLETE',
                        interactive_session_status=lambda sid: seen.append(sid) or 'RUNNING',
                        stop_session=lambda sid: None)
    a = KaggleSlotAdapter(p, 'account', 'gpu-1')
    assert a.reconcile_launch().status == 'UNKNOWN'
    assert a.reconcile_launch(123).status == 'FOUND'
    assert not a.stop_and_verify(123, 0)
    assert seen == [123, 123]


def test_lookup_and_provider_error_keep_unknown():
    def failed(sid):
        raise TimeoutError
    a = KaggleSlotAdapter(SimpleNamespace(interactive_session_status=failed), 'account', 'gpu-1', lambda: 123)
    assert a.reconcile_launch().status == 'UNKNOWN'
