"""Kiểm thử toàn diện chu trình Xuất dữ liệu, làm mờ PII và Owner Confirmation Gate (§21.8).

Vòng đời kiểm chứng:
1. REQUESTED: Tạo export từ Snapshot bất biến đã chốt, xếp job EXPORT_BUILD.
2. PROCESSING -> NEEDS_CONFIRMATION: Worker giải mã frame, che PII (YuNet + ONNX), sinh báo cáo và tính payload_hash.
3. Review preview: Chủ sở hữu xem trước ảnh đã che mờ và báo cáo HTML.
4. Confirmation Gate: Từ chối xác nhận nếu payload_hash không khớp (409 payload_mismatch). Chấp thuận khi đúng hash.
5. FINALIZING -> READY: Worker đóng gói file ZIP, tải lên S3.
6. Download: Cấp Presigned GET URL tải file ZIP hợp lệ.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.rav.api.auth import AuthConfig
from src.rav.api.pilot import create_pilot_app
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import DecisionChange, DecisionInput, PilotDB, SnapshotInput
from src.rav.store.pilot_export import PilotExport
from src.rav.worker import export_handlers, pii
from src.rav.worker.export_handlers import make_export_handlers
from tests.conftest import make_video
from tests.test_pilot_api_v2 import ISS, JWK, auth
from tests.test_pilot_curation import cu  # noqa: F401  (fixture cu)
from tests.test_pilot_media import actor, seed_frames, seed_source, unit


class ReadyDetector(pii.PIIDetector):
    """Lifecycle tests inject a ready detector: CI has no PII weights, and runtime has no fake fallback (§21.8 DEP6)."""
    def __init__(self):
        self.config, self.errors, self.model_dir = dict(pii.DEFAULT_CONFIG), {}, None

    ready = True

    def detect(self, image):
        return [{"kind": "plate", "confidence": 0.9, "box": [1.0, 1.0, 5.0, 5.0]}]


@pytest.fixture
def exp_env(cu, monkeypatch):  # noqa: F811
    monkeypatch.setattr(export_handlers, "PIIDetector", ReadyDetector)
    # Đăng ký export handlers vào worker dispatcher
    for kind, fn in make_export_handlers(cu.s3, PilotDB(cu.pg.worker_url), cu.tmp / "work").items():
        cu.d.register_handler(kind, fn)
    cu.exp = PilotExport(cu.api, cu.s3)

    # Giữ 1 frame trong session để tạo snapshot
    sv = cu.api.get_review_state(actor(cu.a), cu.session.id).state_version
    unit_id = f"{cu.core}:0.000-0.000"
    cu.api.append_decisions(
        actor(cu.a),
        cu.session.id,
        DecisionInput(
            changes=[DecisionChange(unit_id=unit_id, action="keep")],
            expected_state_version=sv,
        ),
    )
    sv2 = cu.api.get_review_state(actor(cu.a), cu.session.id).state_version
    cu.snap = cu.api.create_snapshot(actor(cu.a), cu.session.id, SnapshotInput(expected_state_version=sv2, note="test snap"), "snap-key-1")
    return cu


def test_export_timing_reaches_collector_and_cache_skips_inference(exp_env):
    import logging

    from src.rav.telemetry import TimingCollector

    env = exp_env
    collector = TimingCollector(PilotDB(env.pg.worker_url), 'worker')
    logging.getLogger('rav.timing').addHandler(collector)
    try:
        for run in range(2):
            exp = env.exp.request_export(actor(env.a), env.snap.id, idempotency_key=f'trace-{run}')
            assert env.d.run_once()
            assert env.exp.get_export(actor(env.a), exp.id).state == 'NEEDS_CONFIRMATION'
            while not collector.pending.empty():
                collector.flush_batch()
            assert collector.failures == 0
            events = env.pg.sql('SELECT stage,data FROM rav_pilot_timing_event WHERE job_id=%s', (exp.build_job_id,))
            stages = {x['stage'] for x in events}
            cache = [x['data']['cache_hit'] for x in events if x['stage'] == 'worker.export.pii_cache']
            assert cache == [bool(run)]
            if not run:
                assert {'worker.export.frame_load', 'worker.export.pii', 'worker.export.preview_put'} <= stages
            else:
                assert 'worker.export.pii' not in stages
    finally:
        collector.close()


def test_export_lifecycle_full_pipeline(exp_env):
    env = exp_env
    snap_id = env.snap.id

    # 1. Yêu cầu export (REQUESTED)
    exp = env.exp.request_export(actor(env.a), snap_id, idempotency_key="key-exp-1")
    assert exp.state == "REQUESTED"
    assert exp.build_job_id is not None
    assert exp.n_members >= 1

    # Idempotent replay
    exp_replay = env.exp.request_export(actor(env.a), snap_id, idempotency_key="key-exp-1")
    assert exp_replay.id == exp.id

    # Chưa review được khi đang REQUESTED
    with pytest.raises(ServiceError) as exc_info:
        env.exp.get_review(actor(env.a), exp.id)
    assert exc_info.value.code == "export_not_reviewable"

    # Chưa download được khi chưa READY
    with pytest.raises(ServiceError) as exc_info:
        env.exp.get_download(actor(env.a), exp.id)
    assert exc_info.value.code == "export_not_ready"

    # 2. Worker xử lý EXPORT_BUILD
    assert env.d.run_once()

    # Kiểm tra trạng thái chuyển sang NEEDS_CONFIRMATION
    exp_after_build = env.exp.get_export(actor(env.a), exp.id)
    assert exp_after_build.state == "NEEDS_CONFIRMATION", f"Export failed with error: {exp_after_build.error}"
    assert exp_after_build.payload_hash is not None
    assert exp_after_build.review_receipt_id is not None

    # 3. Chủ sở hữu xem trước Review (Owner Review)
    review = env.exp.get_review(actor(env.a), exp.id)
    assert review.export_id == exp.id
    assert review.payload_hash == exp_after_build.payload_hash
    assert review.report_url is not None
    assert len(review.members) >= 1
    assert review.members[0].preview_url is not None
    assert review.source_identity_verified

    # 4. Owner Confirmation Gate
    # 4a. Gửi sai payload_hash -> Bị từ chối (409 payload_mismatch)
    with pytest.raises(ServiceError) as exc_info:
        env.exp.confirm_export(
            actor(env.a),
            exp.id,
            payload_hash="invalid_hash_1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab",
            review_receipt_id=review.review_receipt_id,
            idempotency_key="confirm-bad",
        )
    assert exc_info.value.code == "payload_mismatch"

    # 4b. Gửi đúng payload_hash -> Chấp thuận, chuyển sang FINALIZING
    exp_finalizing = env.exp.confirm_export(
        actor(env.a),
        exp.id,
        payload_hash=review.payload_hash,
        review_receipt_id=review.review_receipt_id,
        idempotency_key="confirm-good",
    )
    assert exp_finalizing.state == "FINALIZING"
    assert exp_finalizing.finalize_job_id is not None

    # 5. Worker xử lý EXPORT_FINALIZE
    assert env.d.run_once()

    # Kiểm tra trạng thái chuyển sang READY
    exp_ready = env.exp.get_export(actor(env.a), exp.id)
    assert exp_ready.state == "READY"
    assert exp_ready.output_size_bytes is not None and exp_ready.output_size_bytes > 0
    # Durable S3 bundle and artifacts are accepted atomically, not only ephemeral UI state.
    arts = env.pg.sql("SELECT kind,size_bytes FROM rav_pilot_artifact WHERE job_id=ANY(%s)",
                      ([exp.build_job_id, exp_finalizing.finalize_job_id],))
    assert {a["kind"] for a in arts} >= {"EXPORT_PREVIEW", "EXPORT_BUNDLE", "REPORT"}
    objects = env.pg.sql("SELECT state FROM rav_pilot_worker_object WHERE job_id=ANY(%s)",
                         ([exp.build_job_id, exp_finalizing.finalize_job_id],))
    assert objects and all(o["state"] == "ACCEPTED" for o in objects)

    # 6. Tải gói ZIP hoàn tất (Download)
    download = env.exp.get_download(actor(env.a), exp.id)
    assert download.export_id == exp.id
    assert download.url.startswith("https://") or download.url.startswith("http://")
    assert download.filename.endswith(".zip")
    assert download.size_bytes == exp_ready.output_size_bytes


def test_export_api_v2_endpoints(exp_env):
    env = exp_env
    snap_id = env.snap.id

    client = TestClient(
        create_pilot_app(
            env.pg.api_url,
            AuthConfig(expected_issuer=ISS),
            raw_jwks={"keys": [JWK]},
            storage_factory=lambda: env.s3,
        )
    )

    # 1. POST /api/v2/snapshots/{snap_id}/exports
    res = client.post(
        f"/api/v2/snapshots/{snap_id}/exports",
        headers={**auth(env.a), "Idempotency-Key": "api-exp-1"},
    )
    assert res.status_code == 202, res.text
    data = res.json()
    export_id = data["id"]
    assert data["state"] == "REQUESTED"

    listing = client.get(f"/api/v2/sessions/{env.session.id}/exports?limit=1", headers=auth(env.a))
    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == export_id
    assert listing.json()["items"][0]["n_members"] == env.snap.n_members
    assert client.get(f"/api/v2/sessions/{env.session.id}/exports", headers=auth(env.b)).status_code == 404

    # Người dùng B không được truy cập export của A (404)
    res_b = client.get(f"/api/v2/exports/{export_id}", headers=auth(env.b))
    assert res_b.status_code == 404

    # Worker chạy BUILD
    assert env.d.run_once()

    # 2. GET /api/v2/exports/{export_id}
    res_check = client.get(f"/api/v2/exports/{export_id}", headers=auth(env.a))
    assert res_check.status_code == 200
    assert res_check.json()["state"] == "NEEDS_CONFIRMATION"

    # 3. GET /api/v2/exports/{export_id}/review
    res_rev = client.get(f"/api/v2/exports/{export_id}/review", headers=auth(env.a))
    assert res_rev.status_code == 200
    rev_data = res_rev.json()
    payload_hash = rev_data["payload_hash"]
    receipt_id = rev_data["review_receipt_id"]
    assert len(rev_data["members"]) >= 1

    # 4. POST /api/v2/exports/{export_id}/confirm
    res_conf = client.post(
        f"/api/v2/exports/{export_id}/confirm",
        headers={**auth(env.a), "Idempotency-Key": "api-conf-1"},
        json={"payload_hash": payload_hash, "review_receipt_id": receipt_id},
    )
    assert res_conf.status_code == 202
    assert res_conf.json()["state"] == "FINALIZING"

    # Worker chạy FINALIZE
    assert env.d.run_once()

    # 5. GET /api/v2/exports/{export_id}/download
    res_dl = client.get(f"/api/v2/exports/{export_id}/download", headers=auth(env.a))
    assert res_dl.status_code == 200
    dl_data = res_dl.json()
    assert dl_data["filename"].endswith(".zip")
    assert dl_data["size_bytes"] > 0
    assert "url" in dl_data


def test_export_fails_closed_without_pii_models(exp_env, monkeypatch, tmp_path):
    """Missing weights: export FAILED with pii_model_unavailable, no preview/ZIP ever uploaded."""
    monkeypatch.setattr(export_handlers, "PIIDetector", pii.PIIDetector)
    monkeypatch.setenv("RAV_PII_MODEL_DIR", str(tmp_path / "no-models"))
    exp = exp_env.exp.request_export(actor(exp_env.a), exp_env.snap.id, idempotency_key="key-exp-closed")
    exp_env.d.run_once()
    failed = exp_env.exp.get_export(actor(exp_env.a), exp.id)
    assert failed.state == "FAILED" and "pii_model_unavailable" in str(failed.error)
    keys = [o["Key"] for o in exp_env.s3.client.list_objects_v2(Bucket=exp_env.s3.bucket).get("Contents", [])]
    assert not [k for k in keys if "/exports/" in k]


REAL_MODELS = Path("data/models/pii").resolve()


def test_export_frame_identity_and_checksum(exp_env, monkeypatch):
    """Materialized uppercase FRAME must win over raw fallback, with exact pixels and checked SHA."""
    from PIL import Image

    from src.rav.worker.handlers import _decoder, _probe

    env = exp_env
    frames = _decoder().frames(_probe(env.path))
    _, rows = seed_frames(env.pg, env.s3, env.sid, env.a, env.path, frames)
    f = frames[-1]
    member = dict(source_id=str(env.sid), core_source_id=env.core, unit_id=unit(env.core, f), ord=0)
    with monkeypatch.context() as m:
        m.setattr(export_handlers, "_RawFrames", lambda *a: pytest.fail("must use canonical FRAME"))
        img, idx = export_handlers._load_member_image(env.s3, env.api, env.a, member, env.tmp, {})
    assert idx == f.idx != member["ord"]
    data = env.s3.client.get_object(Bucket=env.s3.bucket, Key=rows[("FRAME", f.idx)]["key"])["Body"].read()
    with Image.open(io.BytesIO(data)) as expected:
        assert img.tobytes() == expected.convert("RGB").tobytes()
    with pytest.raises(ServiceError):
        export_handlers._load_member_image(env.s3, env.api, env.b, member, env.tmp, {})
    env.pg.sql("UPDATE rav_pilot_frame SET sha256 = %s WHERE id = %s", ("0" * 64, rows[("FRAME", f.idx)]["id"]))
    with pytest.raises(ServiceError, match="checksum"):
        export_handlers._load_member_image(env.s3, env.api, env.a, member, env.tmp, {})


def test_export_raw_fallback_exact_units_not_ordinals(exp_env):
    """Legacy raw-only sources: multiple times, backwards read and another source; no fake images."""
    import numpy as np

    from src.rav.worker.handlers import _decoder, _probe

    env, sources = exp_env, {}
    other = make_video(env.tmp / "other.mp4", seconds=2, fps=6, size=(80, 48))
    sid2, core2, frames2 = seed_source(env.pg, env.s3, other, env.a)
    frames = _decoder().frames(_probe(env.path))
    try:
        for sid, core, path, frame in [(env.sid, env.core, env.path, frames[-1]),
                                      (env.sid, env.core, env.path, frames[2]),
                                      (sid2, core2, other, frames2[-1])]:
            member = dict(source_id=str(sid), core_source_id=core, unit_id=unit(core, frame), ord=0)
            img, idx = export_handlers._load_member_image(env.s3, env.api, env.a, member, env.tmp, sources)
            assert idx == frame.idx != 0
            assert np.array_equal(np.asarray(img), _decoder().read_exact(_probe(path), frame))
        member["unit_id"] = f"{core2}:999.000-999.000"
        with pytest.raises(ServiceError, match="unit export"):
            export_handlers._load_member_image(env.s3, env.api, env.a, member, env.tmp, sources)
    finally:
        for src in sources.values():
            src.close()


def test_export_pixels_and_history_match_frozen_members(exp_env):
    """Full build with distant timestamps, verified materialized pixels and durable paginated history."""
    from PIL import Image

    from src.rav.worker.handlers import _decoder, _probe

    env, a = exp_env, actor(exp_env.a)
    frames = _decoder().frames(_probe(env.path))
    _, rows = seed_frames(env.pg, env.s3, env.sid, env.a, env.path, frames)
    selected = [frames[-2], frames[-1]]
    state = env.api.get_review_state(a, env.session.id)
    env.api.append_decisions(a, env.session.id, DecisionInput(expected_state_version=state.state_version,
                            changes=[DecisionChange(unit_id=unit(env.core, f), action="keep") for f in selected]))
    state = env.api.get_review_state(a, env.session.id)
    snap = env.api.create_snapshot(a, env.session.id, SnapshotInput(expected_state_version=state.state_version), "snap-2")
    exp = env.exp.request_export(a, snap.id, "pixels")
    assert env.d.run_once()
    review = env.exp.get_review(a, exp.id)
    for member, frame in zip(review.members, [frames[0], *selected], strict=True):
        raw = env.s3.client.get_object(Bucket=env.s3.bucket, Key=rows[("FRAME", frame.idx)]["key"])["Body"].read()
        with Image.open(io.BytesIO(raw)) as original:
            expected, _ = pii.redact(original.convert("RGB"), ReadyDetector().detect(original), pii.DEFAULT_CONFIG)
        refs = env.pg.sql("SELECT artifact_refs FROM rav_pilot_export_review_receipt WHERE id=%s", (review.review_receipt_id,))[0]["artifact_refs"]
        ref = next(r for r in refs if r.get("role") == "member_image" and r["unit_id"] == member.unit_id)
        result = env.s3.client.get_object(Bucket=env.s3.bucket, Key=ref["key"])["Body"].read()
        with Image.open(io.BytesIO(result)) as actual:
            assert actual.tobytes() == expected.tobytes()
    second = env.exp.request_export(a, env.snap.id, "history-second")
    # A fresh service instance after reload reads the same records, not in-memory state.
    fresh = PilotExport(env.api, env.s3)
    page = fresh.list_exports(a, env.session.id, limit=1)
    assert page.total == 2 and page.next_cursor == "1" and page.items[0].id == second.id
    older = fresh.list_exports(a, env.session.id, cursor=page.next_cursor, limit=1)
    assert older.items[0].id == exp.id and older.items[0].n_members == 3 and older.next_cursor is None


@pytest.mark.skipif(not all((REAL_MODELS / n).is_file() for n, _ in pii.MODELS.values()), reason="cần model PII trong data/models/pii")
def test_export_lifecycle_with_real_pinned_models(exp_env, monkeypatch):
    """Real YuNet + plate ONNX through build → review → confirm → finalize; ZIP evidence names pinned models."""
    monkeypatch.setattr(export_handlers, "PIIDetector", pii.PIIDetector)
    monkeypatch.setenv("RAV_PII_MODEL_DIR", str(REAL_MODELS))
    a = actor(exp_env.a)
    exp = exp_env.exp.request_export(a, exp_env.snap.id, idempotency_key="key-exp-real")
    assert exp_env.d.run_once()
    built = exp_env.exp.get_export(a, exp.id)
    assert built.state == "NEEDS_CONFIRMATION", built.error
    review = exp_env.exp.get_review(a, exp.id)
    exp_env.exp.confirm_export(a, exp.id, payload_hash=review.payload_hash, review_receipt_id=review.review_receipt_id,
                               idempotency_key="confirm-real")
    assert exp_env.d.run_once()
    assert exp_env.exp.get_export(a, exp.id).state == "READY"
    s3 = exp_env.s3
    key = exp_env.pg.sql("SELECT output_key FROM rav_pilot_export WHERE id=%s", (exp.id,))[0]["output_key"]
    assert str(exp.id) in key and key.endswith("/bundle.zip")
    with zipfile.ZipFile(io.BytesIO(s3.client.get_object(Bucket=s3.bucket, Key=key)["Body"].read())) as z:
        names = z.namelist()
        manifest = json.loads(z.read(next(n for n in names if n.endswith("manifest.json"))))
    assert any(n.startswith("images/") for n in names)
    assert pii.MODELS["plate"][1] in json.dumps(manifest) and pii.MODELS["face"][1] in json.dumps(manifest)


def test_pii_cache_durable_reuse_owner_and_corruption(exp_env, monkeypatch):
    from src.rav.store.pilot_export import read_pii_cache

    env, calls = exp_env, []
    original = ReadyDetector.detect
    monkeypatch.setattr(ReadyDetector, "detect", lambda self, image: calls.append(image.size) or original(self, image))
    first = env.exp.request_export(actor(env.a), env.snap.id, "cache-first")
    assert env.d.run_once()
    assert env.exp.get_export(actor(env.a), first.id).state == "NEEDS_CONFIRMATION"
    rows = env.pg.sql("SELECT * FROM rav_pilot_artifact WHERE owner_id=%s AND variant='PII_REDACTED'", (env.a,))
    assert len(rows) == 1 and len(calls) == 1
    cached = rows[0]
    assert read_pii_cache(env.api, env.b, [cached["input_hash"]]) == {}
    # New handler/service instances simulate a worker restart; reuse is in DB/S3.
    for kind, fn in make_export_handlers(env.s3, PilotDB(env.pg.worker_url), env.tmp / "work2").items():
        env.d.register_handler(kind, fn)
    second = env.exp.request_export(actor(env.a), env.snap.id, "cache-second")
    assert env.d.run_once()
    assert env.exp.get_export(actor(env.a), second.id).state == "NEEDS_CONFIRMATION" and len(calls) == 1
    assert env.pg.sql("SELECT count(*) AS n FROM rav_pilot_artifact WHERE variant='PII_REDACTED'")[0]["n"] == 1
    # Missing/corrupt cached bytes must trigger real processing, not a fake image or silent reuse.
    env.s3.client.put_object(Bucket=env.s3.bucket, Key=cached["object_key"], Body=b"corrupt")
    third = env.exp.request_export(actor(env.a), env.snap.id, "cache-third")
    assert env.d.run_once()
    assert env.exp.get_export(actor(env.a), third.id).state == "NEEDS_CONFIRMATION" and len(calls) == 2
    assert env.pg.sql("SELECT count(*) AS n FROM rav_pilot_artifact WHERE variant='PII_REDACTED'")[0]["n"] == 2


def test_pii_cache_identity_invalidates_input_models_and_config():
    import copy
    from uuid import uuid4

    owner, sid = uuid4(), uuid4()
    m = dict(source_id=str(sid), core_source_id="core", unit_id="core:1.000-1.000")
    inputs = ({sid: dict(core_source_id="core", raw_sha256="raw")}, {(sid, m["unit_id"]): {"sha256": "frame"}})
    d = ReadyDetector()
    original = export_handlers.pii_cache_hash(owner, m, inputs, d)
    class OtherModel(ReadyDetector):
        @property
        def model_manifest(self):
            return {**super().model_manifest, "plate": {"sha256": "different-model"}}
    assert original != export_handlers.pii_cache_hash(owner, m, inputs, OtherModel())
    assert original != export_handlers.pii_cache_hash(uuid4(), m, inputs, d)
    changed = copy.deepcopy(inputs)
    changed[1][(sid, m["unit_id"])]["sha256"] = "changed-frame"
    assert original != export_handlers.pii_cache_hash(owner, m, changed, d)
    changed = copy.deepcopy(inputs)
    changed[0][sid]["raw_sha256"] = "changed-raw"
    assert original != export_handlers.pii_cache_hash(owner, m, changed, d)
    d.config["inference_max_side"] = 320
    assert original != export_handlers.pii_cache_hash(owner, m, inputs, d)


def test_pii_partial_upload_never_publishes_cache_and_can_cleanup(exp_env, monkeypatch):
    from src.rav.store.pilot_objects import cleanup_attempt

    env = exp_env
    put = env.s3.put_file
    def fail_report(key, *args):
        if key.endswith("/payload.json"):
            raise ServiceError("dependency_unavailable", "test interrupted upload", 503)
        return put(key, *args)
    monkeypatch.setattr(env.s3, "put_file", fail_report)
    exp = env.exp.request_export(actor(env.a), env.snap.id, "interrupted")
    assert env.d.run_once()
    assert env.exp.get_export(actor(env.a), exp.id).state == "FAILED"
    assert env.pg.sql("SELECT count(*) AS n FROM rav_pilot_artifact WHERE variant='PII_REDACTED'")[0]["n"] == 0
    attempt = env.pg.sql("SELECT id FROM rav_pilot_job_attempt WHERE job_id=%s", (exp.build_job_id,))[0]["id"]
    assert cleanup_attempt(PilotDB(env.pg.worker_url), env.s3, attempt, process_stopped=True)
    assert all(r["state"] == "DELETED" for r in env.pg.sql("SELECT state FROM rav_pilot_worker_object WHERE attempt_id=%s", (attempt,)))


def test_pii_dataset_persists_when_storage_accounting_disabled(exp_env):
    from tests.conftest import POLICY_CONFIG

    env, a = exp_env, actor(exp_env.a)
    policy = env.pg.policy(config={**POLICY_CONFIG, "storage_accounting_enabled": False,
                                  "storage_user_bytes": 0, "storage_global_bytes": 0})
    env.pg.sql("UPDATE rav_pilot_profile SET policy_id=%s WHERE user_id=%s", (policy, env.a))
    exp = env.exp.request_export(a, env.snap.id, "disabled-storage")
    assert env.d.run_once()
    review = env.exp.get_review(a, exp.id)
    env.exp.confirm_export(a, exp.id, review.payload_hash, review.review_receipt_id, "disabled-confirm")
    assert env.d.run_once()
    assert env.exp.get_export(a, exp.id).state == "READY"
    assert env.pg.sql("SELECT count(*) AS n FROM rav_pilot_storage_account")[0]["n"] == 0
    bundle = env.pg.sql("SELECT object_key,sha256 FROM rav_pilot_artifact WHERE kind='EXPORT_BUNDLE'")[0]
    data = env.s3.client.get_object(Bucket=env.s3.bucket, Key=bundle["object_key"])["Body"].read()
    import hashlib
    assert hashlib.sha256(data).hexdigest() == bundle["sha256"]


def test_old_wrong_frame_preview_cannot_be_confirmed(exp_env):
    env, a = exp_env, actor(exp_env.a)
    exp = env.exp.request_export(a, env.snap.id, "legacy-source")
    assert env.d.run_once()
    review = env.exp.get_review(a, exp.id)
    refs = env.pg.sql("SELECT artifact_refs FROM rav_pilot_export_review_receipt WHERE id=%s", (review.review_receipt_id,))[0]["artifact_refs"]
    for ref in refs:
        ref.pop("frame_identity", None)
    env.pg.sql("UPDATE rav_pilot_export_review_receipt SET artifact_refs=%s WHERE id=%s",
               (json.dumps(refs), review.review_receipt_id))
    assert not env.exp.get_review(a, exp.id).source_identity_verified
    with pytest.raises(ServiceError) as caught:
        env.exp.confirm_export(a, exp.id, review.payload_hash, review.review_receipt_id, "legacy-confirm")
    assert caught.value.code == "export_source_unverified"

