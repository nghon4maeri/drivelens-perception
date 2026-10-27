"""Upload trực tiếp pilot (DEP4 #91): Postgres thật + S3 moto + worker VALIDATE_UPLOAD với video thật.

Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_uploads.py -q`.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
import requests

from src.rav.actor import ActorContext
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
from src.rav.store.pilot_uploads import PilotUploads, UploadIn
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.handlers import make_handlers
from tests.conftest import POLICY_CONFIG, make_video


def actor(uid) -> ActorContext:
    return ActorContext(user_id=uid)


@pytest.fixture
def up(pg, s3_storage, tmp_path):
    config = dict(POLICY_CONFIG, storage_user_bytes=10 << 20, staging_grace_ms=1)
    policy = pg.policy(config=config)
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    pg.slot("cpu-1")
    worker = PilotRuntime(PilotDB(pg.worker_url))
    d = PilotDispatcher(worker, WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "CPU", heartbeat_s=0.2)
    for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "work").items():
        d.register_handler(kind, fn)
    video = make_video(tmp_path / "clip.mp4", seconds=4, fps=10)
    return {"pg": pg, "s3": s3_storage, "a": a, "b": b, "svc": PilotUploads(PilotDB(pg.api_url), lambda: s3_storage),
            "dispatch": d, "video": video, "tmp": tmp_path}


def post(created, data: bytes) -> None:
    t = created.transfer
    r = requests.post(t.url, data=t.fields, files={"file": ("video", data)})
    assert r.status_code in (200, 204), r.text


def account(pg, uid):
    return pg.sql("SELECT persisted_bytes, reserved_bytes FROM rav_pilot_storage_account WHERE account_key = %s",
                  (f"USER:{uid}",))[0]


def test_pipeline_storage_error_rolls_back_both_accounts_and_hold(up):
    import psycopg

    from src.rav.store.pilot_storage import settle

    pg, uid = up["pg"], up["a"]
    created = up["svc"].create(actor(uid), UploadIn(filename="clip.mp4", declared_bytes=1000), "pipeline-rollback")
    hold = pg.sql("SELECT * FROM rav_pilot_storage_hold WHERE upload_id=%s ORDER BY id", (created.id,))[0]
    accounts = pg.sql("SELECT * FROM rav_pilot_storage_account ORDER BY account_key")
    pg.sql("CREATE FUNCTION test_reject_storage() RETURNS trigger LANGUAGE plpgsql AS $$ "
           "BEGIN RAISE check_violation USING MESSAGE='injected'; END $$")
    pg.sql("CREATE TRIGGER test_reject_storage BEFORE UPDATE ON rav_pilot_storage_hold "
           "FOR EACH ROW EXECUTE FUNCTION test_reject_storage()")
    db = up["svc"].db
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.backend() as cur:
                settle(cur, hold["id"], persist=500, final=True)
    finally:
        pg.sql("DROP TRIGGER test_reject_storage ON rav_pilot_storage_hold")
        pg.sql("DROP FUNCTION test_reject_storage()")
    assert pg.sql("SELECT * FROM rav_pilot_storage_account ORDER BY account_key") == accounts
    assert pg.sql("SELECT * FROM rav_pilot_storage_hold WHERE id=%s", (hold["id"],))[0] == hold
    with db.backend() as cur:
        settle(cur, hold["id"], persist=500, final=True)
        settle(cur, hold["id"], persist=500, final=True)
    assert account(pg, uid)["persisted_bytes"] == 500


def frame_bytes(pg) -> int:
    return pg.sql("SELECT coalesce(sum(total_bytes), 0)::bigint AS n FROM rav_pilot_frame_set WHERE state = 'READY'")[0]["n"]


def uploaded(u, data: bytes, key: str):
    svc, a = u["svc"], u["a"]
    created = svc.create(actor(a), UploadIn(filename="clip.mp4", declared_bytes=len(data)), key)
    post(created, data)
    dto = svc.complete(actor(a), created.id)
    assert dto.state == "VALIDATING" and dto.validation_job_id
    assert u["dispatch"].run_once()
    return created, svc.get(actor(a), created.id)


def test_create_reserves_and_signs_staging_only(up):
    svc, a, pg = up["svc"], up["a"], up["pg"]
    created = svc.create(actor(a), UploadIn(filename="../../evil name.mp4", declared_bytes=1000), "k1")
    assert created.state == "CREATED" and created.transfer.method == "POST"
    assert created.transfer.fields["key"] == f"pilot-test/staging/{a}/{created.id}/video"
    assert account(pg, a)["reserved_bytes"] == 2000
    row = pg.sql("SELECT filename_display FROM rav_pilot_upload WHERE id = %s", (created.id,))[0]
    assert row["filename_display"] == "evil name.mp4"   # chỉ tên hiển thị, bỏ đường dẫn
    assert svc.create(actor(a), UploadIn(filename="../../evil name.mp4", declared_bytes=1000), "k1").id == created.id
    with pytest.raises(ServiceError) as e:
        svc.create(actor(a), UploadIn(filename="x.mp4", declared_bytes=2000), "k1")
    assert e.value.code == "idempotency_conflict"
    with pytest.raises(ServiceError) as e:
        svc.create(actor(a), UploadIn(filename="x.mp4", declared_bytes=POLICY_CONFIG["max_video_bytes"] + 1), "k2")
    assert e.value.code == "validation_error"
    with pytest.raises(ServiceError) as e:
        svc.create(actor(a), UploadIn(filename="x.mp4", declared_bytes=6 << 20), "k3")   # 2 × 6 MiB > 10 MiB
    assert (e.value.code, e.value.status) == ("storage_exceeded", 429)


def test_missing_s3_config_is_503(pg):
    from src.rav.store.pilot_storage import PilotStorage

    uid = pg.profile(policy_id=pg.policy())
    svc = PilotUploads(PilotDB(pg.api_url), lambda: PilotStorage(object(), "bucket", ""))
    with pytest.raises(ServiceError) as e:
        svc.create(actor(uid), UploadIn(filename="x.mp4", declared_bytes=10), "k")
    assert (e.value.code, e.value.status) == ("dependency_unavailable", 503)


def test_full_flow_creates_private_source_with_core_parity(up):
    from src.rav.worker.handlers import _decoder, _probe

    pg, a, s3 = up["pg"], up["a"], up["s3"]
    data = up["video"].read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    created, dto = uploaded(up, data, "u1")
    assert dto.state == "READY" and dto.source_id
    src = pg.sql("SELECT * FROM rav_pilot_source_asset WHERE id = %s", (dto.source_id,))[0]
    probe = _probe(up["video"])
    assert (src["scope"], src["owner_id"], src["availability"], src["core_source_id"]) == ("PRIVATE", a, "READY", sha[:16])
    assert src["core_source_id"] == probe.id and src["metadata"]["n_units"] == len(_decoder().frames(probe))
    assert src["raw_key"] == f"pilot-test/private/{a}/{dto.source_id}/raw/{sha}/video.mp4" and s3.head(src["raw_key"])
    frames = frame_bytes(pg)
    assert frames > 0   # ảnh lưu sẵn tách cùng job (§21.20)
    acct = account(pg, a)
    assert (acct["persisted_bytes"], acct["reserved_bytes"]) == (len(data) + frames, len(data))   # còn giữ phần staging
    assert up["svc"].complete(actor(a), created.id) == dto   # gọi lại không tạo job mới
    assert up["svc"].sweep_staging() == []   # chưa hết hạn
    pg.sql("UPDATE rav_pilot_upload SET capability_expires_at = now() - interval '1 minute'")
    assert up["svc"].sweep_staging() == [created.id]
    assert s3.head(created.transfer.fields["key"]) is None
    acct = account(pg, a)
    assert (acct["persisted_bytes"], acct["reserved_bytes"]) == (len(data) + frames, 0)


def test_same_bytes_reuse_source_without_double_charge(up):
    pg, a = up["pg"], up["a"]
    data = up["video"].read_bytes()
    _, first = uploaded(up, data, "u1")
    _, second = uploaded(up, data, "u2")
    assert second.state == "READY" and second.source_id == first.source_id
    assert account(pg, a)["persisted_bytes"] == len(data) + frame_bytes(pg)
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_source_asset")[0]["n"] == 1
    assert pg.sql("SELECT count(*) AS n FROM rav_pilot_frame_set")[0]["n"] == 1


def test_invalid_bytes_fail_and_release_canonical_reservation(up):
    pg, a = up["pg"], up["a"]
    junk = b"not a video" * 50
    created, dto = uploaded(up, junk, "u1")
    assert dto.state == "FAILED" and dto.error["code"] == "upload_invalid" and dto.source_id is None
    assert account(pg, a) == {"persisted_bytes": 0, "reserved_bytes": len(junk)}   # chỉ còn phần staging chờ sweep
    job = pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (dto.validation_job_id,))[0]
    assert job["state"] == "FAILED" and job["error"]["code"] == "upload_invalid"


def test_duration_bound_rejects_long_video(up):
    pg = up["pg"]
    uid = pg.profile(policy_id=pg.policy(config=dict(POLICY_CONFIG, max_duration_ms=1000)))
    svc = PilotUploads(PilotDB(pg.api_url), lambda: up["s3"])
    data = up["video"].read_bytes()
    created = svc.create(actor(uid), UploadIn(filename="v.mp4", declared_bytes=len(data)), "k")
    post(created, data)
    svc.complete(actor(uid), created.id)
    up["dispatch"].run_once()
    dto = svc.get(actor(uid), created.id)
    assert dto.state == "FAILED" and "quá dài" in dto.error["message"]


def test_complete_requires_object_and_declared_size(up):
    svc, a = up["svc"], up["a"]
    created = svc.create(actor(a), UploadIn(filename="v.mp4", declared_bytes=100), "k")
    with pytest.raises(ServiceError) as e:
        svc.complete(actor(a), created.id)
    assert e.value.code == "upload_invalid"


def test_abort_and_isolation(up):
    svc, a, b = up["svc"], up["a"], up["b"]
    created = svc.create(actor(a), UploadIn(filename="v.mp4", declared_bytes=100), "k")
    for call in (lambda: svc.get(actor(b), created.id), lambda: svc.complete(actor(b), created.id),
                 lambda: svc.abort(actor(b), created.id)):
        with pytest.raises(ServiceError) as e:
            call()
        assert e.value.status == 404
    assert svc.abort(actor(a), created.id).state == "ABORTED"
    with pytest.raises(ServiceError):
        svc.complete(actor(a), created.id)
    data = up["video"].read_bytes()
    done, _ = uploaded(up, data, "k2")
    with pytest.raises(ServiceError) as e:
        svc.abort(actor(a), done.id)
    assert e.value.code == "upload_invalid"


def test_staging_overwrite_after_verify_keeps_canonical(up):
    pg, a, s3 = up["pg"], up["a"], up["s3"]
    data = up["video"].read_bytes()
    created, dto = uploaded(up, data, "u1")
    post(created, b"x" * 10)   # trình duyệt POST lại vào staging khi capability còn hạn
    src = pg.sql("SELECT raw_sha256, raw_key FROM rav_pilot_source_asset WHERE id = %s", (dto.source_id,))[0]
    body = s3.client.get_object(Bucket=s3.bucket, Key=src["raw_key"])["Body"].read()
    assert src["raw_sha256"] == hashlib.sha256(body).hexdigest() == hashlib.sha256(data).hexdigest()
    assert up["svc"].complete(actor(a), created.id).state == "READY"


def test_abort_lost_preput_attempt_keeps_usage_and_storage_until_reconcile(up):
    from src.rav.store.pilot_runtime import SlotEvidence

    pg, svc, a = up['pg'], up['svc'], up['a']
    data = up['video'].read_bytes()
    created = svc.create(actor(a), UploadIn(filename='v.mp4', declared_bytes=len(data)), 'lost-preput')
    post(created, data)
    job = svc.complete(actor(a), created.id)
    runtime = up['dispatch'].runtime
    lease = runtime.claim(up['dispatch'].worker_ctx, 'CPU')
    pg.sql("UPDATE rav_pilot_job_attempt SET lease_until=now()-interval '1 minute' WHERE id=%s", (lease.attempt_id,))
    runtime.recover_expired_leases()
    assert svc.abort(actor(a), created.id).state == 'VALIDATING'
    assert account(pg, a)['reserved_bytes'] == 2 * len(data)
    holds = pg.sql('SELECT state,released_ms FROM rav_pilot_quota_hold WHERE job_id=%s', (job.validation_job_id,))
    assert all(h['state'] == 'OPEN' and h['released_ms'] == 0 for h in holds)
    runtime.reconcile_slot(up['dispatch'].worker_ctx, lease.slot_key, lease.slot_generation,
                           SlotEvidence(process_exit_verified=True, cpu_ms=10, gpu_ms=0))
    assert svc.get(actor(a), created.id).state == 'ABORTED'
    assert account(pg, a)['reserved_bytes'] == len(data)


def test_complete_sweep_race_marks_expired_upload_before_deleting_staging(up, monkeypatch):
    import threading

    svc, a, pg = up['svc'], up['a'], up['pg']
    data = up['video'].read_bytes()
    created = svc.create(actor(a), UploadIn(filename='v.mp4', declared_bytes=len(data)), 'expired-race')
    post(created, data)
    entered, resume = threading.Event(), threading.Event()
    head = up['s3'].head
    def paused_head(key):
        result = head(key)
        if threading.current_thread() is not threading.main_thread():
            entered.set()
            assert resume.wait(10)
        return result
    monkeypatch.setattr(up['s3'], 'head', paused_head)
    errors = []
    def complete():
        try:
            svc.complete(actor(a), created.id)
        except ServiceError as exc:
            errors.append(exc)
    thread = threading.Thread(target=complete)
    thread.start()
    assert entered.wait(10)
    pg.sql("UPDATE rav_pilot_upload SET capability_expires_at=now()-interval '1 minute' WHERE id=%s", (created.id,))
    assert svc.sweep_staging() == [created.id]
    resume.set()
    thread.join(10)
    assert not thread.is_alive()
    assert svc.get(actor(a), created.id).state == 'ABORTED'
    assert pg.sql('SELECT count(*) AS n FROM rav_pilot_job')[0]['n'] == 0
    assert account(pg, a)['reserved_bytes'] == 0
    assert errors == []  # complete rereads ABORTED instead of enqueuing from its stale HEAD.


def test_complete_rejects_expired_capability(up):
    svc, a, pg = up['svc'], up['a'], up['pg']
    data = up['video'].read_bytes()
    created = svc.create(actor(a), UploadIn(filename='v.mp4', declared_bytes=len(data)), 'expired-complete')
    post(created, data)
    pg.sql("UPDATE rav_pilot_upload SET capability_expires_at=now()-interval '1 minute' WHERE id=%s", (created.id,))
    with pytest.raises(ServiceError) as exc:
        svc.complete(actor(a), created.id)
    assert exc.value.code == 'upload_invalid'
    assert pg.sql('SELECT count(*) AS n FROM rav_pilot_job')[0]['n'] == 0
