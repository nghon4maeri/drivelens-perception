"""L8e — P1 chạy nền có tiến độ, thêm video vào phiên có sẵn (#56).

Dùng fixture của test_p5a (a.mp4, b.mp4 đã có field giả). Video mới chỉ tính `hash.phash` (FIELDS thu nhỏ) để không chạy DINOv2.
"""

import time

import pytest

from src.rav.commands import ServiceError
from src.rav.pipeline import prepare as p1

from .test_p1 import make_video
from .test_p5a import app, new_session, run, video_id  # noqa: F401  (fixture `app`)


@pytest.fixture
def phash_only(monkeypatch):
    monkeypatch.setattr(p1, "FIELDS", ("hash.phash",))


def wait_job(app, vid, timeout=60):  # noqa: F811
    end = time.time() + timeout
    while time.time() < end:
        job = app.prepare_jobs.get(vid)
        if job and job["state"] in ("done", "error"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job P1 của {vid} chưa xong sau {timeout} s")


def test_progress_counts_pixel_reads(app, phash_only, tmp_path):  # noqa: F811
    make_video(app.videos_dir / "c.mp4", seconds=2, shift=50)
    video = next(v for v in p1.catalog(app.spec).values() if v.meta["name"] == "c.mp4")
    calls = []
    ctx = p1.prepare([video], app.spec, app.fields, on_progress=lambda f, d, t: calls.append((f, d, t)))
    n = len(ctx.units)
    assert calls[0] == ("hash.phash", 0, n) and calls[-1] == ("hash.phash", n, n)
    assert all(0 <= d <= t == n for _, d, t in calls)


def test_upload_only_stores_then_explicit_prepare(app, phash_only, tmp_path):  # noqa: F811
    app.auto_prepare = True
    src = make_video(tmp_path / "c.mp4", seconds=2, shift=50)
    v = run(app, "upload_video", path=str(src), filename="c.mp4")
    assert not v.prepared and v.prepare is None and v.clip_prepare is None
    assert v.id not in app.prepare_jobs and v.id not in app.clip_prepare_jobs
    run(app, "list_videos")
    assert not app.prepare_jobs
    run(app, "queue_prepare", video_id=v.id)
    job = wait_job(app, v.id)
    assert job["state"] == "done" and job["stage"] == "hash.phash" and job["done"] == job["total"] > 0
    listed = next(x for x in run(app, "list_videos").root if x.id == v.id)
    assert listed.prepared and listed.prepare.state == "done"
    run(app, "queue_prepare", video_id=v.id)                       # đã chuẩn bị → không xếp lại
    assert app.prepare_jobs[v.id]["state"] == "done"


def test_add_videos_to_session(app):  # noqa: F811
    s = new_session(app)
    p = run(app, "propose", session_id=s.id, recipe="random", batch_size=2, seed=0)
    b = video_id(app, "b.mp4")
    out = run(app, "add_videos_to_session", session_id=s.id, video_ids=[b])
    assert set(out.pool.video_ids) == {video_id(app, "a.mp4"), b} and out.n_units > s.n_units
    assert {u.video_id for u in run(app, "list_units", session_id=s.id).root} == set(out.pool.video_ids)
    assert run(app, "list_proposals", session_id=s.id).root[0].picks == p.picks      # đề xuất cũ giữ nguyên
    run(app, "propose", session_id=s.id, recipe="random", batch_size=3, seed=1)       # đề xuất mới trên pool mới

    make_video(app.videos_dir / "c.mp4", seconds=1, shift=50)
    c = video_id(app, "c.mp4")
    with pytest.raises(ServiceError) as e:
        run(app, "add_videos_to_session", session_id=s.id, video_ids=[c])
    assert (e.value.code, e.value.status) == ("video_not_prepared", 409)
    app.prepare_jobs[c] = {"state": "running", "stage": "emb.dinov2_s", "done": 1, "total": 4, "started_at": None, "error": None}
    for cmd, kw in (("add_videos_to_session", {"session_id": s.id, "video_ids": [c]}),
                    ("create_session", {"pool": {"video_ids": [c]}, "goal": "g", "target_k": 1})):
        with pytest.raises(ServiceError) as e:
            run(app, cmd, **kw)
        assert (e.value.code, e.value.status) == ("video_preparing", 409)
