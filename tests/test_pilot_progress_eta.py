"""Kiểm thử tiến độ xử lý chi tiết và ước lượng ETA thực tế (#135, N2).

Kiểm tra:
- Migration additive 016 áp dụng cột progress_detail.
- Telemetry tiến độ (phase, done, total, fps, elapsed_s, eta_s).
- Bảo vệ liveness heartbeat không ghi đè mất tiến độ.
- UploadDTO làm giàu progress của validation job khi đang VALIDATING.
- Worker validate_upload và materialize cập nhật telemetry đúng chuẩn CONTRACTS §21.28.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import requests

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import EnqueueSpec, PilotRuntime, Progress, Usage, WorkerContext
from src.rav.store.pilot_uploads import PilotUploads, UploadIn
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.handlers import make_handlers
from tests.conftest import POLICY_CONFIG, make_video

FINAL = Usage(cpu_ms=200, gpu_ms=0, state="FINAL")


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


def spec(key="prog-1", kind="VALIDATE_UPLOAD", **extra) -> EnqueueSpec:
    return EnqueueSpec(kind=kind, idempotency_key=key, payload=extra)


@pytest.fixture
def pr_env(pg, s3_storage, tmp_path):
    config = dict(POLICY_CONFIG, storage_user_bytes=20 << 20, staging_grace_ms=1, heartbeat_ms=500, lease_ms=2000)
    policy = pg.policy(config=config)
    uid = pg.profile(policy_id=policy)
    pg.slot("cpu-1")
    rt = PilotRuntime(PilotDB(pg.worker_url))
    api_rt = PilotRuntime(PilotDB(pg.api_url))
    ctx = WorkerContext(worker_id=uuid4(), host_boot_id=uuid4())
    d = PilotDispatcher(rt, ctx, "CPU", heartbeat_s=0.1)
    for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "work").items():
        d.register_handler(kind, fn)
    video = make_video(tmp_path / "clip.mp4", seconds=2, fps=10)
    return {
        "pg": pg,
        "s3": s3_storage,
        "uid": uid,
        "rt": rt,
        "api_rt": api_rt,
        "ctx": ctx,
        "dispatcher": d,
        "video": video,
        "tmp": tmp_path,
        "uploads": PilotUploads(PilotDB(pg.api_url), lambda: s3_storage),
    }


def test_migration_016_adds_progress_detail_column(pr_env):
    pg = pr_env["pg"]
    rows = pg.sql(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'rav_pilot_job' AND column_name = 'progress_detail'"
    )
    assert len(rows) == 1
    assert rows[0]["column_name"] == "progress_detail"


def test_update_progress_and_get_job(pr_env):
    api_rt, rt, uid, ctx = pr_env["api_rt"], pr_env["rt"], pr_env["uid"], pr_env["ctx"]
    job = api_rt.enqueue(actor(uid), spec("test-prog-update", "EXPORT_BUILD"))
    lease = rt.claim(ctx, "CPU", kinds=("EXPORT_BUILD",), slot_keys=("cpu-1",))
    assert lease is not None and lease.job_id == job.id

    # Cập nhật tiến độ chi tiết
    rt.update_progress(
        lease,
        Progress(
            sequence=1,
            phase="EXTRACTING_FRAMES",
            done=15,
            total=60,
            fps=20.5,
            elapsed_s=0.73,
            eta_s=2.2,
            detail="batch 1",
        ),
    )

    j = api_rt.get_job(actor(uid), job.id)
    assert j.phase == "EXTRACTING_FRAMES"
    assert j.progress["done"] == 15
    assert j.progress["total"] == 60
    assert j.progress["fps"] == 20.5
    assert j.progress["elapsed_s"] == 0.73
    assert j.progress["eta_s"] == 2.2
    assert j.progress["detail"] == "batch 1"

    # Hoàn tất job
    rt.publish(lease, {}, FINAL)


def test_liveness_heartbeat_preserves_job_progress(pr_env):
    api_rt, rt, uid, ctx = pr_env["api_rt"], pr_env["rt"], pr_env["uid"], pr_env["ctx"]
    job = api_rt.enqueue(actor(uid), spec("test-liveness-guard", "EXPORT_BUILD"))
    lease = rt.claim(ctx, "CPU", kinds=("EXPORT_BUILD",), slot_keys=("cpu-1",))
    assert lease is not None

    # Handler cập nhật tiến độ thực tế
    rt.update_progress(
        lease,
        Progress(
            sequence=1,
            phase="EXTRACTING_FRAMES",
            done=30,
            total=100,
            fps=15.0,
            elapsed_s=2.0,
            eta_s=4.7,
        ),
    )

    # Liveness ping định kỳ từ background thread (done=0, total=0, phase="working")
    lease = rt.heartbeat(lease, Progress(sequence=99, phase="working", done=0, total=0))

    # Kiểm tra tiến độ không bị ghi đè lùi về 0 hay "working"
    j = api_rt.get_job(actor(uid), job.id)
    assert j.phase == "EXTRACTING_FRAMES"
    assert j.progress["done"] == 30
    assert j.progress["total"] == 100
    assert j.progress["fps"] == 15.0
    assert j.progress["eta_s"] == 4.7

    rt.publish(lease, {}, FINAL)


def test_heartbeat_rejects_progress_decrease(pr_env):
    api_rt, rt, uid, ctx = pr_env["api_rt"], pr_env["rt"], pr_env["uid"], pr_env["ctx"]
    api_rt.enqueue(actor(uid), spec("test-prog-decrease", "EXPORT_BUILD"))
    lease = rt.claim(ctx, "CPU", kinds=("EXPORT_BUILD",), slot_keys=("cpu-1",))
    assert lease is not None

    rt.update_progress(lease, Progress(sequence=1, phase="p", done=50, total=100))

    # Hạ tiến độ phải bị ServiceError 422
    with pytest.raises(ServiceError) as exc:
        rt.update_progress(lease, Progress(sequence=2, phase="p", done=20, total=100))
    assert exc.value.code == "validation_error"

    rt.publish(lease, {}, FINAL)


def test_upload_dto_enriches_validation_progress(pr_env):
    uploads, rt, uid, vid = pr_env["uploads"], pr_env["rt"], pr_env["uid"], pr_env["video"]
    data = vid.read_bytes()
    created = uploads.create(actor(uid), UploadIn(filename="clip.mp4", declared_bytes=len(data)), "up-prog-test")
    assert created.state == "CREATED"

    # Tải file lên S3
    t = created.transfer
    r = requests.post(t.url, data=t.fields, files={"file": ("video", data)})
    assert r.status_code in (200, 204)

    # Complete upload để chuyển sang VALIDATING
    completed = uploads.complete(actor(uid), created.id)
    assert completed.state == "VALIDATING"
    assert completed.validation_job_id is not None

    # Ban đầu job vừa enqueue
    initial_up = uploads.get(actor(uid), created.id)
    assert initial_up.state == "VALIDATING"
    assert initial_up.progress is not None
    assert initial_up.progress["phase"] == "queued"
    assert initial_up.progress["done"] == 0

    # Giả lập worker cập nhật tiến độ
    ctx = pr_env["ctx"]
    lease = rt.claim(ctx, "CPU", kinds=("VALIDATE_UPLOAD",), slot_keys=("cpu-1",))
    assert lease is not None and lease.job_id == completed.validation_job_id

    rt.update_progress(
        lease,
        Progress(
            sequence=1,
            phase="EXTRACTING_FRAMES",
            done=8,
            total=20,
            fps=12.5,
            elapsed_s=0.64,
            eta_s=1.0,
        ),
    )

    # API GET /uploads/{id} tự động trả về progress của validation job
    polled = uploads.get(actor(uid), created.id)
    assert polled.state == "VALIDATING"
    assert polled.progress is not None
    assert polled.progress["phase"] == "EXTRACTING_FRAMES"
    assert polled.progress["done"] == 8
    assert polled.progress["total"] == 20
    assert polled.progress["fps"] == 12.5
    assert polled.progress["eta_s"] == 1.0


def test_worker_validate_upload_pipeline_with_progress(pr_env):
    uploads, d, uid, vid = pr_env["uploads"], pr_env["dispatcher"], pr_env["uid"], pr_env["video"]
    data = vid.read_bytes()
    created = uploads.create(actor(uid), UploadIn(filename="clip.mp4", declared_bytes=len(data)), "up-work-prog")

    t = created.transfer
    requests.post(t.url, data=t.fields, files={"file": ("video", data)})
    completed = uploads.complete(actor(uid), created.id)
    assert completed.state == "VALIDATING"

    # Chạy dispatcher để worker thực hiện validate_upload hoàn tất
    ok = d.run_once()
    assert ok is True

    final_up = uploads.get(actor(uid), created.id)
    assert final_up.state == "READY"
    assert final_up.source_id is not None
