"""L6 — API: smoke mọi endpoint architecture §5.2 (trừ views / metrics — L7), lỗi chuẩn, upload, media Range, khoá decoder (#49).

Dùng lại video PyAV + field giả của test_p5a (không chạy DINOv2).
"""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.rav.api import create_app
from src.rav.commands import COMMANDS
from src.rav.components.decoders.pyav import PyAV
from src.rav.pipeline import prepare as p1

from .test_p1 import make_video
from .test_p5a import app, video_id  # noqa: F401  (fixture `app`)


@pytest.fixture
def client(app):  # noqa: F811
    return TestClient(create_app(app), raise_server_exceptions=False)


def assert_error(res, status, code):
    assert res.status_code == status, res.text
    assert res.json()["error"]["code"] == code and res.json()["error"]["message"]


def test_review_flow_over_http(client, app):  # noqa: F811
    a = video_id(app, "a.mp4")
    videos = client.get("/api/v1/videos").json()
    assert {v["id"] for v in videos} == {a, video_id(app, "b.mp4")}          # mảng, không bọc object
    assert next(v for v in videos if v["id"] == a)["media_url"] == f"/media/videos/{a}"

    s = client.post("/api/v1/sessions", json={"pool": {"video_ids": [a]}, "goal": "khác biệt", "target_k": 3}).json()
    assert s["n_units"] == 12 and client.get(f"/api/v1/sessions/{s['id']}").json()["id"] == s["id"]
    assert [x["id"] for x in client.get("/api/v1/sessions").json()] == [s["id"]]
    units = client.get(f"/api/v1/sessions/{s['id']}/units", params={"video_id": a}).json()
    assert len(units) == 12

    frame = client.get(units[1]["thumb_url"])
    assert frame.headers["content-type"] == "image/jpeg" and "immutable" in frame.headers["cache-control"]
    assert frame.content[:2] == b"\xff\xd8"

    recipes = client.get("/api/v1/recipes").json()
    assert "diversity" in {r["name"] for r in recipes}
    assert client.get("/api/v1/components/objective/novelty").json()["doc"]

    base = f"/api/v1/sessions/{s['id']}"
    p = client.post(f"{base}/proposals", json={"recipe": "diversity", "batch_size": 4, "return_scores": True}).json()
    assert p["n"] == 1 and len(p["picks"]) == 4 and p["scores"]["unit_id"] == [pick['unit_id'] for pick in p['picks']]
    ids = [x["unit_id"] for x in p["picks"]]
    state = client.patch(f"{base}/state", json={"changes": [{"unit_id": ids[0], "action": "keep", "proposal_n": 1},
                                                            {"unit_id": ids[1], "action": "drop", "proposal_n": 1}]}).json()
    assert state["kept_ids"] == [ids[0]] and client.get(f"{base}/state").json() == state
    assert [x["n"] for x in client.get(f"{base}/proposals").json()] == [1]
    out = client.post(f"{base}/export").json()
    assert out["n_frames"] == 1 and out["path"].endswith("export.csv")
    assert [e["id"] for e in client.get("/api/v1/exports", params={"session_id": s["id"]}).json()] == [out["id"]]
    z = client.get(out["download_url"])                         # zip CSV + ảnh (#54)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip" and z.content[:2] == b"PK"
    assert_error(client.get(f"/api/v1/exports/{s['id']}/20990101T000000000Z/download"), 404, "export_not_found")
    b = video_id(app, "b.mp4")                                      # thêm video vào phiên (#56)
    s2 = client.post(f"{base}/videos", json={"video_ids": [b]}).json()
    assert b in s2["pool"]["video_ids"] and s2["n_units"] > s["n_units"]
    assert client.post(f"/api/v1/videos/{b}/prepare").json()["prepared"]   # đã chuẩn bị → trả video, không xếp job


def test_errors_are_uniform(client, app, monkeypatch):  # noqa: F811
    assert_error(client.get("/api/v1/sessions/nope"), 404, "session_not_found")
    assert_error(client.get("/api/v1/khong-co"), 404, "not_found")
    assert_error(client.post("/api/v1/sessions", json={"pool": {}, "goal": "g", "target_k": "abc"}), 422, "validation_error")
    assert_error(client.post("/api/v1/sessions", content=b"{hong", headers={"content-type": "application/json"}),
                 422, "validation_error")
    s = client.post("/api/v1/sessions", json={"pool": {"video_ids": [video_id(app, "a.mp4")]}, "goal": "g", "target_k": 1}).json()
    ids = [u["id"] for u in client.get(f"/api/v1/sessions/{s['id']}/units").json()]
    assert_error(client.patch(f"/api/v1/sessions/{s['id']}/state",
                              json={"changes": [{"unit_id": i, "action": "keep"} for i in ids[:2]]}), 422, "target_k_reached")
    assert_error(client.post(f"/api/v1/sessions/{s['id']}/proposals", json={"recipe": "nope"}), 404, "recipe_not_found")
    assert_error(client.get(f"/api/v1/videos/{ids[0].split(':')[0]}/frames/9999"), 404, "frame_not_found")

    def boom(self, inp, app):
        raise RuntimeError("chi tiết nội bộ")
    monkeypatch.setattr(type(COMMANDS["list_videos"]), "run", boom)
    res = client.get("/api/v1/videos")
    assert_error(res, 500, "internal_error")
    assert "chi tiết nội bộ" not in res.text


def test_upload_and_media_range(client, app, tmp_path):  # noqa: F811
    src = make_video(tmp_path / "c.mp4", seconds=1, shift=50)
    with src.open("rb") as f:
        v = client.post("/api/v1/videos", files={"file": ("c.mp4", f, "video/mp4")}).json()
    assert v["name"] == "c.mp4" and v["n_sessions"] == 0 and not v["prepared"]
    thumb = client.get(v["thumb_url"])                                      # frame giữa video (#57)
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert_error(client.post("/api/v1/videos", files={"file": ("x.mp4", b"khong phai video", "video/mp4")}), 400, "invalid_video")
    assert not list((app.output_dir / "tmp").iterdir())                      # file tạm luôn bị xoá

    app.max_upload_bytes = 1000
    with src.open("rb") as f:
        assert_error(client.post("/api/v1/videos", files={"file": ("d.mp4", f, "video/mp4")}), 400, "file_too_large")

    res = client.get(v["media_url"], headers={"Range": "bytes=0-99"})
    assert res.status_code == 206 and len(res.content) == 100 and res.content == src.read_bytes()[:100]
    assert_error(client.get("/media/videos/khongco"), 404, "video_not_found")


def test_decoder_parallel_reads_match_sequential(app):  # noqa: F811
    video = next(iter(p1.catalog(app.spec).values()))
    frames = PyAV().frames(video)
    expected = [PyAV().read(video, f).copy() for f in frames]
    PyAV._handles.clear()
    order = [frames[i] for i in np.random.default_rng(0).permutation(np.tile(np.arange(len(frames)), 4))]
    with ThreadPoolExecutor(8) as pool:                                       # lưới UI tải nhiều thumbnail song song
        got = list(pool.map(lambda f: (f.idx, PyAV().read(video, f).copy()), order))
    by_idx = {f.idx: e for f, e in zip(frames, expected)}
    assert all(np.array_equal(img, by_idx[idx]) for idx, img in got)
