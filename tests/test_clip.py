"""CLIP gates, strict diversity, immutable query/replay and remote cache/error boundaries.

No network/models in CI; real checkpoint smoke is run separately with tiny local images/text.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.rav.api import create_app
from src.rav.components.features.clip_b32 import ClipB32
from src.rav.components.objectives import text_match
from src.rav.components.selectors.greedy_nms import GreedyNMS
from src.rav.core.types import FieldTable
from src.rav.pipeline import prepare as p1
from src.rav.pipeline.remote_embed import remote_compute, validate_result
from src.rav.service import clip_prepare
from src.rav.service.session import make_context

from . import test_p5a as p5

app = p5.app


def basis(n, dim=512):
    return np.eye(dim, dtype=np.float32)[:n]


def install_clip(app):
    for video in p1.catalog(app.spec).values():
        ctx = p1.make_context([video], app.spec, app.fields)
        app.fields.put(FieldTable(ClipB32().key, [u.id for u in ctx.units], basis(len(ctx.units)), "vector"),
                       (video.id, ctx._execution.spec_hash))


def test_gate_or_negatives_and_strict_batch(app, monkeypatch):
    install_clip(app)
    row = p5.new_session(app)
    ctx = make_context(app, app.db.get_session(row.id))
    monkeypatch.setattr(text_match, "encode_texts", lambda texts, *args: basis(3)[[0, 2, 1]])
    obj = text_match.TextMatch({"texts": ["car", "person"], "negative": ["empty road"], "min_similarity": 0.1})
    result = obj.score(ctx.units, ctx)
    assert np.flatnonzero(result.mask).tolist() == [0, 2]
    assert result.values[0] == 1 and result.values[1] == 0.5
    assert not text_match.TextMatch.uses_goal
    # Identical DINO embedding, mask admits two: strict NMS returns only one; never fills with masked-out unit.
    fake_ctx = SimpleNamespace(field=lambda field, units: SimpleNamespace(values=np.ones((len(units), 8))))
    strict = GreedyNMS({"relax": False, "nms_s": 0, "min_dist": 0.1})
    out = strict.pick(ctx.units, result.values, result.mask, 16, fake_ctx)
    assert out.indices == [0] and not out.relaxed
    assert strict.pick(ctx.units, result.values, np.zeros(len(ctx.units), bool), 16, fake_ctx).indices == []


def test_invalid_queries_and_revision_cache_identity():
    for kwargs in ({"texts": []}, {"texts": [" "]}, {"negative": [""]}, {"field": "emb.dinov2_s"}, {"min_similarity": 2}):
        with pytest.raises(ValidationError):
            text_match.TextMatch(kwargs)
    assert ClipB32().key != ClipB32({"revision": "a" * 40}).key
    with pytest.raises(ValidationError):
        ClipB32({"revision": "main"})


def test_http_clip_not_ready_query_snapshot_and_replay(app, monkeypatch):
    client = TestClient(create_app(app))
    row = p5.new_session(app)
    base = f"/api/v1/sessions/{row.id}"
    monkeypatch.setattr(ClipB32, "compute", lambda *args: pytest.fail("HTTP must not compute CLIP image embeddings"))
    response = client.post(base + "/proposals", json={"recipe": "text_diversity"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "clip_not_prepared"
    assert app.db.proposals(row.id) == []
    install_clip(app)
    monkeypatch.setattr(text_match, "encode_texts", lambda texts, *args: basis(1))
    recipes = client.get("/api/v1/recipes").json()
    config = next(r["config"] for r in recipes if r["name"] == "text_diversity")
    config["objectives"][0]["params"] = {"texts": ["car"], "min_similarity": 0.1}
    p = client.post(base + "/proposals", json={"recipe": "text_diversity", "config": config, "return_scores": True}).json()
    assert len(p["picks"]) == 1 and p["picks"][0]["breakdown"]["text_match"] == 1
    assert p["input"]["config"]["objectives"][0]["params"]["texts"] == ["car"]
    assert p["scores"]["unit_id"] == [pick['unit_id'] for pick in p['picks']]
    e = client.get(base + "/proposals/1/explain").json()
    assert e["semantic_query"]["texts"] == ["car"] and not e["uses_goal"]
    assert e["similarity_field"] == "emb.dinov2_s"
    original_goal = client.get(base).json()["goal"]
    config["objectives"][0]["params"] = {"texts": ["nothing"], "min_similarity": 1.0, "negative": ["car"]}
    monkeypatch.setattr(text_match, "encode_texts", lambda texts, *args: basis(1).repeat(len(texts), axis=0))
    second = client.post(base + "/proposals", json={"recipe": "text_diversity", "config": config}).json()
    assert second["picks"] == []
    assert client.get(base + "/proposals/1/explain").json()["semantic_query"]["texts"] == ["car"]
    assert client.get(base).json()["goal"] == original_goal
    # Existing DINO recipe still works, including keep/drop/export path covered by existing suite.
    assert client.post(base + "/proposals", json={"recipe": "diversity"}).status_code == 200


class FakeClient:
    def __init__(self, mode="ok"):
        self.mode, self.started, self.stopped = mode, [], []
        self.session = SimpleNamespace(session_id=123, tools=self)

    def create_session(self, kernel, **kwargs):
        assert kwargs == {"accelerator": "t4", "enable_internet": True, "wait": False}
        self.started.append(kernel)
        return self.session

    def wait_session(self, session, **kwargs):
        if self.mode == "timeout":
            raise TimeoutError("https://private-token-proxy.example/secret")

    def put_file(self, local, remote):
        with zipfile.ZipFile(local) as archive:
            self.manifest = json.loads(archive.read("manifest.json"))
            assert archive.read(self.manifest["samples"][0][0]["name"]).startswith(b"\x89PNG")

    def execute(self, code, **kwargs):
        assert "cuda.is_available" in code and "3d74acf" not in code  # revision travels with manifest
        return {"status": "error" if self.mode == "execute_error" else "ok"}

    def get_file(self, remote, local):
        m = json.loads(json.dumps(self.manifest))
        values = basis(len(m["sample_ids"]))
        if self.mode == "ids":
            m["sample_ids"].reverse()
        if self.mode == "revision":
            m["revision"] = "a" * 40
        if self.mode == "shape":
            values = values[:, :3]
        if self.mode == "nan":
            values[0, 0] = np.nan
        if self.mode == "norm":
            values *= 2
        np.savez(local, values=values, metadata=json.dumps({"manifest": m, "job_id": "123", "status": "done"}))

    def stop_session(self, sid):
        self.stopped.append(sid)


@pytest.mark.parametrize("mode", ["ok", "timeout", "execute_error", "ids", "revision", "shape", "nan", "norm"])
def test_remote_validation_cleanup(app, mode):
    video = next(iter(p1.catalog(app.spec).values()))
    ctx = p1.make_context([video], app.spec, app.fields)
    client = FakeClient(mode)
    def call():
        return remote_compute(ClipB32(), ctx.units, ctx, kernel="owner/own-notebook", output_dir=app.output_dir,
                              max_payload_bytes=1 << 20, timeout=1, client_factory=lambda: client)
    if mode == "ok":
        table = call()
        assert table.unit_ids == [u.id for u in ctx.units] and table.values.shape == (len(ctx.units), 512)
    else:
        with pytest.raises((ValueError, RuntimeError)) as err:
            call()
        assert "private-token" not in str(err.value)
    assert client.stopped == [123] and not list((app.output_dir / "tmp").iterdir())
    assert not clip_prepare.prepared(app, video)   # adapter returns data, never publishes invalid/partial cache itself


def test_payload_limit_before_session_and_cache_hit_skips_job(app):
    video = next(iter(p1.catalog(app.spec).values()))
    ctx = p1.make_context([video], app.spec, app.fields)
    client = FakeClient()
    with pytest.raises(ValueError, match="payload"):
        remote_compute(ClipB32(), ctx.units, ctx, kernel="owner/test", output_dir=app.output_dir,
                       max_payload_bytes=1, timeout=1, client_factory=lambda: client)
    assert not client.started and not list((app.output_dir / "tmp").iterdir())
    install_clip(app)
    clip_prepare.queue_prepare(app, video.id)
    assert app.clip_prepare_jobs == {}


def test_invalid_result_does_not_publish_cache(app, monkeypatch):
    monkeypatch.setattr(clip_prepare, "dotenv_values", lambda *args: {"RAV_KAGGLE_KERNEL": "owner/test"})
    monkeypatch.setattr(clip_prepare, "remote_compute", lambda f, units, *args, **kwargs:
                        FieldTable(f.key, [u.id for u in units], np.zeros((len(units), 512), np.float32), "vector"))
    video = next(iter(p1.catalog(app.spec).values()))
    app.clip_prepare_jobs[video.id] = {}
    clip_prepare._run(app, video.id)
    assert app.clip_prepare_jobs[video.id]["state"] == "error" and not clip_prepare.prepared(app, video)


def test_duplicate_result_ids_rejected(tmp_path):
    m = {"sample_ids": ["x", "x"]}
    file = tmp_path / "bad.npz"
    np.savez(file, values=basis(2), metadata=json.dumps({"manifest": m, "job_id": "123", "status": "done"}))
    with pytest.raises(ValueError, match="IDs"):
        validate_result(file, m)


def test_auth_only_repo_env_and_missing_settings(app, monkeypatch):
    from src.rav.pipeline import kaggle_gpu, remote_embed

    video = next(iter(p1.catalog(app.spec).values()))
    ctx = p1.make_context([video], app.spec, app.fields)
    monkeypatch.setenv("KAGGLE_API_TOKEN", "wrong-environment-token")
    monkeypatch.setattr(remote_embed, "dotenv_values", lambda *args: {})
    with pytest.raises(ValueError, match=".env repo"):
        remote_compute(ClipB32(), ctx.units, ctx, kernel="owner/test", output_dir=app.output_dir,
                       max_payload_bytes=1 << 20, timeout=1)
    used = []
    fake = FakeClient()
    def factory(*, api_token):
        used.append(api_token)
        return fake
    monkeypatch.setattr(kaggle_gpu, "KaggleGPUClient", factory)
    monkeypatch.setattr(remote_embed, "dotenv_values", lambda *args: {"KAGGLE_API_TOKEN": "repo-token"})
    remote_compute(ClipB32(), ctx.units, ctx, kernel="owner/test", output_dir=app.output_dir,
                   max_payload_bytes=1 << 20, timeout=1)
    assert used == ["repo-token"] and fake.started == ["owner/test"]


@pytest.mark.parametrize("order,status,expected", [("idle_first", "ok", "ok"), ("reply_first", "ok", "ok"),
                                                    ("idle_first", "aborted", "error"), ("missing_reply", "ok", "error")])
def test_official_transport_requires_matching_reply_and_idle(monkeypatch, order, status, expected):
    import websocket

    from src.rav.pipeline.kaggle_gpu import JupyterGPU

    class Socket:
        closed = False
        def send(self, raw):
            request = json.loads(raw)
            def message(kind, channel, content, parent=request["header"]["msg_id"]):
                return json.dumps({"header": {"msg_type": kind}, "parent_header": {"msg_id": parent},
                                   "channel": channel, "content": content})
            unrelated = message("status", "iopub", {"execution_state": "idle"}, parent="other-job")
            idle = message("status", "iopub", {"execution_state": "idle"})
            reply = message("execute_reply", "shell", {"status": status})
            self.messages = [unrelated] + ([idle, reply] if order == "idle_first" else [reply, idle] if order == "reply_first" else [idle]) + [""]
        def recv(self):
            return self.messages.pop(0)
        def settimeout(self, timeout):
            assert timeout > 0
        def close(self):
            self.closed = True
    socket = Socket()
    monkeypatch.setattr(websocket, "create_connection", lambda *args, **kwargs: socket)
    gpu = JupyterGPU("https://private-proxy.invalid/proxy", 1)
    monkeypatch.setattr(gpu, "_request", lambda *args, **kwargs: SimpleNamespace(json=lambda: {"id": "new-kernel"}))
    assert gpu.execute("print('CLIP')", timeout=1)["status"] == expected
    assert socket.closed


def test_remote_source_changed_and_transport_error_are_safe(app):
    video = next(iter(p1.catalog(app.spec).values()))
    ctx = p1.make_context([video], app.spec, app.fields)
    client = FakeClient()
    original = client.get_file
    source = Path(video.uri)
    raw = source.read_bytes()
    def download_and_change(remote, local):
        original(remote, local)
        source.write_bytes(raw + b"changed")
    client.get_file = download_and_change
    try:
        with pytest.raises(ValueError, match="đổi"):
            remote_compute(ClipB32(), ctx.units, ctx, kernel="owner/test", output_dir=app.output_dir,
                           max_payload_bytes=1 << 20, timeout=1, client_factory=lambda: client)
        assert client.stopped == [123]
    finally:
        source.write_bytes(raw)
    bad = FakeClient()
    def fail(*args, **kwargs):
        raise ValueError("https://signed-proxy.invalid/token-secret")
    bad.wait_session = fail
    with pytest.raises(RuntimeError) as error:
        remote_compute(ClipB32(), ctx.units, ctx, kernel="owner/test", output_dir=app.output_dir,
                       max_payload_bytes=1 << 20, timeout=1, client_factory=lambda: bad)
    assert "token-secret" not in str(error.value) and bad.stopped == [123]


@pytest.mark.parametrize("saved_account", [None, "other-account"])
def test_private_worker_quick_save_without_response_ref(tmp_path, saved_account):
    from kagglesdk.kernels.types.kernels_api_service import KernelExecutionType

    from src.rav.pipeline.kaggle_gpu import KaggleGPUClient

    client = object.__new__(KaggleGPUClient)
    client._token = "repo-token"
    seen = []
    def introspect(req):
        assert req.token == "repo-token"
        return SimpleNamespace(active=True, username="owner")
    client._sdk = SimpleNamespace(security=SimpleNamespace(oauth_client=SimpleNamespace(introspect_token=introspect)))
    def save(req):
        seen.append(req)
        assert req.is_private and not req.enable_gpu and req.enable_internet
        assert req.kernel_execution_type == KernelExecutionType.QUICK_SAVE
        notebook = json.loads(req.text)
        assert "repo-token" not in req.text and len(notebook["cells"]) == 1
        return SimpleNamespace(error="", ref="", kernel_id=0)
    def get(req):
        assert req.user_name == "owner" and req.kernel_slug == seen[-1].slug.split("/")[1]
        return SimpleNamespace(metadata=SimpleNamespace(ref=seen[-1].slug, id=123, is_private=True))
    client.kernels = SimpleNamespace(save_kernel=save, get_kernel=get)
    if saved_account:
        (tmp_path / "clip-worker.json").write_text(json.dumps({"account": saved_account, "slug": saved_account + "/private"}))
    slug = client.ensure_kernel(tmp_path)
    saved = json.loads((tmp_path / "clip-worker.json").read_text())
    assert saved["account"] == "owner" and saved["slug"] == slug and saved["private"]
    assert client.ensure_kernel(tmp_path) == slug and len(seen) == 1
    with pytest.raises(ValueError, match="tài khoản"):
        client.ensure_kernel(tmp_path, override="other-account/notebook")
    assert client.ensure_kernel(tmp_path, override="owner/explicit") == "owner/explicit"


def test_jupyter_download_accepts_wrapped_base64_but_rejects_invalid(tmp_path, monkeypatch):
    import binascii

    from src.rav.pipeline.kaggle_gpu import JupyterGPU

    gpu = JupyterGPU("https://private-proxy.invalid/proxy", 1)
    data = {"format": "base64", "content": "Y2xp\ncC1y\r\nZXN1bHQ=\n"}
    monkeypatch.setattr(gpu, "_request", lambda *args, **kwargs: SimpleNamespace(json=lambda: data))
    target = tmp_path / "result"
    gpu.get_file("/kaggle/working/result", target)
    assert target.read_bytes() == b"clip-result"
    data["content"] = "Y2xp#invalid"
    with pytest.raises(binascii.Error):
        gpu.get_file("/kaggle/working/result", target)
    assert target.read_bytes() == b"clip-result"
