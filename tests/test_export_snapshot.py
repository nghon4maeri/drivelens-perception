"""S8 T21–T25: ZIP bất biến, pixel đúng index, nguồn đổi/thiếu, giới hạn và cleanup cả khi ngắt gửi.

Video tổng hợp/PyAV và field giả; chạy python -m pytest tests/test_export_snapshot.py -q.
"""

import asyncio
import hashlib
import io
import json
import os
import uuid
import zipfile
from fractions import Fraction

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from src.rav.api import create_app
from src.rav.api.routes import _TemporaryFileResponse
from src.rav.components.decoders.base import Decoder
from src.rav.components.decoders.pyav import PyAV
from src.rav.core.types import Frame, VideoRef
from src.rav.service.export_snapshot import export_snapshot
from src.rav.service.snapshot import members_hash

from . import test_p5a as p5
from .test_datasets import image, yolo_root

app = p5.app


def freeze(app):
    session = p5.new_session(app)
    units = p5.run(app, "list_units", session_id=session.id).root
    ids = [units[i].id for i in (0, 3, 7)]
    p5.run(app, "update_state", session_id=session.id, changes=[{"unit_id": u, "action": "keep"} for u in ids])
    snapshot = p5.run(app, "create_snapshot", session_id=session.id, note="chốt")
    return session, snapshot, units


def temporary_files(app):
    return list((app.output_dir / "tmp").glob("snapshot-*.zip"))


def test_zip_uses_frozen_manifest_and_exact_pixels_after_kept_changes(app):
    session, snapshot, units = freeze(app)
    p5.run(app, "update_state", session_id=session.id, changes=[
        {"unit_id": units[0].id, "action": "drop"}, {"unit_id": units[1].id, "action": "keep"}])
    client = TestClient(create_app(app))
    assert client.post(f"/api/v1/snapshots/{snapshot.id}/verify").json() == {"ok": True}
    assert not temporary_files(app)
    response = client.get(f"/api/v1/snapshots/{snapshot.id}/download")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert not temporary_files(app)
    with av.open(str(app.videos_dir / "a.mp4")) as video:
        frames = [f.to_ndarray(format="rgb24") for f in video.decode(video=0)]
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["snapshot"]["id"] == snapshot.id
        assert [m["unit_id"] for m in manifest["members"]] == [units[i].id for i in (0, 3, 7)]
        assert len([n for n in z.namelist() if n.startswith("images/")]) == 3
        assert len(z.read("manifest.csv").decode().splitlines()) == 4
        for m in manifest["members"]:
            exported = np.array(Image.open(io.BytesIO(z.read(m["file"]))))
            assert np.array_equal(exported, frames[m["provenance"]["frame_idx"]])
    # Export kept cũ tiếp tục dùng tập hiện tại (T25), khác mẫu đầu của snapshot.
    legacy = p5.run(app, "export", session_id=session.id)
    assert legacy.n_frames == 3
    assert client.get("/api/v1/snapshots/not-a-snapshot/download").status_code == 404


@pytest.mark.parametrize("problem", ["missing", "changed", "changed_same_metadata"])
def test_unavailable_raw_has_details_and_never_creates_zip(app, problem):
    _, snapshot, _ = freeze(app)
    source = app.videos_dir / "a.mp4"
    if problem == "missing":
        source.unlink()
    elif problem == "changed_same_metadata":
        stat = source.stat()
        data = bytearray(source.read_bytes())
        data[len(data) // 2] ^= 1
        source.write_bytes(data)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        source.write_bytes(b"changed video bytes")
    client = TestClient(create_app(app))
    verified = client.post(f"/api/v1/snapshots/{snapshot.id}/verify")
    response = client.get(f"/api/v1/snapshots/{snapshot.id}/download")
    assert verified.status_code == 409 and verified.json() == response.json()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_unavailable"
    assert response.json()["error"]["sources"] == [{"source_id": snapshot_members_source(app, snapshot.id),
                                                   "kind": "video", "problem": "missing" if problem == "missing" else "changed", "n_samples": 3}]
    assert not temporary_files(app)


def snapshot_members_source(app, snapshot_id):
    return app.db.snapshot_members(snapshot_id)[0]["source_id"]


def test_size_limit_and_decode_failure_cleanup_partial_zip(app, monkeypatch):
    _, snapshot, _ = freeze(app)
    client = TestClient(create_app(app))
    url = f"/api/v1/snapshots/{snapshot.id}/download"
    app.max_snapshot_zip_bytes = 256
    limited = client.get(url)
    assert limited.status_code == 413 and limited.json()["error"]["code"] == "export_too_large"
    assert not temporary_files(app)
    app.max_snapshot_zip_bytes = 1 << 20
    read = PyAV.read_exact
    calls = []

    def fail_second(self, video, frame):
        calls.append(frame.idx)
        if len(calls) == 2:
            assert len(temporary_files(app)) == 1
            raise IndexError("frame test missing")
        return read(self, video, frame)

    monkeypatch.setattr(PyAV, "read_exact", fail_second)
    response = client.get(url)
    assert response.status_code == 409 and response.json()["error"]["code"] == "frame_mismatch"
    assert len(calls) == 2 and not temporary_files(app)
    monkeypatch.setattr(PyAV, "read_exact", Decoder.read_exact)
    response = client.get(url)
    assert response.status_code == 422 and response.json()["error"]["code"] == "unsupported_decoder"
    assert not temporary_files(app)


def test_exact_reader_rejects_out_of_range_and_handles_ui_interleave(app):
    _, _, units = freeze(app)
    video = p5.p1.catalog(app.spec)[units[0].video_id]

    decoder = PyAV()
    with av.open(video.uri) as c:
        frames = [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]
    assert np.array_equal(decoder.read_exact(video, Frame(video.id, 10, 1)), frames[10])
    decoder.read(video, Frame(video.id, 5, .5))
    assert np.array_equal(decoder.read_exact(video, Frame(video.id, 12, 1.2)), frames[12])
    with pytest.raises(IndexError):
        decoder.read_exact(video, Frame(video.id, len(frames), 3))


def test_exact_reader_counts_stream_indices_on_vfr_with_numbers(tmp_path):
    path = tmp_path / "vfr.mp4"
    with av.open(str(path), "w") as c:
        stream = c.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.codec_context.time_base = Fraction(1, 10)
        for index, pts in enumerate((0, 1, 4, 6)):
            image = Image.new("RGB", (64, 48), (index * 60, 20, 0))
            ImageDraw.Draw(image).text((10, 10), str(index), fill="white")
            frame = av.VideoFrame.from_ndarray(np.array(image), format="rgb24")
            frame.pts, frame.time_base = pts, Fraction(1, 10)
            c.mux(stream.encode(frame))
        c.mux(stream.encode())
    with av.open(str(path)) as c:
        decoded = list(c.decode(video=0))
    assert float(decoded[2].time) == .4  # index 2 khác timestamp × 10 = 4
    video = VideoRef("vfr", str(path), 10, .7)
    assert np.array_equal(p5.p1.read_frame(video, 2, p5.fresh_app(tmp_path).spec), decoded[2].to_ndarray(format="rgb24"))
    decoder = PyAV()
    for index in (0, 2, 3, 1):
        assert np.array_equal(decoder.read_exact(video, Frame(video.id, index, index / 10)),
                              decoded[index].to_ndarray(format="rgb24"))


def test_transport_cancel_and_boot_orphan_cleanup(app):
    _, snapshot, _ = freeze(app)
    result = export_snapshot(app, snapshot.id)
    response = _TemporaryFileResponse(result["path"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, send))
    assert not temporary_files(app)
    orphan = app.output_dir / "tmp" / f"snapshot-{uuid.uuid4().hex}.zip"
    other = orphan.with_name("unrelated.zip")
    orphan.write_bytes(b"partial")
    other.write_bytes(b"keep")
    create_app(app)
    assert not orphan.exists() and other.read_bytes() == b"keep"


def test_image_manifest_export_checks_original_bytes_and_labels(app, tmp_path):
    # Tương thích đọc source_kind đã có trong schema; không thêm ảnh vào pool API.
    app.datasets_dir = tmp_path / "datasets"
    app.datasets_dir.mkdir()
    root = yolo_root(app)
    raw = image(root, "images/a.png")
    label = root / "labels/a.txt"
    label.write_text("", encoding="utf-8")
    version = p5.run(app, "import_dataset", folder="tập ảnh", name="ref").version
    _, video_snapshot, _ = freeze(app)
    original = app.db.snapshot(video_snapshot.id)
    member = app.db.dataset_samples(version.id, 0, 1, None)[1][0]
    snapshot = {**original, "id": uuid.uuid4().hex, "members_hash": members_hash([member["sample_id"]])}
    snapshot, _ = app.db.add_snapshot(snapshot, [{"unit_id": member["sample_id"], "source_kind": "image_set",
        "source_id": version.id, "provenance": {"image": {"dataset_version_id": version.id,
        "relpath": member["relpath"], "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}}}])
    client = TestClient(create_app(app))
    url = f"/api/v1/snapshots/{snapshot['id']}/download"
    response = client.get(url)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert z.read(manifest["members"][0]["file"]) == raw.read_bytes()
    label.write_text("0 .5 .5 .2 .2", encoding="utf-8")
    assert client.get(url).json()["error"]["sources"][0]["problem"] == "changed"
    assert not temporary_files(app)
