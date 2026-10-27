"""Kiểm thử toàn diện luồng VPS upload → ingest S3 → READY (§21.30, #139).

Bao quát:
1. Token capability HMAC: ký, giải mã, hết hạn, giả mạo, sai upload_id.
2. VPSDiskUpload ghi tuần tự: fsync đĩa, metadata .meta atomic.
3. Từ chối lệch offset (409 offset_mismatch) kèm expected_offset.
4. Phục hồi sự cố (crash recovery / restart): tự động truncate phần chưa ack về durable offset.
5. HTTP Endpoints: GET /vps-uploads/{id} và PUT /vps-uploads/{id}/chunks.
6. Toàn luồng: Tạo upload VPS_DISK → ghi chunks → complete → worker ingest stream S3 → READY.
7. Dọn dẹp đĩa VPS: xóa .part/.meta sau khi publish READY.
8. Lỗi S3: giữ nguyên .part để retry, dọn dẹp khi abort/sweep staging quá hạn.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from src.rav.actor import ActorContext
from src.rav.api.pilot import create_pilot_app
from src.rav.errors import ServiceError
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
from src.rav.store.pilot_uploads import PilotUploads, UploadIn
from src.rav.store.pilot_vps_disk import (
    VPSDiskUpload,
    create_vps_capability,
    verify_vps_capability,
)
from src.rav.worker.dispatcher import PilotDispatcher
from src.rav.worker.handlers import make_handlers
from tests.conftest import POLICY_CONFIG, make_video


def actor(uid: UUID) -> ActorContext:
    return ActorContext(user_id=uid)


@pytest.fixture(autouse=True)
def vps_test_secret(monkeypatch):
    monkeypatch.setenv("RAV_VPS_UPLOAD_SECRET", "vps-test-secret-explicit-32-characters")
    monkeypatch.delenv("RAV_VPS_UPLOAD_URL", raising=False)
    monkeypatch.delenv("RAV_RUNTIME_MODE", raising=False)


# -----------------------------------------------------------------------------
# 1. Unit tests: Capability Token
# -----------------------------------------------------------------------------
def test_vps_capability_token():
    upload_id = uuid4()
    owner_id = uuid4()
    declared_bytes = 1024 * 1024 * 50
    now = time.time()

    token = create_vps_capability(upload_id, owner_id, declared_bytes, now + 3600)
    payload = verify_vps_capability(token, upload_id)
    assert payload["uid"] == str(upload_id)
    assert payload["owner"] == str(owner_id)
    assert payload["bytes"] == declared_bytes

    # Hết hạn
    expired_token = create_vps_capability(upload_id, owner_id, declared_bytes, now - 10)
    with pytest.raises(ServiceError) as exc:
        verify_vps_capability(expired_token, upload_id)
    assert exc.value.code == "upload_invalid"

    # Sai upload_id
    with pytest.raises(ServiceError) as exc:
        verify_vps_capability(token, uuid4())
    assert exc.value.code == "forbidden"

    # Giả mạo token
    b64, sig = token.split(".", 1)
    tampered = f"{b64}.deadbeef{sig[8:]}"
    with pytest.raises(ServiceError) as exc:
        verify_vps_capability(tampered, upload_id)
    assert exc.value.code == "unauthorized"


# -----------------------------------------------------------------------------
# 2. Unit tests: Sequential Write, Offset Mismatch & Durability
# -----------------------------------------------------------------------------
def test_vps_disk_sequential_write_and_mismatch(tmp_path: Path):
    upload_id = uuid4()
    declared_bytes = 1000
    vps = VPSDiskUpload(upload_id, work_dir=tmp_path)

    # Khởi tạo trạng thái ban đầu
    status = vps.get_status(declared_bytes)
    assert status["offset"] == 0
    assert not status["completed"]

    # Ghi chunk 1: 400 bytes
    c1 = b"A" * 400
    offset1 = vps.write_chunk(declared_bytes, 0, io.BytesIO(c1))
    assert offset1 == 400
    assert vps.get_status(declared_bytes)["offset"] == 400

    # Gửi sai offset (vd gửi offset 0 hoặc 500 thay vì 400)
    with pytest.raises(ServiceError) as exc:
        vps.write_chunk(declared_bytes, 0, io.BytesIO(b"dup"))
    assert exc.value.code == "offset_mismatch"
    assert exc.value.details["expected_offset"] == 400

    with pytest.raises(ServiceError) as exc:
        vps.write_chunk(declared_bytes, 500, io.BytesIO(b"gap"))
    assert exc.value.code == "offset_mismatch"
    assert exc.value.details["expected_offset"] == 400

    # Ghi chunk 2: 600 bytes -> hoàn thành
    c2 = b"B" * 600
    offset2 = vps.write_chunk(declared_bytes, 400, io.BytesIO(c2))
    assert offset2 == 1000

    status_final = vps.get_status(declared_bytes)
    assert status_final["offset"] == 1000
    assert status_final["completed"]
    assert vps.part_path.read_bytes() == c1 + c2


# -----------------------------------------------------------------------------
# 3. Crash Recovery / Restart: Truncate unacknowledged trailing bytes
# -----------------------------------------------------------------------------
def test_vps_disk_crash_recovery(tmp_path: Path):
    upload_id = uuid4()
    declared_bytes = 1000
    vps = VPSDiskUpload(upload_id, work_dir=tmp_path)

    # Ghi thành công 500 bytes được fsync và ack
    c1 = b"X" * 500
    vps.write_chunk(declared_bytes, 0, io.BytesIO(c1))
    assert vps.get_status(declared_bytes)["offset"] == 500

    # Giả lập crash giữa chừng lúc ghi chunk tiếp theo:
    # 200 bytes bị ghi dở xuống file vật lý nhưng server đứt điện/crash trước khi ack/ghi .meta
    with vps.part_path.open("a+b") as f:
        f.write(b"CORRUPTED_TRAIL" * 15)  # 225 bytes un-acked
    assert vps.part_path.stat().st_size == 725

    # Server / daemon khởi động lại: get_status() tự động truncate về durable offset (500)
    recovered_status = vps.get_status(declared_bytes)
    assert recovered_status["offset"] == 500
    assert vps.part_path.stat().st_size == 500
    assert vps.part_path.read_bytes() == c1

    # Browser resume gửi tiếp từ offset 500 bình thường
    c2 = b"Y" * 500
    new_offset = vps.write_chunk(declared_bytes, 500, io.BytesIO(c2))
    assert new_offset == 1000
    assert vps.get_status(declared_bytes)["completed"]
    assert vps.part_path.read_bytes() == c1 + c2


# -----------------------------------------------------------------------------
# 4. HTTP API Endpoints: GET & PUT chunks
# -----------------------------------------------------------------------------
def test_vps_upload_http_api(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RAV_VPS_UPLOAD_DIR", str(tmp_path))
    app = create_pilot_app(database_url="postgresql://fake:5432/fake")
    client = TestClient(app, raise_server_exceptions=False)

    upload_id = uuid4()
    owner_id = uuid4()
    declared_bytes = 200
    token = create_vps_capability(upload_id, owner_id, declared_bytes, time.time() + 300)

    # 1. GET status khi chưa có dữ liệu
    r = client.get(f"/api/v2/vps-uploads/{upload_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["offset"] == 0

    # 2. PUT chunk 1 (100 bytes)
    c1 = b"1" * 100
    r = client.put(
        f"/api/v2/vps-uploads/{upload_id}/chunks",
        headers={"Authorization": f"Bearer {token}", "Upload-Offset": "0"},
        content=c1,
    )
    assert r.status_code == 200
    assert r.json()["offset"] == 100

    # 3. PUT sai offset -> 409
    r = client.put(
        f"/api/v2/vps-uploads/{upload_id}/chunks",
        headers={"Authorization": f"Bearer {token}", "Upload-Offset": "0"},
        content=c1,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "offset_mismatch"
    assert r.json()["error"]["expected_offset"] == 100

    # 4. PUT chunk 2 (100 bytes) -> hoàn tất
    c2 = b"2" * 100
    r = client.put(
        f"/api/v2/vps-uploads/{upload_id}/chunks",
        headers={"Authorization": f"Bearer {token}", "Upload-Offset": "100"},
        content=c2,
    )
    assert r.status_code == 200
    assert r.json()["offset"] == 200

    # 5. Kiểm tra GET status hoàn thành
    r = client.get(f"/api/v2/vps-uploads/{upload_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["offset"] == 200
    assert r.json()["completed"] is True


# -----------------------------------------------------------------------------
# 5. Full Pipeline: VPS_DISK → Ingest S3 → READY & Disk Cleanup
# -----------------------------------------------------------------------------
@pytest.fixture
def vps_env(pg, s3_storage, tmp_path, monkeypatch):
    monkeypatch.setenv("RAV_VPS_UPLOAD_DIR", str(tmp_path / "vps_data"))
    config = dict(POLICY_CONFIG, storage_user_bytes=20 << 20, vps_upload_enabled=True)
    policy = pg.policy(config=config)
    uid = pg.profile(policy_id=policy)
    pg.slot("cpu-vps-1")
    worker = PilotRuntime(PilotDB(pg.worker_url))
    d = PilotDispatcher(worker, WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "CPU", heartbeat_s=0.2)
    for kind, fn in make_handlers(s3_storage, PilotDB(pg.worker_url), tmp_path / "work").items():
        d.register_handler(kind, fn)
    video = make_video(tmp_path / "clip.mp4", seconds=4, fps=10)
    return {
        "pg": pg,
        "s3": s3_storage,
        "uid": uid,
        "svc": PilotUploads(PilotDB(pg.api_url), lambda: s3_storage),
        "dispatch": d,
        "video": video,
        "vps_dir": tmp_path / "vps_data",
    }


def test_vps_upload_full_pipeline_and_disk_cleanup(vps_env):
    pg = vps_env["pg"]
    uid = vps_env["uid"]
    svc = vps_env["svc"]
    dispatch = vps_env["dispatch"]
    video_bytes = vps_env["video"].read_bytes()
    declared_bytes = len(video_bytes)

    # 1. Tạo upload VPS_DISK
    created = svc.create(actor(uid), UploadIn(filename="test.mp4", declared_bytes=declared_bytes, preferred_transport="VPS_DISK"), "vps-full-1")
    assert created.transfer.method == "VPS_DISK"
    upload_id = created.id

    vps = VPSDiskUpload(upload_id, work_dir=vps_env["vps_dir"])
    assert vps.part_path.parent.exists()

    # 2. Truyền chunk lên đĩa VPS
    half = declared_bytes // 2
    vps.write_chunk(declared_bytes, 0, io.BytesIO(video_bytes[:half]))
    vps.write_chunk(declared_bytes, half, io.BytesIO(video_bytes[half:]))
    assert vps.get_status(declared_bytes)["completed"] is True
    assert vps.part_path.exists()
    assert vps.meta_path.exists()

    # 3. Gọi complete
    comp = svc.complete(actor(uid), upload_id)
    assert comp.state == "VALIDATING"
    assert comp.validation_job_id is not None

    # 4. Worker ingest S3 và extract frames
    assert dispatch.run_once() is True

    # 5. Upload chuyển sang READY
    up_done = svc.get(actor(uid), upload_id)
    assert up_done.state == "READY"
    assert up_done.source_id is not None

    # 6. Nguồn PRIVATE đã READY trên DB
    sources = pg.sql("SELECT id, availability, raw_key FROM rav_pilot_source_asset WHERE id=%s", (up_done.source_id,))
    assert len(sources) == 1
    assert sources[0]["availability"] == "READY"

    # Canonical video đã nằm an toàn trên S3
    head = vps_env["s3"].head(sources[0]["raw_key"])
    assert head is not None
    assert head["ContentLength"] == declared_bytes

    # 7. QUAN TRỌNG: File .part và .meta trên đĩa VPS đã được xóa sạch để giải phóng đĩa!
    assert not vps.part_path.exists()
    assert not vps.meta_path.exists()


# -----------------------------------------------------------------------------
# 6. S3 Transient Error: Giữ nguyên .part trên đĩa VPS để retry an toàn
# -----------------------------------------------------------------------------
def test_vps_upload_s3_error_preserves_part_file(vps_env, monkeypatch):
    uid = vps_env["uid"]
    svc = vps_env["svc"]
    video_bytes = vps_env["video"].read_bytes()
    declared_bytes = len(video_bytes)

    created = svc.create(actor(uid), UploadIn(filename="test_err.mp4", declared_bytes=declared_bytes, preferred_transport="VPS_DISK"), "vps-err-1")
    upload_id = created.id
    vps = VPSDiskUpload(upload_id, work_dir=vps_env["vps_dir"])
    vps.write_chunk(declared_bytes, 0, io.BytesIO(video_bytes))

    comp = svc.complete(actor(uid), upload_id)
    assert comp.state == "VALIDATING"

    # Claim lease
    runtime = vps_env["dispatch"].runtime
    worker_ctx = vps_env["dispatch"].worker_ctx
    lease = runtime.claim(worker_ctx, queue="CPU")
    assert lease is not None
    assert lease.job_id == comp.validation_job_id

    # Lấy handler validate_upload
    handler = vps_env["dispatch"]._handlers["VALIDATE_UPLOAD"]

    # Giả lập S3 put_file bị lỗi mạng tạm thời
    def broken_put_file(*args, **kwargs):
        raise ServiceError("s3_network_error", "Mất kết nối S3 tạm thời", 503)

    monkeypatch.setattr(vps_env["s3"], "put_file", broken_put_file)

    import threading
    stop = threading.Event()
    with pytest.raises(ServiceError) as exc:
        handler(lease, stop)
    assert exc.value.code == "s3_network_error"

    # KIỂM CHỨNG BẮT BUỘC: Khi attempt handler lỗi S3 tạm thời, file .part KHÔNG BỊ XÓA
    # để attempt retry tiếp theo có thể tiếp tục mà không cần client upload lại!
    assert vps.part_path.exists()
    assert vps.part_path.stat().st_size == declared_bytes

    # Khi job thất bại vĩnh viễn (finish_in_tx FAILED), file .part được dọn dẹp sạch
    with PilotDB(vps_env["pg"].worker_url).backend() as cur:
        from src.rav.store.pilot_uploads import finish_in_tx
        finish_in_tx(cur, {"id": lease.job_id, "kind": "VALIDATE_UPLOAD", "payload": lease.payload}, "FAILED", {"code": "s3_fatal"})
        assert vps.part_path.exists()  # transaction rollback must preserve bytes
    vps_env["dispatch"]._cleanup_vps(lease)
    assert not vps.part_path.exists()


# -----------------------------------------------------------------------------
# 7. Abort & Sweep Staging: Dọn dẹp sạch đĩa VPS
# -----------------------------------------------------------------------------
def test_vps_upload_abort_and_sweep(vps_env):
    uid = vps_env["uid"]
    svc = vps_env["svc"]
    video_bytes = vps_env["video"].read_bytes()
    declared_bytes = len(video_bytes)

    # 1. Abort dọn dẹp đĩa
    c1 = svc.create(actor(uid), UploadIn(filename="ab.mp4", declared_bytes=declared_bytes, preferred_transport="VPS_DISK"), "vps-ab-1")
    vps1 = VPSDiskUpload(c1.id, work_dir=vps_env["vps_dir"])
    vps1.write_chunk(declared_bytes, 0, io.BytesIO(video_bytes[:100]))
    assert vps1.part_path.exists()

    aborted = svc.abort(actor(uid), c1.id)
    assert aborted.state == "ABORTED"
    assert not vps1.part_path.exists()
    assert not vps1.meta_path.exists()

    # 2. Sweep staging dọn dẹp đĩa khi quá hạn
    c2 = svc.create(actor(uid), UploadIn(filename="sw.mp4", declared_bytes=declared_bytes, preferred_transport="VPS_DISK"), "vps-sw-1")
    vps2 = VPSDiskUpload(c2.id, work_dir=vps_env["vps_dir"])
    vps2.write_chunk(declared_bytes, 0, io.BytesIO(video_bytes[:100]))
    assert vps2.part_path.exists()

    # Giả lập hết hạn capability
    vps_env["pg"].sql("UPDATE rav_pilot_upload SET capability_expires_at = now() - interval '1 hour' WHERE id=%s", (c2.id,))
    swept = svc.sweep_staging()
    assert c2.id in swept
    assert not vps2.part_path.exists()
    assert not vps2.meta_path.exists()
