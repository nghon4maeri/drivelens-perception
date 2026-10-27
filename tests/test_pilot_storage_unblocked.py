"""Kiểm thử bỏ chặn Storage Quota và Kế toán Dung lượng Độc lập (#138, Lát 1 / CONTRACTS §21.29).

Chạy:
  . .\\scripts\\use_project_storage.ps1
  $env:RAV_PILOT_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:25432/postgres'
  python -m pytest tests/test_pilot_storage_unblocked.py -v --basetemp=output/pytest-tmp
"""
# ruff: noqa: F811

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store import pilot_storage
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_delete import make_handler
from src.rav.store.pilot_runtime import EnqueueSpec, PilotRuntime, WorkerContext, policy_problem
from src.rav.store.pilot_uploads import PilotUploads, UploadIn
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.handlers import make_handlers
from tests.conftest import POLICY_CONFIG, make_video
from tests.test_pilot_uploads import post, up, uploaded  # noqa: F401


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


def test_policy_problem_with_storage_accounting_flag():
    """Kiểm tra chính sách: chấp thuận storage_accounting_enabled=False với hạn mức = 0."""
    valid_cfg = dict(
        POLICY_CONFIG,
        storage_accounting_enabled=False,
        storage_user_bytes=0,
        storage_global_bytes=0,
    )
    assert policy_problem(valid_cfg) is None

    # Sai kiểu boolean
    invalid_type = dict(POLICY_CONFIG, storage_accounting_enabled="false")
    assert policy_problem(invalid_type) == "storage_accounting_enabled phải boolean"

    # Nếu storage_accounting_enabled=True mà hạn mức <= 0 thì lỗi
    invalid_zero = dict(POLICY_CONFIG, storage_accounting_enabled=True, storage_user_bytes=0)
    assert policy_problem(invalid_zero) == "thiếu hoặc sai storage_user_bytes"


def test_storage_reserve_and_settle_unblocked(pg):
    """Khi storage_accounting_enabled=False: reserve vượt xa hạn mức vẫn thành công, account không tăng."""
    cfg = dict(
        POLICY_CONFIG,
        storage_accounting_enabled=False,
        storage_user_bytes=1000,
        storage_global_bytes=1000,
    )
    policy_id = pg.policy(config=cfg)
    uid = pg.profile(policy_id=policy_id)
    rt = PilotRuntime(PilotDB(pg.api_url))
    job = rt.enqueue(
        actor(uid),
        EnqueueSpec(kind="METRICS", resource_id=None, idempotency_key=f"m:{uuid4()}", payload={"metrics": "test"}),
    )

    with psycopg.connect(pg.api_url, autocommit=True) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        # Khai báo giữ 50 GiB (vượt xa 1000 bytes)
        big_bytes = 50 * 1024 * 1024 * 1024
        hold_id = pilot_storage.reserve(cur, uid, cfg, big_bytes, "test:big:hold", job_id=job.id)

        # Kiểm tra hold được tạo kèm flag storage_accounting_enabled = False
        cur.execute("SELECT * FROM rav_pilot_storage_hold WHERE id = %s", (hold_id,))
        hold = cur.fetchone()
        assert hold is not None
        assert hold["reserved_bytes"] == big_bytes
        assert hold["storage_accounting_enabled"] is False

        # Kiểm tra rav_pilot_storage_account không bị ghi nhận reserved_bytes
        cur.execute("SELECT * FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{uid}",))
        account = cur.fetchone()
        assert account is None or account["reserved_bytes"] == 0

        # Settle hold: chuyển sang FINAL
        pilot_storage.settle(cur, hold_id, persist=big_bytes, final=True)

        cur.execute("SELECT * FROM rav_pilot_storage_hold WHERE id = %s", (hold_id,))
        hold_after = cur.fetchone()
        assert hold_after["state"] == "FINAL"
        assert hold_after["persisted_bytes"] == big_bytes

        # Account vẫn không tăng persisted_bytes
        cur.execute("SELECT * FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{uid}",))
        account_after = cur.fetchone()
        assert account_after is None or account_after["persisted_bytes"] == 0


def test_storage_reserve_backward_compatibility(pg):
    """Khi storage_accounting_enabled=True: giữ nguyên hành vi kiểm quota chặt chẽ (429 storage_exceeded)."""
    cfg = dict(
        POLICY_CONFIG,
        storage_accounting_enabled=True,
        storage_user_bytes=1000,
        storage_global_bytes=1000,
    )
    policy_id = pg.policy(config=cfg)
    uid = pg.profile(policy_id=policy_id)
    rt = PilotRuntime(PilotDB(pg.api_url))
    job = rt.enqueue(
        actor(uid),
        EnqueueSpec(kind="METRICS", resource_id=None, idempotency_key=f"m:{uuid4()}", payload={"metrics": "test"}),
    )

    with psycopg.connect(pg.api_url, autocommit=True) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        with pytest.raises(ServiceError) as exc_info:
            pilot_storage.reserve(cur, uid, cfg, 2000, "test:overflow:hold", job_id=job.id)
        assert exc_info.value.code == "storage_exceeded"
        assert exc_info.value.status == 429


def test_job_and_hold_flag_immutability(pg):
    """Cột storage_accounting_enabled trên rav_pilot_job và rav_pilot_storage_hold là bất biến."""
    cfg = dict(
        POLICY_CONFIG,
        storage_accounting_enabled=False,
        storage_user_bytes=0,
        storage_global_bytes=0,
    )
    policy_id = pg.policy(config=cfg)
    uid = pg.profile(policy_id=policy_id)
    rt = PilotRuntime(PilotDB(pg.api_url))

    job = rt.enqueue(
        actor(uid),
        EnqueueSpec(kind="METRICS", resource_id=None, idempotency_key=f"m:{uuid4()}", payload={"metrics": "test"}),
    )

    with psycopg.connect(pg.api_url, autocommit=True) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        # Kiểm tra job có flag False
        cur.execute("SELECT storage_accounting_enabled FROM rav_pilot_job WHERE id = %s", (job.id,))
        assert cur.fetchone()["storage_accounting_enabled"] is False

        # Cố tình đổi storage_accounting_enabled trên rav_pilot_job -> vi phạm trigger
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            cur.execute("UPDATE rav_pilot_job SET storage_accounting_enabled = true WHERE id = %s", (job.id,))
        assert "storage accounting job snapshot immutable" in str(exc.value)

        # Tạo hold và kiểm tra immutability trigger
        hold_id = pilot_storage.reserve(cur, uid, cfg, 500, f"immut:{uuid4()}", job_id=job.id)
        with pytest.raises(psycopg.errors.CheckViolation) as exc:
            cur.execute("UPDATE rav_pilot_storage_hold SET storage_accounting_enabled = true WHERE id = %s", (hold_id,))
        assert "storage accounting hold snapshot immutable" in str(exc.value)


def test_upload_and_delete_with_storage_unblocked(pg, s3_storage, tmp_path):
    """Luồng upload -> validate READY -> delete thành công 100% không bị 409 storage_reconcile_required."""
    cfg = dict(
        POLICY_CONFIG,
        storage_accounting_enabled=False,
        storage_user_bytes=0,
        storage_global_bytes=0,
        staging_grace_ms=1,
    )
    policy_id = pg.policy(config=cfg)
    uid = pg.profile(policy_id=policy_id)
    pg.slot("cpu-1")

    rt_worker = PilotRuntime(PilotDB(pg.worker_url))
    worker_ctx = WorkerContext(worker_id=uuid4(), host_boot_id=uuid4())
    dispatcher = PilotDispatcher(rt_worker, worker_ctx, "CPU", heartbeat_s=0.2)
    for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "work").items():
        dispatcher.register_handler(kind, fn)
    # Đăng ký thêm delete handler
    dispatcher.register_handler("DELETE_SOURCE", make_handler(s3_storage, PilotDB(pg.worker_url)))

    video = make_video(tmp_path / "unblocked_clip.mp4", seconds=2, fps=10)
    data = video.read_bytes()

    uploads_svc = PilotUploads(PilotDB(pg.api_url), lambda: s3_storage)
    created = uploads_svc.create(actor(uid), UploadIn(filename="clip.mp4", declared_bytes=len(data)), "up-unblocked-1")

    # POST file lên S3 và complete
    post(created, data)
    completed = uploads_svc.complete(actor(uid), created.id)
    assert completed.state in ("UPLOADED", "VALIDATING")

    # Worker claim và xử lý VALIDATE_UPLOAD
    assert dispatcher.run_once()

    # Kiểm tra upload đạt READY
    up_ready = uploads_svc.get(actor(uid), created.id)
    assert up_ready.state == "READY"
    assert up_ready.source_id is not None

    # Kiểm tra tài khoản storage vẫn 0 persisted bytes
    with psycopg.connect(pg.api_url) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{uid}",))
        row = cur.fetchone()
        assert row is None or row["persisted_bytes"] == 0

    # Bây giờ thực hiện xóa nguồn (DELETE_SOURCE)
    rt_api = PilotRuntime(PilotDB(pg.api_url))
    del_receipt = rt_api.request_source_delete(actor(uid), up_ready.source_id, 1, "del-unblocked-1")
    assert del_receipt.state == "REQUESTED"

    # Worker claim và xử lý DELETE_SOURCE
    assert dispatcher.run_once()

    # Kiểm tra kết quả xóa: SUCCEEDED mà không bị 409 storage_reconcile_required
    del_final = rt_api.get_source_delete(actor(uid), del_receipt.id)
    assert del_final.state == "SUCCEEDED"


def _storage_policy(env, enabled):
    cfg = dict(POLICY_CONFIG, storage_accounting_enabled=enabled, staging_grace_ms=1)
    policy_id = env["pg"].policy(config=cfg)
    env["pg"].sql("UPDATE rav_pilot_profile SET policy_id=%s WHERE user_id=%s", (policy_id, env["a"]))


def _persisted_accounts(pg):
    return {r["account_key"]: r["persisted_bytes"]
            for r in pg.sql("SELECT account_key, persisted_bytes FROM rav_pilot_storage_account")}


def _delete_and_check(env, source_id, counted_bytes):
    before = _persisted_accounts(env["pg"])
    runtime = PilotRuntime(env["svc"].db)
    dispatcher = env["dispatch"]
    dispatcher.register_handler("DELETE_SOURCE", make_handler(env["s3"], dispatcher.runtime.db))
    req = runtime.request_source_delete(actor(env["a"]), source_id, 1, "mixed-delete")
    assert dispatcher.run_once()
    result = runtime.get_source_delete(actor(env["a"]), req.id)
    assert result.state == "SUCCEEDED", result
    expected = dict(before)
    if counted_bytes:
        for key in ("GLOBAL", f"USER:{env['a']}"):
            expected[key] -= counted_bytes
    assert _persisted_accounts(env["pg"]) == expected
    receipt = env["pg"].sql("SELECT receipt FROM rav_pilot_source_delete WHERE id=%s", (req.id,))[0]["receipt"]
    assert receipt["accounted_released_bytes"] == counted_bytes
    assert runtime.request_source_delete(actor(env["a"]), source_id, 1, "mixed-delete").state == "SUCCEEDED"
    assert not dispatcher.run_once()
    assert _persisted_accounts(env["pg"]) == expected


@pytest.mark.parametrize("source_enabled", [False, True])
@pytest.mark.parametrize("delete_enabled", [False, True])
def test_delete_uses_original_accounting_with_surviving_sources(up, source_enabled, delete_enabled):
    """Deleting new data must preserve existing USER/GLOBAL charges under either policy."""
    uploaded(up, up["video"].read_bytes(), "counted-survivor")
    uploaded(dict(up, a=up["b"]), up["video"].read_bytes(), "other-owner-survivor")
    _storage_policy(up, source_enabled)
    path = make_video(up["tmp"] / "new.mp4", seconds=2, fps=10, shift=37)
    _, ready = uploaded(up, path.read_bytes(), "mixed-new")
    physical = len(path.read_bytes()) + up["pg"].sql(
        "SELECT total_bytes FROM rav_pilot_frame_set WHERE source_id=%s", (ready.source_id,))[0]["total_bytes"]
    _storage_policy(up, delete_enabled)
    _delete_and_check(up, ready.source_id, physical if source_enabled else 0)


@pytest.mark.parametrize("raw_enabled", [False, True])
def test_delete_handles_policy_change_between_create_and_complete(up, raw_enabled):
    """Raw follows the upload hold; frames follow the separate validation job snapshot."""
    uploaded(up, up["video"].read_bytes(), "counted-survivor")
    _storage_policy(up, raw_enabled)
    path = make_video(up["tmp"] / "split.mp4", seconds=2, fps=10, shift=51)
    data = path.read_bytes()
    created = up["svc"].create(actor(up["a"]), UploadIn(filename="split.mp4", declared_bytes=len(data)), "split-upload")
    post(created, data)
    _storage_policy(up, not raw_enabled)
    up["svc"].complete(actor(up["a"]), created.id)
    assert up["dispatch"].run_once()
    ready = up["svc"].get(actor(up["a"]), created.id)
    assert ready.state == "READY"
    frame_bytes = up["pg"].sql("SELECT total_bytes FROM rav_pilot_frame_set WHERE source_id=%s", (ready.source_id,))[0]["total_bytes"]
    _delete_and_check(up, ready.source_id, len(data) if raw_enabled else frame_bytes)


@pytest.mark.parametrize("artifact_enabled", [False, True])
def test_delete_mixed_media_uses_artifact_producer(up, artifact_enabled):
    from src.rav.store.pilot_media import MediaRequestIn, PilotMedia

    uploaded(up, up["video"].read_bytes(), "counted-survivor")
    _storage_policy(up, False)
    path = make_video(up["tmp"] / "media-source.mp4", seconds=2, fps=10, shift=61)
    _, ready = uploaded(up, path.read_bytes(), "media-source")
    src = up["pg"].sql("SELECT * FROM rav_pilot_source_asset WHERE id=%s", (ready.source_id,))[0]
    _storage_policy(up, artifact_enabled)
    up["pg"].slot("media-mixed", "MEDIA")
    media_worker = PilotDispatcher(up["dispatch"].runtime, up["dispatch"].worker_ctx, "MEDIA")
    for kind, fn in make_handlers(up["s3"], media_worker.runtime.db, up["tmp"] / "media-work").items():
        media_worker.register_handler(kind, fn)
    media = PilotMedia(up["svc"].db, lambda: up["s3"])
    req = media.request(actor(up["a"]), MediaRequestIn(source_id=ready.source_id,
                        unit_id=f"{src['core_source_id']}:0.000-0.000", variant="context"))
    assert media_worker.run_once()
    assert media.get(actor(up["a"]), req.id).state == "READY"
    size = up["pg"].sql("SELECT size_bytes FROM rav_pilot_artifact WHERE source_id=%s", (ready.source_id,))[0]["size_bytes"]
    _storage_policy(up, not artifact_enabled)
    _delete_and_check(up, ready.source_id, size if artifact_enabled else 0)


def test_delete_unaccounted_source_does_not_wait_for_global_lock(up):
    from concurrent.futures import ThreadPoolExecutor

    uploaded(up, up["video"].read_bytes(), "counted-survivor")
    _storage_policy(up, False)
    path = make_video(up["tmp"] / "uncounted.mp4", seconds=2, fps=10, shift=71)
    _, ready = uploaded(up, path.read_bytes(), "uncounted-source")
    with psycopg.connect(up["pg"].admin_url) as conn, ThreadPoolExecutor(max_workers=1) as pool:
        conn.execute("SELECT account_key FROM rav_pilot_storage_account WHERE account_key='GLOBAL' FOR UPDATE")
        future = pool.submit(_delete_and_check, up, ready.source_id, 0)
        try:
            future.result(timeout=3)
        finally:
            conn.rollback()


def test_delete_legacy_source_and_frames_without_producer(up):
    from tests.test_pilot_media import seed_frames, seed_source

    uploaded(up, up["video"].read_bytes(), "counted-survivor")
    path = make_video(up["tmp"] / "legacy.mp4", seconds=2, fps=10, shift=81)
    sid, _, frames = seed_source(up["pg"], up["s3"], path, up["a"])
    seed_frames(up["pg"], up["s3"], sid, up["a"], path, frames)
    frame_bytes = up["pg"].sql("SELECT total_bytes FROM rav_pilot_frame_set WHERE source_id=%s", (sid,))[0]["total_bytes"]
    counted = path.stat().st_size + frame_bytes
    up["pg"].sql("UPDATE rav_pilot_storage_account SET persisted_bytes=persisted_bytes+%s "
                 "WHERE account_key=ANY(%s)", (counted, ["GLOBAL", f"USER:{up['a']}"]))
    _storage_policy(up, False)
    _delete_and_check(up, sid, counted)


@pytest.mark.parametrize("source_enabled", [False, True])
def test_delete_after_deduplicated_upload_keeps_original_charge(up, source_enabled):
    uploaded(up, up["video"].read_bytes(), "counted-survivor")
    _storage_policy(up, source_enabled)
    path = make_video(up["tmp"] / "duplicate.mp4", seconds=2, fps=10, shift=91)
    data = path.read_bytes()
    _, first = uploaded(up, data, "first-upload")
    frames = up["pg"].sql("SELECT total_bytes FROM rav_pilot_frame_set WHERE source_id=%s", (first.source_id,))[0]["total_bytes"]
    _storage_policy(up, not source_enabled)
    _, duplicate = uploaded(up, data, "duplicate-upload")
    assert duplicate.source_id == first.source_id
    _delete_and_check(up, first.source_id, len(data) + frames if source_enabled else 0)


def test_counted_source_requires_consistent_ledger_even_when_disabled(up):
    _, ready = uploaded(up, up["video"].read_bytes(), "counted-source")
    up["pg"].sql("UPDATE rav_pilot_storage_account SET persisted_bytes=0 WHERE account_key=%s", (f"USER:{up['a']}",))
    _storage_policy(up, False)
    runtime = PilotRuntime(up["svc"].db)
    dispatcher = up["dispatch"]
    dispatcher.register_handler("DELETE_SOURCE", make_handler(up["s3"], dispatcher.runtime.db))
    req = runtime.request_source_delete(actor(up["a"]), ready.source_id, 1, "inconsistent-ledger")
    before = _persisted_accounts(up["pg"])
    assert dispatcher.run_once()
    result = runtime.get_source_delete(actor(up["a"]), req.id)
    assert result.state == "FAILED"
    assert result.error["code"] == "storage_reconcile_required"
    assert _persisted_accounts(up["pg"]) == before
