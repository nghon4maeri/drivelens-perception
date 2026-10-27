"""S5–S6: verify raw/nhãn, prepare/job và reference phiên, không tải model mạng."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.rav.api import create_app
from src.rav.core.types import FieldTable
from src.rav.service import App
from src.rav.service import dataset as svc

from .test_datasets import image, import_new, run, yolo_root


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "datasets"
    root.mkdir()
    return App(tmp_path / "videos", tmp_path / "output", 1 << 20, datasets_dir=root)


def test_first_parallel_requests_on_new_database(app):
    from concurrent.futures import ThreadPoolExecutor

    client = TestClient(create_app(app))
    with ThreadPoolExecutor(max_workers=8) as workers:
        responses = list(workers.map(client.get, ["/api/v1/videos", "/api/v1/sessions", "/api/v1/datasets"] * 4))
    assert all(r.status_code == 200 and r.json() == [] for r in responses)
    image(app.datasets_dir / "tập ảnh", "a.png")
    out = import_new(app)
    assert client.get("/api/v1/datasets").json()[0]["versions"][0]["id"] == out.version.id


@pytest.fixture
def fake_embedding(monkeypatch):
    from src.rav.components.features.dinov2_s import DinoV2S

    calls = []
    def compute(self, units, ctx):
        calls.append([u.id for u in units])
        for u in units:
            ctx.pixels(u.anchor)
        return FieldTable(self.key, [u.id for u in units], np.tile([1., 0., 0.], (len(units), 1)), "vector")
    monkeypatch.setattr(DinoV2S, "compute", compute)
    return calls


def test_prepare_cache_and_verify_before_cache_hit(app, fake_embedding):
    root = yolo_root(app)
    image(root, "images/a.png")
    (root / "labels/a.txt").write_text("", encoding="utf-8")
    out = import_new(app)
    client = TestClient(create_app(app))
    url = f"/api/v1/datasets/versions/{out.version.id}"
    assert client.post(url + "/verify").json() == {"ok": True, "missing": [], "changed": []}
    svc.prepare_dataset(app, out.version.id)
    assert client.get(url).json()["prepared"]
    svc.prepare_dataset(app, out.version.id)
    assert len(fake_embedding) == 1
    (root / "labels/a.txt").write_text("0 .5 .5 .2 .2", encoding="utf-8")
    assert client.post(url + "/verify").json()["changed"] == ["labels/a.txt"]
    with pytest.raises(Exception, match="nguồn dataset"):
        svc.prepare_dataset(app, out.version.id)
    (root / "images/a.png").unlink()
    assert client.post(url + "/verify").json()["missing"] == ["images/a.png"]
    assert client.get(url + f"/images/{app.db.dataset_samples(out.version.id, 0, 1, None)[1][0]['key']}").status_code == 404


def test_verify_conflict_sources_and_missing_label_that_appears(app):
    root = yolo_root(app)
    a = image(root, "images/a.png")
    (root / "images/b.png").write_bytes(a.read_bytes())
    (root / "labels/a.txt").write_text("", encoding="utf-8")
    out = import_new(app)
    assert svc.verify(app, out.version.id)["ok"]
    (root / "labels/b.txt").write_text("", encoding="utf-8")
    assert svc.verify(app, out.version.id)["changed"] == ["labels/b.txt"]
    (root / "classes.txt").write_text("truck", encoding="utf-8")
    assert run(app, "get_dataset_version", version_id=out.version.id).classes == ["car", "person"]
    (root / "images/b.png").unlink()
    assert svc.verify(app, out.version.id)["missing"] == ["images/b.png"]


def test_queue_auto_prepare_no_duplicate_job_and_error_status(app, fake_embedding):
    import queue
    app.prepare_queue = queue.Queue()
    app.auto_prepare = True
    source = image(app.datasets_dir / "tập ảnh", "a.png")
    out = import_new(app)
    assert out.version.prepare.state == "queued"
    run(app, "queue_prepare_dataset", version_id=out.version.id)
    assert app.prepare_queue.qsize() == 1
    app.prepare_queue.get()()
    status = run(app, "get_dataset_version", version_id=out.version.id)
    assert status.prepared and status.prepare.state == "done"
    source.unlink()
    run(app, "queue_prepare_dataset", version_id=out.version.id)
    app.prepare_queue.get()()
    assert "dataset_file_missing" in run(app, "get_dataset_version", version_id=out.version.id).prepare.error


def test_reference_validation_snapshot_and_old_session_compat(app, fake_embedding):
    from src.rav.core.registry import REGISTRY, discover
    from src.rav.pipeline import prepare as p1

    from .test_p1 import make_video

    app.videos_dir.mkdir()
    make_video(app.videos_dir / "clip.mp4", seconds=1)
    discover()
    video = next(iter(p1.catalog(app.spec).values()))
    ctx = p1.make_context([video], app.spec, app.fields)
    for name, values, kind in (("emb.dinov2_s", np.eye(len(ctx.units), dtype=np.float32), "vector"),
                              ("hash.phash", np.arange(len(ctx.units), dtype=np.uint64), "scalar")):
        app.fields.put(FieldTable(REGISTRY.feature_for(name)().key, [u.id for u in ctx.units], values, kind),
                       (video.id, ctx._execution.spec_hash))
    root = app.datasets_dir / "tập ảnh"
    image(root, "a.png")
    out = import_new(app)
    client = TestClient(create_app(app))
    body = {"pool": {"video_ids": [video.id]}, "goal": "g", "target_k": 2,
            "reference": {"dataset_version_ids": [out.version.id]}}
    assert client.post("/api/v1/sessions", json=body).status_code == 409
    svc.prepare_dataset(app, out.version.id)
    reference = client.post("/api/v1/sessions", json=body)
    assert reference.status_code == 200
    sid = reference.json()["id"]
    assert reference.json()["reference"] == {**body["reference"], "model_id": None}
    image(root, "b.png", 20)
    two = run(app, "import_dataset", folder="tập ảnh", dataset_id=out.dataset.id)
    svc.prepare_dataset(app, two.version.id)
    bad = {**body, "reference": {"dataset_version_ids": [out.version.id, two.version.id]}}
    assert client.post("/api/v1/sessions", json=bad).json()["error"]["code"] == "duplicate_dataset_in_reference"
    bad["reference"]["dataset_version_ids"] = ["dv-missing"]
    assert client.post("/api/v1/sessions", json=bad).status_code == 404
    run(app, "update_state", session_id=sid, changes=[{"unit_id": ctx.units[0].id, "action": "keep"}])
    snap = run(app, "create_snapshot", session_id=sid)
    assert run(app, "get_snapshot", snapshot_id=snap.id).context["session"]["reference"] == body["reference"]
    assert run(app, "get_session", session_id=sid).latest_snapshot["is_current"]
    run(app, "update_state", session_id=sid, changes=[{"unit_id": ctx.units[1].id, "action": "keep"}])
    assert not run(app, "get_session", session_id=sid).latest_snapshot["is_current"]
    old = {**app.db.get_session(sid), "id": "old"}
    old["spec"].pop("reference")
    app.db.add_session(old)
    assert run(app, "get_session", session_id="old").reference.dataset_version_ids == []
    assert "reference" not in app.db.get_session("old")["spec"]
