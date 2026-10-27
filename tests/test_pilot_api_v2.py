"""API `/api/v2` qua app pilot trên PostgreSQL thật (DEP2F #89): auth, phạm vi A/B, phiên, quyết định, snapshot, xóa nguồn, job, quota.

Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_api_v2.py -q`.
"""

from __future__ import annotations

import json
import time
from uuid import uuid4

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.testclient import TestClient

from src.rav.api.auth import AuthConfig
from src.rav.api.pilot import create_pilot_app
from src.rav.store.pilot_curation import PILOT_RECIPES

ISS = "https://test.supabase.co/auth/v1"
KEY = ec.generate_private_key(ec.SECP256R1())
JWK = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key())) | {"kid": "k1", "alg": "ES256"}


def auth(uid) -> dict:
    tok = jwt.encode({"sub": str(uid), "iss": ISS, "aud": "authenticated", "exp": int(time.time()) + 600}, KEY,
                     algorithm="ES256", headers={"kid": "k1"})
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def api(pg):
    policy = pg.policy()
    a, b = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    client = TestClient(create_pilot_app(pg.api_url, AuthConfig(expected_issuer=ISS), raw_jwks={"keys": [JWK]}))
    return {"c": client, "pg": pg, "a": a, "b": b, "va": pg.source(a, "cam_a", n_units=8), "vb": pg.source(b, "cam_a"),
            "sys": pg.source(None, "demo")}


def test_auth_required_and_no_store(api):
    c = api["c"]
    r = c.get("/api/v2/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized" and r.json()["error"]["request_id"]
    assert r.headers["Cache-Control"] == "private, no-store"
    # §21.34: tài khoản mới hợp lệ tự động bootstrap theo signup_default policy, chỉ có quyền user thường
    new_uid = uuid4()
    new_me = c.get("/api/v2/me", headers=auth(new_uid))
    assert new_me.status_code == 200 and new_me.json()["user_id"] == str(new_uid)
    assert new_me.json()["operations_read"] is False and new_me.json()["models_write"] is False
    assert c.get("/api/v2/operations", headers=auth(new_uid)).status_code == 403
    # Gọi lại idempotent sau reload/đăng nhập lại
    assert c.get("/api/v2/me", headers=auth(new_uid)).json()["user_id"] == str(new_uid)
    # Khi operator chưa cấu hình signup_default → 503 policy_unconfigured cho tài khoản mới
    api["pg"].sql("UPDATE rav_pilot_quota_policy SET signup_default = false")
    unconf = c.get("/api/v2/me", headers=auth(uuid4()))
    assert unconf.status_code == 503 and unconf.json()["error"]["code"] == "policy_unconfigured"
    api["pg"].sql("UPDATE rav_pilot_quota_policy SET signup_default = true")
    # Tài khoản bị khóa vẫn 401 account_disabled, không tự mở lại
    disabled = api["pg"].profile(active=False)
    assert c.get("/api/v2/me", headers=auth(disabled)).json()["error"]["code"] == "account_disabled"
    me = c.get("/api/v2/me", headers=auth(api["a"]))
    assert me.status_code == 200 and me.json()["user_id"] == str(api["a"])


def test_v1_routes_not_mounted(api):
    for path in ("/videos", "/api/v1/videos", "/media/videos/x", "/api/v1/exports"):
        assert api["c"].get(path).status_code == 404


def test_sources_scoped_and_no_raw_key(api):
    c, a = api["c"], api["a"]
    page = c.get("/api/v2/sources", headers=auth(a)).json()
    assert page["total"] == 2 and {s["id"] for s in page["items"]} == {str(api["va"]), str(api["sys"])}
    assert "raw_key" not in json.dumps(page) and "owner_id" not in page["items"][0]
    assert {s["core_source_id"] for s in page["items"]} == {"cam_a", "demo"}   # §21.16: unit ID dạng core:t0-t1
    assert c.get(f"/api/v2/sources/{api['vb']}", headers=auth(a)).status_code == 404


def test_preparations_route(api):
    """DEP5a §21.14: 202 + job GPU cho nguồn riêng; SYSTEM thiếu đặc trưng → 409; nguồn người khác → 404; cần Idempotency-Key."""
    c, a = api["c"], api["a"]
    url = f"/api/v2/sources/{api['va']}/preparations"
    assert c.post(url, json={"feature": "DINO"}, headers=auth(a)).status_code == 422
    assert c.post(url, json={"feature": "SIFT"}, headers=auth(a) | {"Idempotency-Key": "p"}).status_code == 422
    r = c.post(url, json={"feature": "DINO"}, headers=auth(a) | {"Idempotency-Key": "p"})
    assert r.status_code == 202 and r.json()["job"]["kind"] == "PREPARE_DINO" and not r.json()["ready"]
    assert c.post(url, json={"feature": "DINO"}, headers=auth(a) | {"Idempotency-Key": "p"}).json()["job"]["id"] == r.json()["job"]["id"]
    src = c.get(f"/api/v2/sources/{api['va']}", headers=auth(a)).json()
    assert src["dino"] == {"ready": False, "job_id": r.json()["job"]["id"]} and src["clip"]["job_id"] is None
    sysr = c.post(f"/api/v2/sources/{api['sys']}/preparations", json={"feature": "CLIP"}, headers=auth(a) | {"Idempotency-Key": "q"})
    assert sysr.status_code == 409 and sysr.json()["error"]["code"] == "system_feature_missing"
    assert c.post(f"/api/v2/sources/{api['vb']}/preparations", json={"feature": "CLIP"},
                  headers=auth(a) | {"Idempotency-Key": "z"}).status_code == 404


def test_proposal_routes(api):
    """DEP5b §21.15: catalog recipe pilot, POST đề xuất → 202 job, đọc proposal/kết quả qua route."""
    c, a = api["c"], api["a"]
    recipes = c.get("/api/v2/recipes", headers=auth(a)).json()
    assert [r["name"] for r in recipes if r["available"]] == list(PILOT_RECIPES) and recipes[0]["params_schema"]
    assert {r["name"] for r in recipes if not r["available"]} == {"reference_diversity", "model_guided", "text_model_guided"}
    s = c.post("/api/v2/sessions", json={"name": "p", "goal": "g", "target_k": 3, "pool_source_ids": [str(api["va"])]},
               headers=auth(a) | {"Idempotency-Key": "ps"}).json()
    url = f"/api/v2/sessions/{s['id']}/proposals"
    body = {"recipe": "random", "batch_size": 2, "expected_state_version": 1}
    assert c.post(url, json=body, headers=auth(a)).status_code == 422                       # thiếu Idempotency-Key
    r = c.post(url, json=body, headers=auth(a) | {"Idempotency-Key": "k"})
    assert r.status_code == 202 and r.json()["kind"] == "PROPOSE" and r.json()["state"] == "QUEUED"
    nf = c.post(url, json=body | {"recipe": "diversity"}, headers=auth(a) | {"Idempotency-Key": "k2"})
    assert nf.status_code == 409 and nf.json()["error"]["code"] in ("proposal_in_progress", "feature_not_ready")
    assert c.get(url, headers=auth(a)).json() == []
    assert c.get(f"{url}/1", headers=auth(a)).status_code == 404
    assert c.get(f"/api/v2/jobs/{r.json()['id']}/result", headers=auth(a)).json()["error"]["code"] == "job_not_succeeded"
    assert c.post(f"/api/v2/jobs/{r.json()['id']}/retry", headers=auth(a) | {"Idempotency-Key": "r"}).status_code == 409
    assert c.get(url, headers=auth(api["b"])).status_code == 404


def test_session_flow_decisions_snapshot(api):
    c, a, b = api["c"], api["a"], api["b"]
    body = {"name": "P", "goal": "xe", "target_k": 2, "pool_source_ids": [str(api["va"])], "reference": None}
    assert c.post("/api/v2/sessions", json=body, headers=auth(a)).json()["error"]["code"] == "validation_error"   # thiếu key
    assert c.post("/api/v2/sessions", json=body | {"owner_id": str(b)},
                  headers=auth(a) | {"Idempotency-Key": "k"}).status_code == 422
    r = c.post("/api/v2/sessions", json=body, headers=auth(a) | {"Idempotency-Key": "k"})
    assert r.status_code == 201
    s = r.json()
    assert s["state_version"] == 1 and s["n_units"] == 8
    assert c.post("/api/v2/sessions", json=body, headers=auth(a) | {"Idempotency-Key": "k"}).json()["id"] == s["id"]
    assert c.get(f"/api/v2/sessions/{s['id']}", headers=auth(b)).status_code == 404

    u = "cam_a:0.000-0.000"
    st = c.post(f"/api/v2/sessions/{s['id']}/decisions",
                json={"changes": [{"unit_id": u, "action": "keep"}], "expected_state_version": 1}, headers=auth(a)).json()
    assert st["kept_ids"] == [u] and st["state_version"] == 2
    stale = c.post(f"/api/v2/sessions/{s['id']}/decisions",
                   json={"changes": [{"unit_id": u, "action": "drop"}], "expected_state_version": 1}, headers=auth(a))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "state_version_conflict"
    assert c.get(f"/api/v2/sessions/{s['id']}/state", headers=auth(a)).json() == st
    listed = c.get("/api/v2/sessions", headers=auth(a)).json()["items"][0]   # §21.16: số giữ/bỏ cho sidebar
    assert (listed["n_kept"], listed["n_dropped"]) == (len(st["kept_ids"]), len(st["dropped_ids"]))
    snap = c.post(f"/api/v2/sessions/{s['id']}/snapshots", json={"note": "v1", "expected_state_version": 2},
                  headers=auth(a) | {"Idempotency-Key": "s1"})
    assert snap.status_code == 201 and snap.json()["n_members"] == 1
    forged = c.post(f"/api/v2/sessions/{s['id']}/snapshots",
                    json={"expected_state_version": 2, "snapshot_hash": "0" * 64}, headers=auth(a) | {"Idempotency-Key": "s2"})
    assert forged.status_code == 422   # client không gửi được hash/manifest
    detail = c.get(f"/api/v2/snapshots/{snap.json()['id']}", headers=auth(a)).json()
    assert detail["members"][0]["unit_id"] == u
    assert c.get(f"/api/v2/sessions/{s['id']}/snapshots", headers=auth(a)).json()["total"] == 1


def test_delete_requests(api):
    c, a, pg = api["c"], api["a"], api["pg"]
    r = c.post(f"/api/v2/sources/{api['sys']}/delete-requests", json={"expected_resource_version": 1},
               headers=auth(a) | {"Idempotency-Key": "d1"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    lone = pg.source(a, "lone")
    r = c.post(f"/api/v2/sources/{lone}/delete-requests", json={"expected_resource_version": 1},
               headers=auth(a) | {"Idempotency-Key": "d2"})
    assert r.status_code == 202 and r.json()["state"] == "REQUESTED"
    assert c.get(f"/api/v2/delete-requests/{r.json()['id']}", headers=auth(a)).json() == r.json()
    job = c.get(f"/api/v2/jobs/{r.json()['job_id']}", headers=auth(a)).json()
    assert job["kind"] == "DELETE_SOURCE" and set(job) == {"id", "kind", "state", "state_version", "phase", "progress",
                                                         "attempt_n", "waiting_reason", "error", "resource_id", "result_ref", "accepted_state_version"}
    assert job["accepted_state_version"] is None  # only PROPOSE acknowledges the frozen session version
    assert c.get(f"/api/v2/jobs/{job['id']}", headers=auth(api["b"])).status_code == 404
    cancelled = c.post(f"/api/v2/jobs/{job['id']}/cancel", json={"expected_state_version": 1}, headers=auth(a)).json()
    assert cancelled["state"] == "CANCELLED"
    assert c.get("/api/v2/jobs", headers=auth(a)).json()["total"] == 1
    receipt = c.get(f"/api/v2/delete-requests/{r.json()['id']}", headers=auth(a)).json()
    assert receipt["state"] == "FAILED" and receipt["error"]["code"] == "cancelled"
    retry = c.post(f"/api/v2/sources/{lone}/delete-requests", json={"expected_resource_version": 2},
                   headers=auth(a) | {"Idempotency-Key": "d3"})
    assert retry.status_code == 202 and retry.json()["job_id"] != job["id"]


def test_quota_and_policy_unconfigured(api):
    c, pg = api["c"], api["pg"]
    q = c.get("/api/v2/quota", headers=auth(api["a"])).json()
    assert q["cpu"]["base_available"] == 10_000 and q["admission"] == {"allowed": True, "reason": None}
    nopolicy = pg.profile()
    r = c.get("/api/v2/quota", headers=auth(nopolicy))
    assert r.status_code == 503 and r.json()["error"]["code"] == "policy_unconfigured" and r.json()["error"]["retryable"]


def test_schema_not_ready_but_health_alive(pg):
    import os

    from tests.conftest import PG_ENV, _swap

    base = os.environ[PG_ENV]
    empty = f"rav_pilot_empty_{uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{empty}"')
    try:
        c = TestClient(create_pilot_app(_swap(base, db=empty), AuthConfig(expected_issuer=ISS), raw_jwks={"keys": [JWK]}))
        assert c.get("/health").status_code == 200
        r = c.get("/api/v2/me", headers=auth(uuid4()))
        assert r.status_code == 503 and r.json()["error"]["code"] == "schema_not_ready"
        assert c.get("/health/ready").status_code == 503
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{empty}" WITH (FORCE)')


def test_missing_database_url_is_503_not_sqlite():
    c = TestClient(create_pilot_app("", AuthConfig(expected_issuer=ISS), raw_jwks={"keys": [JWK]}))
    r = c.get("/api/v2/me", headers=auth(uuid4()))
    assert r.status_code == 503 and r.json()["error"]["code"] == "dependency_unavailable"


def test_dep4_routes_upload_media_templates(pg, s3_storage, tmp_path):
    """Route DEP4 qua app pilot: upload (201/202), media (202 rồi 200), mẫu + tạo phiên (Idempotency-Key bắt buộc)."""
    import hashlib

    import requests

    from src.rav.store.pilot_db import PilotDB, canonical_hash
    from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
    from src.rav.worker.dispatcher import PilotDispatcher
    from src.rav.worker.handlers import make_handlers
    from tests.conftest import make_video
    from tests.test_pilot_templates import apply, legacy_sqlite, load_script

    a = pg.profile(policy_id=pg.policy())
    pg.slot("cpu-1")
    pg.slot("media-1", "MEDIA")
    c = TestClient(create_pilot_app(pg.api_url, AuthConfig(expected_issuer=ISS), raw_jwks={"keys": [JWK]},
                                    storage_factory=lambda: s3_storage))
    workers = {}
    for q in ("CPU", "MEDIA"):
        d = PilotDispatcher(PilotRuntime(PilotDB(pg.worker_url)), WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), q, 0.2)
        for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / f"w-{q}").items():
            d.register_handler(kind, fn)
        workers[q] = d

    data = make_video(tmp_path / "v.mp4", seconds=2).read_bytes()
    body = {"filename": "v.mp4", "declared_bytes": len(data)}
    assert c.post("/api/v2/uploads", json=body, headers=auth(a)).status_code == 422   # thiếu Idempotency-Key
    r = c.post("/api/v2/uploads", json=body, headers=auth(a) | {"Idempotency-Key": "u1"})
    assert r.status_code == 201 and set(r.json()) == {
        "id", "state", "validation_job_id", "source_id", "error", "transfer", "declared_bytes", "filename", "target_scope"
    }
    t = r.json()["transfer"]
    assert requests.post(t["url"], data=t["fields"], files={"file": ("v", data)}).status_code in (200, 204)
    done = c.post(f"/api/v2/uploads/{r.json()['id']}/complete", headers=auth(a))
    assert done.status_code == 202 and done.json()["state"] == "VALIDATING"
    workers["CPU"].run_once()
    ready = c.get(f"/api/v2/uploads/{r.json()['id']}", headers=auth(a)).json()
    assert ready["state"] == "READY" and "transfer" not in ready
    core = hashlib.sha256(data).hexdigest()[:16]
    thumb = c.post("/api/v2/media/requests", headers=auth(a),
                   json={"source_id": ready["source_id"], "unit_id": f"{core}:0.000-0.000", "variant": "thumbnail"})
    assert thumb.status_code == 200 and thumb.json()["state"] == "READY" and thumb.json()["url"]   # ảnh lưu sẵn từ upload (§21.20)
    assert thumb.json()["job_id"] is None
    m = c.post("/api/v2/media/requests", headers=auth(a),
               json={"source_id": ready["source_id"], "unit_id": f"{core}:0.000-0.000", "variant": "context"})
    assert m.status_code == 202 and m.json()["state"] == "PENDING"
    workers["MEDIA"].run_once()
    again = c.post("/api/v2/media/requests", headers=auth(a),
                   json={"source_id": ready["source_id"], "unit_id": f"{core}:0.000-0.000", "variant": "context"})
    assert again.status_code == 200 and again.json()["url"]

    legacy_sqlite(tmp_path / "rav.sqlite", core)
    (tmp_path / "vids").mkdir()
    (tmp_path / "vids" / "v.mp4").write_bytes(data)
    plan, _ = load_script().build_plan({"seed_version": "api", "templates": [{"template_key": "k", "legacy_session_id": "s1",
                                                                            "name": "Mẫu"}]},
                                       tmp_path / "rav.sqlite", tmp_path / "vids", "pilot-test")
    apply(pg, plan, canonical_hash(plan))
    [tmpl] = c.get("/api/v2/demo-templates", headers=auth(a)).json()
    assert c.get(f"/api/v2/demo-templates/{tmpl['id']}", headers=auth(a)).json()["history"]
    assert c.post(f"/api/v2/demo-templates/{tmpl['id']}/fork", json={}, headers=auth(a)).status_code == 422
    fork = c.post(f"/api/v2/demo-templates/{tmpl['id']}/fork", json={}, headers=auth(a) | {"Idempotency-Key": "f"})
    assert fork.status_code == 201 and fork.json()["session"]["state_version"] == 1 and fork.json()["counts"]["decisions"] == 0


def test_sample_catalog_routes_media_frames_and_upload_recovery(pg, s3_storage, tmp_path, monkeypatch):
    """Kiểm route Lát 2 (§21.35): include_unpublished, media/frame trên nguồn SYSTEM đã ẩn và GET /api/v2/uploads."""
    from uuid import UUID

    from src.rav.store import pilot_frames
    from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC

    pol = pg.policy()
    a = pg.profile(policy_id=pol)
    b = pg.profile(policy_id=pol)
    c = TestClient(
        create_pilot_app(
            pg.api_url,
            AuthConfig(expected_issuer=ISS),
            raw_jwks={"keys": [JWK]},
            storage_factory=lambda: s3_storage,
        )
    )

    # Tạo nguồn SYSTEM + frame set READY (có 1 frame THUMBNAIL)
    sys_v = pg.source(None, "sample_road", n_units=4, name="Sample Road", description="Video mẫu", published=True)
    fset_id = uuid4()
    frame_id = uuid4()
    pg.sql(
        "INSERT INTO rav_pilot_frame_set (id, scope, owner_id, source_id, raw_sha256, sample_spec_hash, encoding_version, "
        "n_units, object_count, total_bytes, state) VALUES (%s, 'SYSTEM', NULL, %s, %s, %s, %s, 4, 8, 1024, 'READY')",
        (fset_id, sys_v, "a" * 64, DEFAULT_SAMPLE_SPEC["spec_hash"], pilot_frames.ENCODING_VERSION),
    )
    pg.sql(
        "INSERT INTO rav_pilot_frame (id, frame_set_id, variant, frame_idx, unit_id, object_key, sha256, size_bytes, width, height) "
        "VALUES (%s, %s, 'THUMBNAIL', 0, 'sample_road:0.000-0.000', 'pilot-test/frames/sample_road/0.jpg', %s, 512, 320, 180)",
        (frame_id, fset_id, "b" * 64),
    )

    # User A tạo phiên gắn với sys_v khi sys_v đang published = true
    r_sess = c.post(
        "/api/v2/sessions",
        json={"name": "S-A", "goal": "g", "target_k": 2, "pool_source_ids": [str(sys_v)]},
        headers=auth(a) | {"Idempotency-Key": "sess-a-road"},
    )
    assert r_sess.status_code == 201

    # Operator ẩn sys_v (published = false)
    pg.sql("UPDATE rav_pilot_source_asset SET published = false WHERE id = %s", (sys_v,))

    # GET /api/v2/sources mặc định không trả sys_v; include_unpublished=true đòi quyền operations_read
    assert str(sys_v) not in {x["id"] for x in c.get("/api/v2/sources", headers=auth(a)).json()["items"]}
    assert c.get("/api/v2/sources?include_unpublished=true", headers=auth(b)).status_code == 403
    monkeypatch.setenv("RAV_PILOT_OPERATOR_IDS", str(a))
    assert str(sys_v) in {x["id"] for x in c.get("/api/v2/sources?include_unpublished=true", headers=auth(a)).json()["items"]}
    monkeypatch.delenv("RAV_PILOT_OPERATOR_IDS", raising=False)

    # User A (có phiên gắn sys_v) vẫn lấy được ảnh/frame của sys_v dù đã ẩn
    r_batch_a = c.post(
        "/api/v2/media/batch",
        json={"items": [{"source_id": str(sys_v), "unit_id": "sample_road:0.000-0.000", "variant": "thumbnail"}]},
        headers=auth(a),
    )
    assert r_batch_a.status_code == 200 and r_batch_a.json()[0]["state"] == "READY" and r_batch_a.json()[0]["url"]
    r_frame_a = c.get(f"/api/v2/media/requests/{frame_id}", headers=auth(a))
    assert r_frame_a.status_code == 200 and r_frame_a.json()["state"] == "READY" and r_frame_a.json()["url"]

    # User B (không có phiên gắn sys_v) đoán source_id hoặc frame_id của sys_v ẩn đều bị 404 not_found
    r_batch_b = c.post(
        "/api/v2/media/batch",
        json={"items": [{"source_id": str(sys_v), "unit_id": "sample_road:0.000-0.000", "variant": "thumbnail"}]},
        headers=auth(b),
    )
    assert r_batch_b.status_code == 404 and r_batch_b.json()["error"]["code"] == "not_found"
    r_req_b = c.post(
        "/api/v2/media/requests",
        json={"source_id": str(sys_v), "unit_id": "sample_road:0.000-0.000", "variant": "context"},
        headers=auth(b),
    )
    assert r_req_b.status_code == 404 and r_req_b.json()["error"]["code"] == "not_found"
    r_frame_b = c.get(f"/api/v2/media/requests/{frame_id}", headers=auth(b))
    assert r_frame_b.status_code == 404 and r_frame_b.json()["error"]["code"] == "not_found"

    # Kiểm GET /api/v2/uploads trả danh sách upload đang chạy/tạm dừng của owner để khôi phục sau reload
    u_created = c.post(
        "/api/v2/uploads",
        json={"filename": "clip1.mp4", "declared_bytes": 1024},
        headers=auth(a) | {"Idempotency-Key": "up-rec-1"},
    ).json()
    u_val = c.post(
        "/api/v2/uploads",
        json={"filename": "clip2.mp4", "declared_bytes": 2048},
        headers=auth(a) | {"Idempotency-Key": "up-rec-2"},
    )
    val_id = UUID(u_val.json()["id"])
    s3_storage.client.put_object(Bucket="rav-pilot-test-bucket", Key=s3_storage.staging_key(a, val_id), Body=b"x" * 2048)
    done = c.post(f"/api/v2/uploads/{val_id}/complete", headers=auth(a)).json()
    assert done["state"] == "VALIDATING"

    # Cập nhật progress_detail trên job VALIDATE_UPLOAD
    pg.sql(
        "UPDATE rav_pilot_job SET phase = 'EXTRACTING_FRAMES', done = 12, total = 40, "
        "progress_detail = '{\"fps\": 24.5, \"eta_s\": 3}' WHERE id = %s",
        (UUID(done["validation_job_id"]),),
    )

    uploads_a = c.get("/api/v2/uploads", headers=auth(a)).json()
    by_id = {u["id"]: u for u in uploads_a}
    assert u_created["id"] in by_id and by_id[u_created["id"]]["state"] == "CREATED"
    assert str(val_id) in by_id and by_id[str(val_id)]["state"] == "VALIDATING"
    assert by_id[str(val_id)]["progress"] == {"phase": "EXTRACTING_FRAMES", "done": 12, "total": 40, "fps": 24.5, "eta_s": 3}
    # User B không thấy upload của User A
    assert c.get("/api/v2/uploads", headers=auth(b)).json() == []


def test_lat3_system_ingest_admin_catalog_operations_and_seed(pg, s3_storage, tmp_path, monkeypatch):
    """Kiểm toàn diện Lát 3 (§21.36):
    1. Upload SYSTEM (chỉ admin, GLOBAL_ONLY storage hold, worker tạo nguồn/frame SYSTEM với owner_id=NULL, published=false).
    2. Admin gọi POST /api/v2/sources/{id}/preparations trên nguồn SYSTEM (user thường vẫn 404/409).
    3. GET/PATCH /api/v2/admin/sources & /api/v2/admin/models, kiểm readiness trước khi xuất bản, đồng bộ BLOCKED/VERIFIED cho demo template.
    4. GET /api/v2/operations (job_counts, queue_wait_ms, run_ms) & GET /api/v2/operations/logs (lọc upload_id, job_id, request_id).
    5. Script seed_sample_video nạp video mẫu thật, hai user tạo phiên độc lập không chạy lại prepare.
    """
    from datetime import UTC, datetime
    from uuid import UUID

    import requests
    from psycopg.types.json import Jsonb

    from scripts.pilot_seed_sample_catalog import seed_sample_video
    from src.rav.store.pilot_db import PilotDB
    from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
    from src.rav.worker.dispatcher import PilotDispatcher
    from src.rav.worker.handlers import make_handlers
    from tests.conftest import make_video

    pol = pg.policy()
    admin_uid = pg.profile(policy_id=pol)
    user_uid = pg.profile(policy_id=pol)
    pg.slot("cpu-sys-1")
    monkeypatch.setenv("RAV_PILOT_OPERATOR_IDS", str(admin_uid))

    c = TestClient(
        create_pilot_app(
            pg.api_url,
            AuthConfig(expected_issuer=ISS),
            raw_jwks={"keys": [JWK]},
            storage_factory=lambda: s3_storage,
        )
    )
    cpu_worker = PilotDispatcher(
        PilotRuntime(PilotDB(pg.worker_url)),
        WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()),
        "CPU",
        0.2,
    )
    for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "w-cpu").items():
        cpu_worker.register_handler(kind, fn)

    data = make_video(tmp_path / "sys_sample.mp4", seconds=2).read_bytes()

    # 1. User thường không được tạo upload SYSTEM (403 forbidden)
    r_forbid = c.post(
        "/api/v2/uploads",
        json={"filename": "sys_sample.mp4", "declared_bytes": len(data), "target_scope": "SYSTEM"},
        headers=auth(user_uid) | {"Idempotency-Key": "sys-up-forbid"},
    )
    assert r_forbid.status_code == 403 and r_forbid.json()["error"]["code"] == "forbidden"

    # Admin tạo upload SYSTEM → 201, GLOBAL_ONLY hold (không trừ hạn mức USER:<admin_uid>)
    r_up = c.post(
        "/api/v2/uploads",
        json={"filename": "sys_sample.mp4", "declared_bytes": len(data), "target_scope": "SYSTEM"},
        headers=auth(admin_uid) | {"Idempotency-Key": "sys-up-1"},
    )
    assert r_up.status_code == 201 and r_up.json()["target_scope"] == "SYSTEM"
    up_id = UUID(r_up.json()["id"])

    user_accs = pg.sql("SELECT persisted_bytes, reserved_bytes FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{admin_uid}",))
    assert all(r["persisted_bytes"] == 0 and r["reserved_bytes"] == 0 for r in user_accs)
    hold = pg.sql("SELECT account_scope, state FROM rav_pilot_storage_hold WHERE upload_id = %s", (up_id,))[0]
    assert hold["account_scope"] == "GLOBAL_ONLY" and hold["state"] == "OPEN"

    # Truyền file & complete → worker VALIDATE_UPLOAD tạo nguồn SYSTEM (owner_id=NULL, published=false) + frame set SYSTEM
    t = r_up.json()["transfer"]
    assert requests.post(t["url"], data=t["fields"], files={"file": ("sys_sample.mp4", data)}).status_code in (200, 204)
    r_comp = c.post(f"/api/v2/uploads/{up_id}/complete", headers=auth(admin_uid))
    assert r_comp.status_code == 202 and r_comp.json()["target_scope"] == "SYSTEM"
    cpu_worker.run_once()

    up_done = c.get(f"/api/v2/uploads/{up_id}", headers=auth(admin_uid)).json()
    assert up_done["state"] == "READY" and up_done["source_id"]
    sys_src_id = UUID(up_done["source_id"])

    src_row = pg.sql("SELECT scope, owner_id, protected, published, availability FROM rav_pilot_source_asset WHERE id = %s", (sys_src_id,))[0]
    assert src_row == {"scope": "SYSTEM", "owner_id": None, "protected": True, "published": False, "availability": "READY"}
    fset_row = pg.sql("SELECT scope, owner_id, state FROM rav_pilot_frame_set WHERE source_id = %s", (sys_src_id,))[0]
    assert fset_row == {"scope": "SYSTEM", "owner_id": None, "state": "READY"}
    user_accs_after = pg.sql("SELECT persisted_bytes, reserved_bytes FROM rav_pilot_storage_account WHERE account_key = %s", (f"USER:{admin_uid}",))
    assert all(r["persisted_bytes"] == 0 and r["reserved_bytes"] == 0 for r in user_accs_after)

    # 2. Admin gọi POST /api/v2/sources/{id}/preparations (DINO) trên nguồn SYSTEM → 202 Accepted
    r_prep_admin = c.post(
        f"/api/v2/sources/{sys_src_id}/preparations",
        json={"feature": "DINO"},
        headers=auth(admin_uid) | {"Idempotency-Key": "sys-prep-dino"},
    )
    assert r_prep_admin.status_code == 202 and r_prep_admin.json()["job"]["kind"] == "PREPARE_DINO"
    prep_job_id = UUID(r_prep_admin.json()["job"]["id"])
    prep_job = pg.sql("SELECT payload FROM rav_pilot_job WHERE id = %s", (prep_job_id,))[0]
    assert prep_job["payload"]["target_scope"] == "SYSTEM"

    # 3. Admin catalog API (/api/v2/admin/sources & /api/v2/admin/models)
    assert c.get("/api/v2/admin/sources", headers=auth(user_uid)).status_code == 403
    assert c.get("/api/v2/admin/models", headers=auth(user_uid)).status_code == 403

    admin_sources = c.get("/api/v2/admin/sources", headers=auth(admin_uid)).json()
    s_item = next(x for x in admin_sources["items"] if x["id"] == str(sys_src_id))
    assert s_item["publishable"] is True and s_item["published"] is False

    # Nguồn SYSTEM thiếu FRAMES thì không được xuất bản (409 source_not_publishable)
    unprep_sys = pg.source(None, "unprep_sys", name="Chưa có frame", published=False)
    r_unprep_pub = c.patch(
        f"/api/v2/admin/sources/{unprep_sys}",
        json={"published": True},
        headers=auth(admin_uid),
    )
    assert r_unprep_pub.status_code == 409 and r_unprep_pub.json()["error"]["code"] == "source_not_publishable"

    # Xuất bản sys_src_id và cập nhật tên + mô tả
    r_patch_src = c.patch(
        f"/api/v2/admin/sources/{sys_src_id}",
        json={"name": "Cao tốc Mẫu", "description": "Video mẫu đã chuẩn bị", "published": True},
        headers=auth(admin_uid),
    )
    assert r_patch_src.status_code == 200
    assert r_patch_src.json()["name"] == "Cao tốc Mẫu" and r_patch_src.json()["published"] is True

    # Tạo 1 demo template VERIFIED gắn với sys_src_id, sau đó ẩn sys_src_id → template chuyển sang BLOCKED (source_unpublished)
    tmpl_id = uuid4()
    pg.sql(
        "INSERT INTO rav_pilot_demo_template (id, template_key, name, legacy_session_id, manifest_hash, frozen_spec, "
        "source_mapping, provenance) VALUES (%s, 'tmpl-sys', 'Mẫu Cao tốc', 'legacy-s1', %s, %s, %s, %s)",
        (
            tmpl_id,
            "c" * 64,
            Jsonb({"goal": "g", "target_k": 2}),
            Jsonb([{"source_id": str(sys_src_id), "core_source_id": "core"}]),
            Jsonb({"seed_version": "v1"}),
        ),
    )
    pg.sql(
        "INSERT INTO rav_pilot_demo_template_status (template_id, availability, reason_code) "
        "VALUES (%s, 'VERIFIED', NULL)",
        (tmpl_id,),
    )

    # Ẩn sys_src_id → template bị BLOCKED (source_unpublished) và không fork được (404 nguồn ẩn hoặc 409 template)
    r_hide = c.patch(f"/api/v2/admin/sources/{sys_src_id}", json={"published": False}, headers=auth(admin_uid))
    assert r_hide.status_code == 200 and r_hide.json()["published"] is False
    t_stat = pg.sql("SELECT availability, reason_code FROM rav_pilot_demo_template_status WHERE template_id = %s", (tmpl_id,))[0]
    assert t_stat == {"availability": "BLOCKED", "reason_code": "source_unpublished"}
    r_fork_blocked = c.post(f"/api/v2/demo-templates/{tmpl_id}/fork", json={}, headers=auth(user_uid) | {"Idempotency-Key": "fk-blk"})
    assert r_fork_blocked.status_code in (404, 409)

    # Bật xuất bản lại sys_src_id → template tự khôi phục VERIFIED
    r_repub = c.patch(f"/api/v2/admin/sources/{sys_src_id}", json={"published": True}, headers=auth(admin_uid))
    assert r_repub.status_code == 200 and r_repub.json()["published"] is True
    t_stat2 = pg.sql("SELECT availability, reason_code FROM rav_pilot_demo_template_status WHERE template_id = %s", (tmpl_id,))[0]
    assert t_stat2 == {"availability": "VERIFIED", "reason_code": None}

    # Model SYSTEM: CONTAINER_ONLY → 409 model_not_publishable; INFERENCE_VERIFIED → xuất bản được
    mod_unverified = uuid4()
    mod_verified = uuid4()
    for mid, v_state in [(mod_unverified, "CONTAINER_ONLY"), (mod_verified, "INFERENCE_VERIFIED")]:
        pg.sql(
            "INSERT INTO rav_pilot_model_asset (id, core_model_id, scope, protected, name, description, published, "
            "raw_key, sha256, size_bytes, task, class_map, runtime_manifest, validation, checkpoint_revision, license, provenance) "
            "VALUES (%s, %s, 'SYSTEM', true, 'YOLO', '', false, 'k', %s, 100, 'detect', '{\"0\":\"car\"}', '{}', %s, 'r1', '{}', '{}')",
            (mid, f"core_{mid.hex[:8]}", mid.hex.ljust(64, "0"), v_state),
        )
    assert c.patch(f"/api/v2/admin/models/{mod_unverified}", json={"published": True}, headers=auth(admin_uid)).status_code == 409
    r_mod_pub = c.patch(
        f"/api/v2/admin/models/{mod_verified}",
        json={"name": "YOLO11n Giao thông", "description": "Đã kiểm chuẩn", "published": True},
        headers=auth(admin_uid),
    )
    assert r_mod_pub.status_code == 200 and r_mod_pub.json()["published"] is True and r_mod_pub.json()["name"] == "YOLO11n Giao thông"

    # 4. Operations overview (job_counts) & logs (upload_id, job_id, request_id)
    req_id = uuid4()
    now = datetime.now(UTC)
    pg.sql(
        "INSERT INTO rav_pilot_timing_event (id, received_at, observed_at, source, stage, request_id, job_id, upload_id, data) "
        "VALUES (%s, %s, %s, 'worker', 'worker.handler', %s, %s, %s, %s)",
        (uuid4(), now, now, req_id, prep_job_id, up_id, Jsonb({"wall_ms": 55, "outcome": "ok"})),
    )
    ops_res = c.get("/api/v2/operations", headers=auth(admin_uid)).json()
    assert "job_counts" in ops_res
    assert ops_res["job_counts"]["queued"] >= 1 and ops_res["job_counts"]["succeeded_24h"] >= 1
    assert any("queue_wait_ms" in j and "run_ms" in j for j in ops_res["jobs"])

    for param_k, param_v in [("upload_id", str(up_id)), ("job_id", str(prep_job_id)), ("request_id", str(req_id))]:
        lg = c.get("/api/v2/operations/logs", params={param_k: param_v}, headers=auth(admin_uid))
        assert lg.status_code == 200 and len(lg.json()["items"]) == 1
    assert c.get("/api/v2/operations/logs", params={"job_id": "not-a-uuid"}, headers=auth(admin_uid)).status_code == 422

    # 5. Test doubles stay in tests; production seeding must run real components.
    import numpy as np

    from src.rav.components.features.clip_b32 import ClipB32
    from src.rav.components.features.dinov2_s import DinoV2S
    from src.rav.components.features.phash import PHash
    from src.rav.core.types import FieldTable

    def mock_features(ctx, feature_name):
        classes = (DinoV2S, PHash) if feature_name == "DINO" else (ClipB32,)
        ids = [u.id for u in ctx.units]
        return tuple(FieldTable(cls().key, ids,
                     np.ones((len(ids), 384 if cls is DinoV2S else 512), np.float32)
                     if cls is not PHash else np.zeros(len(ids), np.uint64),
                     "scalar" if cls is PHash else "vector") for cls in classes)

    monkeypatch.setattr("scripts.pilot_seed_sample_catalog._build_feature_tables", mock_features)
    seed_res1 = seed_sample_video(
        PilotDB(pg.admin_url),
        s3_storage,
        video_path=tmp_path / "sys_sample.mp4",
        name="Mẫu Giao thông Seed",
        description="Seed mẫu",
    )
    seed_res2 = seed_sample_video(
        PilotDB(pg.admin_url),
        s3_storage,
        video_path=tmp_path / "sys_sample.mp4",
        name="Mẫu Giao thông Seed",
        description="Seed mẫu",
    )
    assert seed_res1["source_id"] == seed_res2["source_id"] == str(sys_src_id)
    assert seed_res1["published"] is True

    # Hai tài khoản (admin_uid và user_uid) cùng thấy nguồn mẫu đã xuất bản (FRAMES/DINO/CLIP đều ready) và tạo phiên riêng
    user_sources = c.get("/api/v2/sources", headers=auth(user_uid)).json()["items"]
    seeded = next(x for x in user_sources if x["id"] == str(sys_src_id))
    assert seeded["frames"]["ready"] and seeded["dino"]["ready"] and seeded["clip"]["ready"]

    jobs_before = pg.sql("SELECT count(*) AS n FROM rav_pilot_job")[0]["n"]
    sa = c.post(
        "/api/v2/sessions",
        json={"name": "Phiên A", "goal": "g", "target_k": 2, "pool_source_ids": [str(sys_src_id)]},
        headers=auth(admin_uid) | {"Idempotency-Key": "seed-sa"},
    ).json()
    sb = c.post(
        "/api/v2/sessions",
        json={"name": "Phiên B", "goal": "g", "target_k": 2, "pool_source_ids": [str(sys_src_id)]},
        headers=auth(user_uid) | {"Idempotency-Key": "seed-sb"},
    ).json()
    jobs_after = pg.sql("SELECT count(*) AS n FROM rav_pilot_job")[0]["n"]
    assert jobs_after == jobs_before  # không phát sinh job prepare mới

    unit_0 = f"{seeded['core_source_id']}:0.000-0.000"
    st_a = c.post(
        f"/api/v2/sessions/{sa['id']}/decisions",
        json={"changes": [{"unit_id": unit_0, "action": "keep"}], "expected_state_version": 1},
        headers=auth(admin_uid),
    ).json()
    st_b = c.get(f"/api/v2/sessions/{sb['id']}/state", headers=auth(user_uid)).json()
    assert st_a["kept_ids"] == [unit_0] and st_b["kept_ids"] == []


