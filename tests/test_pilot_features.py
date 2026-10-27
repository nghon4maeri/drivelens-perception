"""DEP5a chuẩn bị đặc trưng (CONTRACTS §21.14, #94): PostgreSQL thật + S3 moto + Kaggle giả chạy `s3_clip_worker` thật trên máy.

Kaggle giả: một notebook/slot, trạng thái session điều khiển được; embedding giả (CUDA không cần) nhưng manifest, pixel check,
progress/result qua URL presigned, `validate_result`, artifact NPZ, gpu_hold, launch state và supervisor đều là code thật.
Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_features.py -q`.
"""

from __future__ import annotations

import io
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from src.rav.errors import ServiceError
from src.rav.pipeline import s3_clip_worker as remote_worker
from src.rav.store import pilot_gpu
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_features import PilotFeatures, PreparationIn, input_hash
from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.gpu_handlers import make_gpu_handlers
from src.rav.worker.kaggle_adapter import KaggleSlotAdapter
from src.rav.worker.supervisor import PilotSupervisor
from tests.conftest import POLICY_CONFIG, make_video
from tests.test_pilot_media import actor, seed_source

ACCOUNT = "kag"
HOUR = 3_600_000


class FakeKaggle:
    """Provider giả: notebook theo slot, trạng thái session, quota; `execute` chạy worker từ xa thật trên máy."""

    def __init__(self, root: Path):
        self.root, self.status, self.mode = root, "NEW_SCRIPT", "ok"
        self.created, self.stopped, self.kernels = [], [], set()
        self.embed = lambda images: _unit_rows(len(images), self.dim)
        self.dim, self.used_ms = 512, 0
        root.mkdir(parents=True, exist_ok=True)

    def account(self):
        return ACCOUNT

    def ensure_named_kernel(self, slug):
        self.kernels.add(slug)
        return slug

    def session_status(self, slug):
        if self.mode == "status_error":
            raise RuntimeError("provider down")
        return self.status

    def create_session(self, slug, **kwargs):
        self.status = "RUNNING"
        if self.mode == "create_error":
            raise TimeoutError("lost ack")
        self.created.append(slug)
        return SimpleNamespace(session_id=7, operation_name="operations/create-kernel-session/7", tools=None)

    def wait_session(self, session, timeout):
        session.tools = self

    def put_file(self, local, remote):
        shutil.copyfile(local, self.root / Path(remote).name)

    def execute(self, code, timeout):
        if "subprocess.Popen" in code and self.mode != "hang":
            remote_worker.run(self.root / "config.json", embed_batch=self.embed)
        return {"status": "ok"}

    def stop_session(self, session_id):
        self.stopped.append(session_id)
        if self.mode != "stop_stuck":
            self.status = "CANCEL_ACKNOWLEDGED"

    def quota_statistics(self):
        return {"total_ms": 30 * HOUR, "used_ms": self.used_ms, "reserved_ms": 0,
                "refresh_time": datetime.now(UTC) + timedelta(days=3)}


def _unit_rows(n, dim):
    v = np.ones((n, dim), np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_gpu_session_lookup_reads_real_schema(ft, monkeypatch):
    from src.rav.pipeline import kaggle_gpu
    from src.rav.worker.__main__ import gpu_adapters

    monkeypatch.setenv('KAGGLE_API_TOKEN', 'test-token')
    monkeypatch.setattr(kaggle_gpu, 'KaggleGPUClient', lambda **kwargs: ft.kaggle)
    ft.pg.sql("UPDATE rav_pilot_resource_slot SET reconcile_evidence=%s::jsonb WHERE slot_key='gpu-1'",
              ('{"verified_session_id":"123"}',))
    assert gpu_adapters(ft.d.runtime.db)['gpu-1'].session_lookup() == '123'


@pytest.fixture
def ft(pg, s3_storage, tmp_path):
    policy = pg.policy(config=dict(POLICY_CONFIG, stop_deadline_ms=2_000, gpu_base_ms=HOUR,
                                   profiles_by_kind={**POLICY_CONFIG["profiles_by_kind"],
                                                     "PREPARE_CLIP": {"cpu_ms": 1_000, "gpu_ms": 120_000},
                                                     "PREPARE_DINO": {"cpu_ms": 1_000, "gpu_ms": 120_000}}))
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    pg.slot("gpu-1", "GPU", ACCOUNT)
    wdb = PilotDB(pg.worker_url)
    runtime = PilotRuntime(wdb)
    ctx = WorkerContext(worker_id=uuid4(), host_boot_id=uuid4())
    kaggle = FakeKaggle(tmp_path / "kaggle")
    adapters = {"gpu-1": KaggleSlotAdapter(kaggle, ACCOUNT, "gpu-1")}
    d = PilotDispatcher(runtime, ctx, "GPU", heartbeat_s=0.2)
    for kind, fn in make_gpu_handlers(s3_storage, wdb, tmp_path / "work", adapters, deployment="test", poll_s=0.05).items():
        d.register_handler(kind, fn)
    sup = PilotSupervisor(runtime, ctx, SimpleNamespace(storage=lambda: s3_storage, sweep_staging=lambda: []), gpu=adapters)
    path = make_video(tmp_path / "v.mp4", seconds=2, fps=10, size=(64, 48))
    sid, core, frames = seed_source(pg, s3_storage, path, a)
    api = PilotDB(pg.api_url)
    return SimpleNamespace(pg=pg, s3=s3_storage, a=a, b=b, sid=sid, core=core, frames=frames, d=d, sup=sup, kaggle=kaggle,
                           features=PilotFeatures(api), api=api, runtime=runtime, tmp=tmp_path, policy=policy)


def _prepare(ft, feature="CLIP", key="k1", who=None):
    return ft.features.prepare(actor(who or ft.a), ft.sid, PreparationIn(feature=feature), key)


def _attempt(ft):
    return ft.pg.sql("SELECT * FROM rav_pilot_job_attempt ORDER BY created_at DESC LIMIT 1")[0]


def _expire_lease(ft):
    ft.pg.sql("UPDATE rav_pilot_job_attempt SET lease_until = now() - interval '1 second' WHERE state = 'WORKING'")


def test_clip_prepare_end_to_end_with_budget(ft):
    dto, status = _prepare(ft)
    assert status == 202 and dto.job.state == "QUEUED" and not dto.ready
    assert ft.api.get_source(actor(ft.a), ft.sid).clip.job_id == dto.job.id
    # Chưa có snapshot quota → không launch, job chờ ngân sách được xác minh.
    assert ft.d.run_once() is False and not ft.kaggle.created
    job = ft.pg.sql("SELECT state, waiting_reason FROM rav_pilot_job")[0]
    assert job == {"state": "WAITING_RESOURCE", "waiting_reason": "gpu_budget_unverified"}
    ft.sup.refresh_gpu_budget()
    assert ft.d.run_once() is True
    job = ft.pg.sql("SELECT * FROM rav_pilot_job")[0]
    assert job["state"] == "SUCCEEDED", job["error"]
    art = ft.pg.sql("SELECT * FROM rav_pilot_artifact WHERE kind = 'FEATURE'")[0]
    assert art["state"] == "READY" and art["variant"] == "CLIP" and art["owner_id"] == ft.a and job["result_ref"] == art["id"]
    src = ft.pg.sql("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (ft.sid,))[0]
    assert art["input_hash"] == input_hash(src, "CLIP") and art["manifest"]["n_units"] == len(ft.frames)
    # NPZ đúng ordered IDs của decoder + L2 (khớp local).
    body = ft.s3.client.get_object(Bucket=ft.s3.bucket, Key=art["object_key"])["Body"].read()
    with np.load(io.BytesIO(body), allow_pickle=False) as z:
        assert list(z["ids"]) == [f"{ft.core}:{f.t:.3f}-{f.t:.3f}" for f in ft.frames]
        assert z["emb.clip_b32"].shape == (len(ft.frames), 512)
        assert np.allclose(np.linalg.norm(z["emb.clip_b32"], axis=1), 1, atol=1e-5)
    att = _attempt(ft)
    assert att["launch_state"] == "STOPPED" and att["session_stop_verified"] and att["session_id"] == "7"
    assert att["launch_marker"] == f"pilot:test:{job['id']}:{att['id']}:{att['fence']}"
    hold = ft.pg.sql("SELECT * FROM rav_pilot_gpu_hold")[0]
    receipt = ft.pg.sql("SELECT actual_ms FROM rav_pilot_usage_receipt WHERE resource = 'GPU'")[0]
    assert hold["state"] == "FINAL" and hold["consumed_ms"] == receipt["actual_ms"] > 0
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot WHERE slot_key = 'gpu-1'")[0]["state"] == "FREE"
    # Object trao đổi với Kaggle đã xóa theo tên cụ thể; readiness đọc từ artifact; gọi lại → 200 không job mới.
    for name in ("progress.json", "result.npz"):
        assert ft.s3.head(ft.s3.job_key(job["id"], att["id"], name)) is None
    assert ft.api.get_source(actor(ft.a), ft.sid).clip.ready
    again, status = _prepare(ft, key="k2")
    assert status == 200 and again.ready and again.job is None
    assert ft.pg.sql("SELECT count(*) AS n FROM rav_pilot_job")[0]["n"] == 1


def test_dino_prepare_publishes_vectors_and_phash(ft, monkeypatch, tmp_path):
    weights = tmp_path / "w.pth"
    weights.write_bytes(b"weights")
    runtime = {"opencv": "4.x", "imagehash": "4.y", "scipy": "1.z"}
    monkeypatch.setattr("src.rav.pipeline.dino_assets.prepare_assets",
                        lambda storage, out: ({"weights": storage.upload_raw(weights, "models/dinov2_s")}, runtime))
    names = {"opencv-python-headless": "4.x", "ImageHash": "4.y", "scipy": "1.z"}
    monkeypatch.setattr("importlib.metadata.version", lambda package: names[package])
    ft.kaggle.dim = 384
    ft.sup.refresh_gpu_budget()
    _prepare(ft, "DINO")
    assert ft.d.run_once()
    job = ft.pg.sql("SELECT state, error FROM rav_pilot_job")[0]
    assert job["state"] == "SUCCEEDED", job["error"]
    art = ft.pg.sql("SELECT * FROM rav_pilot_artifact WHERE kind = 'FEATURE'")[0]
    assert art["variant"] == "DINO" and art["manifest"]["fields"] == ["emb.dinov2_s", "hash.phash"]
    body = ft.s3.client.get_object(Bucket=ft.s3.bucket, Key=art["object_key"])["Body"].read()
    with np.load(io.BytesIO(body), allow_pickle=False) as z:
        assert z["emb.dinov2_s"].shape == (len(ft.frames), 384) and z["hash.phash"].dtype == np.uint64
    # Asset model content-addressed dưới system/models, không trong private.
    assert ft.s3.head(ft.s3.system_model_key("dinov2_s", __import__("hashlib").sha256(b"weights").hexdigest(), ".pth"))
    dto = ft.api.get_source(actor(ft.a), ft.sid)
    assert dto.dino.ready and not dto.clip.ready


def test_budget_exhausted_waits_and_scope_isolation(ft):
    ft.kaggle.used_ms = 30 * HOUR - 100_000   # còn 100 s < 120 s ước lượng + headroom
    ft.sup.refresh_gpu_budget()
    _prepare(ft)
    assert ft.d.run_once() is False and not ft.kaggle.created
    assert ft.pg.sql("SELECT waiting_reason FROM rav_pilot_job")[0]["waiting_reason"] == "gpu_budget_exhausted"
    with pytest.raises(ServiceError) as e:
        _prepare(ft, who=ft.b)
    assert e.value.status == 404
    with pytest.raises(ServiceError):
        ft.api.get_source(actor(ft.b), ft.sid)
    # Snapshot mới đủ ngân sách → job chờ được claim lại.
    ft.kaggle.used_ms = 0
    ft.sup.refresh_gpu_budget()
    assert ft.d.run_once() and ft.pg.sql("SELECT state FROM rav_pilot_job")[0]["state"] == "SUCCEEDED"


def test_system_source_needs_operator_import(ft):
    sys_sid, _, _ = seed_source(ft.pg, ft.s3, make_video(ft.tmp / "s.mp4", seconds=1, shift=40), None)
    with pytest.raises(ServiceError) as e:
        ft.features.prepare(actor(ft.a), sys_sid, PreparationIn(feature="DINO"), "s1")
    assert e.value.code == "system_feature_missing" and e.value.status == 409
    src = ft.pg.sql("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (sys_sid,))[0]
    ft.pg.sql("INSERT INTO rav_pilot_artifact (id, scope, source_id, kind, variant, input_hash, object_key, sha256, size_bytes, state) "
              "VALUES (%s, 'SYSTEM', %s, 'FEATURE', 'DINO', %s, 'pilot-test/system/t/features/x', %s, 10, 'READY')",
              (uuid4(), sys_sid, input_hash(src, "DINO"), "b" * 64))
    dto, status = ft.features.prepare(actor(ft.b), sys_sid, PreparationIn(feature="DINO"), "s2")
    assert status == 200 and dto.ready
    assert ft.api.get_source(actor(ft.b), sys_sid).dino.ready
    assert ft.pg.sql("SELECT count(*) AS n FROM rav_pilot_job")[0]["n"] == 0


def test_slot_busy_on_provider_never_launches_second_session(ft):
    ft.kaggle.status = "RUNNING"   # session lạ/đang chạy trên notebook của slot
    ft.sup.refresh_gpu_budget()
    _prepare(ft)
    assert ft.d.run_once()
    assert not ft.kaggle.created and _attempt(ft)["launch_state"] == "NONE"
    _expire_lease(ft)
    ft.sup.run_reconciliation()
    # launch NONE + handler đã dừng → không có session của app; slot được trả, job chạy lại.
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"
    assert ft.pg.sql("SELECT state FROM rav_pilot_job")[0]["state"] == "QUEUED"


def test_launch_uncertain_keeps_slot_until_provider_verified(ft):
    ft.kaggle.mode = "create_error"
    ft.sup.refresh_gpu_budget()
    _prepare(ft)
    assert ft.d.run_once()
    att = _attempt(ft)
    assert att["launch_state"] == "LAUNCH_UNCERTAIN" and att["session_id"] is None
    _expire_lease(ft)
    ft.kaggle.mode = "status_error"           # provider không trả lời → UNKNOWN, không đoán ABSENT
    ft.sup.run_reconciliation()
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "QUARANTINED"
    ft.kaggle.mode, ft.kaggle.status = "ok", "RUNNING"   # FOUND nhưng không có handle → vẫn giữ
    ft.sup.run_reconciliation()
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "QUARANTINED" and not ft.kaggle.stopped
    ft.kaggle.status = "COMPLETE"              # session tự kết thúc → ABSENT_VERIFIED
    ft.sup.run_reconciliation()
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"
    att = ft.pg.sql("SELECT * FROM rav_pilot_job_attempt WHERE id = %s", (att["id"],))[0]
    assert att["launch_state"] == "STOPPED" and att["usage_state"] == "FINAL"
    # GPU không đo được → tính trọn phần đã giữ; gpu_hold FINAL.
    assert ft.pg.sql("SELECT state FROM rav_pilot_gpu_hold WHERE attempt_id = %s", (att["id"],))[0]["state"] == "FINAL"
    assert ft.pg.sql("SELECT actual_ms FROM rav_pilot_usage_receipt WHERE resource = 'GPU'")[0]["actual_ms"] > 0


def test_unverified_stop_detaches_then_supervisor_stops_session(ft):
    ft.kaggle.mode = "stop_stuck"
    ft.sup.refresh_gpu_budget()
    _prepare(ft)
    assert ft.d.run_once()
    att = _attempt(ft)
    assert att["launch_state"] == "STOP_PENDING" and att["usage"]["gpu_ms"] > 0
    assert ft.pg.sql("SELECT count(*) AS n FROM rav_pilot_artifact")[0]["n"] == 0
    _expire_lease(ft)
    ft.sup.run_reconciliation()                # FOUND + handle → supervisor hủy session, chưa trả slot
    assert [str(s) for s in ft.kaggle.stopped] == ["7", "7"]
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "QUARANTINED"
    # Session còn chạy sau khi handler tách: supervisor phải tính tới stop verified,
    # không chốt bằng số ms cũ mà handler đã ghi.
    ft.pg.sql("UPDATE rav_pilot_job_attempt SET process_group = process_group || "
              "jsonb_build_object('gpu_started_at', now() - interval '5 seconds') WHERE id=%s", (att["id"],))
    ft.kaggle.status = "CANCEL_ACKNOWLEDGED"
    ft.sup.run_reconciliation()
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"
    gpu = ft.pg.sql("SELECT actual_ms FROM rav_pilot_usage_receipt WHERE resource = 'GPU'")[0]["actual_ms"]
    assert gpu >= att["usage"]["gpu_ms"]
    assert gpu >= 5000


def test_exchange_delete_failure_keeps_slot_until_cleanup(ft, monkeypatch):
    ft.sup.refresh_gpu_budget()
    _prepare(ft)
    original = ft.s3.delete
    def failed(key):
        if key.endswith("progress.json"):
            raise ServiceError("dependency_unavailable", "S3 unavailable", 503)
        return original(key)
    monkeypatch.setattr(ft.s3, "delete", failed)
    assert ft.d.run_once()
    assert ft.pg.sql("SELECT count(*) AS n FROM rav_pilot_artifact")[0]["n"] == 0
    _expire_lease(ft)
    ft.sup.run_reconciliation()
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "QUARANTINED"
    assert ft.pg.sql("SELECT count(*) AS n FROM rav_pilot_usage_receipt")[0]["n"] == 0
    monkeypatch.setattr(ft.s3, "delete", original)
    ft.sup.run_reconciliation()
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"


def test_invalid_remote_result_fails_without_artifact_and_charges_gpu(ft):
    ft.kaggle.embed = lambda images: np.full((len(images), 512), 3.0, np.float32)   # worker tự chuẩn hóa → hợp lệ
    ft.kaggle.dim = 512
    ft.sup.refresh_gpu_budget()
    original = remote_worker.run

    def corrupt(config_path, **kwargs):   # kết quả không L2 → validate_result từ chối
        orig_savez = np.savez

        def bad_savez(path, values, **kw):
            orig_savez(path, values=values * 2, **kw)
        np.savez = bad_savez
        try:
            original(config_path, **kwargs)
        finally:
            np.savez = orig_savez
    remote_worker.run, saved = corrupt, original
    try:
        _prepare(ft)
        assert ft.d.run_once()
    finally:
        remote_worker.run = saved
    job = ft.pg.sql("SELECT state, error FROM rav_pilot_job")[0]
    assert job["state"] == "FAILED" and job["error"]["code"] == "gpu_job_failed"
    assert ft.pg.sql("SELECT count(*) AS n FROM rav_pilot_artifact")[0]["n"] == 0
    assert ft.kaggle.stopped == [7] and _attempt(ft)["launch_state"] == "STOPPED"
    assert ft.pg.sql("SELECT actual_ms FROM rav_pilot_usage_receipt WHERE resource = 'GPU'")[0]["actual_ms"] > 0
    assert ft.pg.sql("SELECT state FROM rav_pilot_resource_slot")[0]["state"] == "FREE"


def test_cancel_while_remote_running_stops_session(ft):
    ft.kaggle.mode = "hang"                    # worker từ xa không bao giờ xong
    ft.sup.refresh_gpu_budget()
    dto, _ = _prepare(ft)
    runner = threading.Thread(target=ft.d.run_once)
    runner.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not ft.pg.sql(
            "SELECT 1 FROM rav_pilot_job_attempt WHERE launch_state = 'ATTACHED'"):
        time.sleep(0.05)
    job = ft.pg.sql("SELECT state_version FROM rav_pilot_job")[0]
    ft.runtime.__class__(PilotDB(ft.pg.api_url)).cancel(actor(ft.a), dto.job.id, job["state_version"])
    runner.join(timeout=30)
    assert not runner.is_alive()
    assert ft.pg.sql("SELECT state FROM rav_pilot_job")[0]["state"] == "CANCELLED"
    assert ft.kaggle.stopped == [7] and _attempt(ft)["launch_state"] == "STOPPED"
    assert ft.pg.sql("SELECT actual_ms FROM rav_pilot_usage_receipt WHERE resource = 'GPU'")[0]["actual_ms"] > 0


def test_snapshot_and_gpu_hold_are_guarded(ft):
    ft.sup.refresh_gpu_budget()
    snap = ft.pg.sql("SELECT * FROM rav_pilot_gpu_budget_snapshot")[0]
    assert snap["remaining_ms"] == 30 * HOUR and snap["source"] == "KAGGLE_SDK" and snap["operator_id"] is None
    with pytest.raises(Exception):
        ft.pg.sql("UPDATE rav_pilot_gpu_budget_snapshot SET remaining_ms = 1")
    with pytest.raises(ServiceError):
        pilot_gpu.record_snapshot(PilotDB(ft.pg.worker_url), provider_account=ACCOUNT, source="KAGGLE_SDK", remaining_ms=-1,
                                  reset_at=None, evidence_ref="x", evidence={})
    # API không ghi được snapshot/hold (chỉ worker).
    import psycopg
    with psycopg.connect(ft.pg.api_url) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("INSERT INTO rav_pilot_gpu_budget_snapshot (id, provider_account, source, observed_at, remaining_ms, "
                  "evidence_ref) VALUES (%s, 'x', 'KAGGLE_SDK', now(), 1, 'x')", (uuid4(),))
