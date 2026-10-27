"""Release regressions for split-host completion, concurrent writes and capability revocation."""
import io
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from starlette.testclient import TestClient

from src.rav.api.pilot import create_pilot_app
from src.rav.errors import ServiceError
from src.rav.store.pilot_uploads import UploadIn
from src.rav.store.pilot_vps_disk import (
    VPSDiskUpload,
    create_vps_capability,
    get_vps_upload_secret,
    verify_vps_capability,
)
from tests import test_pilot_vps_upload as vps_tests

actor = vps_tests.actor
vps_env = vps_tests.vps_env
vps_test_secret = vps_tests.vps_test_secret


def test_explicit_secret_required(monkeypatch):
    monkeypatch.delenv("RAV_VPS_UPLOAD_SECRET")
    with pytest.raises(ServiceError, match="RAV_VPS_UPLOAD_SECRET"):
        get_vps_upload_secret()


def test_seal_and_revoke_block_browser_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("RAV_VPS_UPLOAD_DIR", str(tmp_path))
    uid, owner = uuid4(), uuid4()
    token = create_vps_capability(uid, owner, 10, time.time() + 300)
    client = TestClient(create_pilot_app(database_url="postgresql://fake:5432/fake"))
    url = f"/api/v2/vps-uploads/{uid}"
    headers = {"Authorization": "Bearer " + token, "Upload-Offset": "0"}
    assert client.post(url + "/control/cleanup", headers=headers).status_code == 403
    assert client.put(url + "/chunks", headers=headers, content=b"0123456789").status_code == 200
    seal = create_vps_capability(uid, owner, 10, time.time() + 60, purpose="seal")
    assert client.post(url + "/control/seal", headers={"Authorization": "Bearer " + seal}).status_code == 200
    assert client.put(url + "/chunks", headers=headers, content=b"0123456789").status_code == 409
    disk = VPSDiskUpload(uid)
    disk.cleanup()
    assert client.put(url + "/chunks", headers=headers, content=b"0123456789").status_code == 409
    assert not disk.part_path.exists()


def test_concurrent_retry_commits_one_chunk(tmp_path, monkeypatch):
    monkeypatch.setenv("RAV_VPS_UPLOAD_DIR", str(tmp_path))
    uid = uuid4()
    token = create_vps_capability(uid, uuid4(), 20, time.time() + 300)
    app = create_pilot_app(database_url="postgresql://fake:5432/fake")
    def write():
        with TestClient(app) as client:
            return client.put(f"/api/v2/vps-uploads/{uid}/chunks", content=b"A" * 10,
                              headers={"Authorization": "Bearer " + token, "Upload-Offset": "0"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == [200, 409]
    disk = VPSDiskUpload(uid)
    assert disk.get_status(20)["offset"] == 10
    assert disk.part_path.read_bytes() == b"A" * 10


def test_oversize_chunk_does_not_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("RAV_VPS_UPLOAD_DIR", str(tmp_path))
    uid = uuid4()
    token = create_vps_capability(uid, uuid4(), 32 << 20, time.time() + 300)
    with TestClient(create_pilot_app(database_url="postgresql://fake:5432/fake")) as client:
        result = client.put(f"/api/v2/vps-uploads/{uid}/chunks", content=b"x" * ((16 << 20) + 1),
                            headers={"Authorization": "Bearer " + token, "Upload-Offset": "0"})
    assert result.status_code == 413
    assert VPSDiskUpload(uid).get_status(32 << 20)["offset"] == 0


def test_complete_on_render_uses_remote_disk(vps_env, tmp_path, monkeypatch):
    service, uid = vps_env["svc"], vps_env["uid"]
    raw = vps_env["video"].read_bytes()
    created = service.create(actor(uid), UploadIn(filename="split-host.mp4", declared_bytes=len(raw),
                                                  preferred_transport="VPS_DISK"), "split-host")
    remote_dir = tmp_path / "contabo"
    disk = VPSDiskUpload(created.id, work_dir=remote_dir)
    disk.write_chunk(len(raw), 0, io.BytesIO(raw))
    monkeypatch.setenv("RAV_VPS_UPLOAD_DIR", str(tmp_path / "render"))
    monkeypatch.setenv("RAV_VPS_UPLOAD_URL", "https://upload.example.test")
    monkeypatch.delenv("RAV_VPS_UPLOAD_LOCAL", raising=False)
    class Remote:
        def __init__(self, **kwargs):
            assert kwargs["verify"] is not False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, headers):
            assert url.endswith(f"/{created.id}/control/seal")
            verify_vps_capability(headers["Authorization"][7:], created.id, purpose="seal")
            return httpx.Response(200, json=disk.seal(len(raw)))
    monkeypatch.setattr(httpx, "Client", Remote)
    completed = service.complete(actor(uid), created.id)
    assert completed.state == "VALIDATING"
    assert not VPSDiskUpload(created.id).part_path.exists()
    assert disk.part_path.exists()
    monkeypatch.setenv("RAV_VPS_UPLOAD_DIR", str(remote_dir))
    assert vps_env["dispatch"].run_once()
    assert service.get(actor(uid), created.id).state == "READY"
    assert not disk.part_path.exists()


def test_only_one_active_vps_upload(vps_env):
    service, uid = vps_env["svc"], vps_env["uid"]
    request = UploadIn(filename="one.mp4", declared_bytes=100, preferred_transport="VPS_DISK")
    first = service.create(actor(uid), request, "first-vps")
    with pytest.raises(ServiceError) as exc:
        service.create(actor(uid), request, "second-vps")
    assert exc.value.status == 429
    service.abort(actor(uid), first.id)
    assert service.create(actor(uid), request, "second-vps").state == "CREATED"


def test_render_does_not_accept_binary_upload(monkeypatch):
    monkeypatch.setenv("RAV_VPS_UPLOAD_URL", "https://upload.example.test")
    monkeypatch.delenv("RAV_VPS_UPLOAD_LOCAL", raising=False)
    app = create_pilot_app(database_url="postgresql://fake:5432/fake")
    assert not any("vps-uploads" in getattr(route, "path", "") for route in app.routes)


def test_corrupt_metadata_does_not_truncate_acknowledged_bytes(tmp_path):
    disk = VPSDiskUpload(uuid4(), work_dir=tmp_path)
    disk.write_chunk(20, 0, b"0123456789")
    disk.meta_path.write_text("corrupted", encoding="utf-8")
    with pytest.raises(ServiceError):
        disk.get_status(20)
    assert disk.part_path.read_bytes() == b"0123456789"
