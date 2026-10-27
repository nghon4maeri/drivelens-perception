"""M2: exact proposal subset, real RGB/anchor path, cache isolation, failure/integrity and concurrency.

Unit tests mock only the YOLO engine; they still decode explicit video anchors. Real nano smoke is run separately.
Run python -m pytest tests/test_batch_yolo.py -q.
"""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.rav.api import create_app
from src.rav.core.context import Context
from src.rav.pipeline import batch_yolo as pipeline
from src.rav.service import batch_yolo as svc
from src.rav.service import reference_model

from .test_p5a import app as video_app  # noqa: F401
from .test_p5a import new_session, run
from .test_reference_model import checkpoint

LOAD_MODEL = pipeline.load_model


class Tensor:
    def __init__(self, value):
        self.value = value

    def cpu(self):
        return self

    def tolist(self):
        return self.value


def detection(boxes=None):
    boxes = [[.1, .2, .5, .8]] if boxes is None else boxes
    return SimpleNamespace(names={0: "car"}, boxes=SimpleNamespace(
        xyxyn=Tensor(boxes), conf=Tensor([.75] * len(boxes)), cls=Tensor([0.] * len(boxes))))


@pytest.fixture
def batch(video_app, tmp_path, monkeypatch):  # noqa: F811
    app = video_app
    app.models_dir = tmp_path / "models"
    file = tmp_path / "input.pt"
    file.write_bytes(checkpoint())
    model = reference_model.upload_model(app, "best.pt", str(file))["model"]
    session = new_session(app)
    proposal = run(app, "propose", session_id=session.id, recipe="random", batch_size=4, seed=11)
    predictions, decoded, loaded = [], [], []
    read = Context.pixels_exact
    def exact(self, frame):
        decoded.append((frame.video_id, frame.idx))
        return read(self, frame)
    monkeypatch.setattr(Context, "pixels_exact", exact)
    class Engine:
        def predict(self, source, **params):
            assert source.mode == "RGB" and source.size == (64, 48)
            assert params["device"] == "cpu" and not params["save"]
            predictions.append(np.asarray(source))
            return [detection([] if len(predictions) == 2 else None)]
    def load(path, output_dir):
        loaded.append(path)
        return Engine()
    monkeypatch.setattr(pipeline, "load_model", load)
    return SimpleNamespace(app=app, model=model, session=session, proposal=proposal,
                           predictions=predictions, decoded=decoded, loaded=loaded, file=file)


def endpoint(batch, n=None):
    return f"/api/v1/sessions/{batch.session.id}/proposals/{n or batch.proposal.n}/yolo"


def test_only_proposal_picks_decoded_and_no_review_scoring_side_effects(batch):
    app, sid = batch.app, batch.session.id
    client = TestClient(create_app(app))
    before = (app.db.get_session(sid), app.db.proposals(sid), app.db.decisions(sid))
    assert client.get(endpoint(batch), params={"model_id": batch.model["id"]}).json() is None
    assert not batch.loaded and not batch.decoded
    response = client.post(endpoint(batch), json={"model_id": batch.model["id"]})
    assert response.status_code == 200
    result = response.json()
    ids = [p.unit_id for p in batch.proposal.picks]
    assert result["total_images"] == 4 and [x["unit_id"] for x in result["items"]] == ids
    units = {u.id: u for u in context_units(app, sid)}
    assert batch.decoded == [(units[i].anchor.video_id, units[i].anchor.idx) for i in ids]
    assert len(batch.loaded) == 1 and len(batch.predictions) == 4
    assert result["items"][1]["detections"] == []
    assert result["items"][0]["detections"] == [{"label": "car", "score": .75, "box": [.1, .2, .5, .8], "source": "yolo:" + batch.model["id"]}]
    assert before == (app.db.get_session(sid), app.db.proposals(sid), app.db.decisions(sid))
    assert not list(app.output_dir.joinpath("yolo_batches").glob("*.tmp"))
    # After restart, GET/POST reuse durable cache, never load the engine or decode more images.
    app2 = type(app)(app.videos_dir, app.output_dir, app.max_upload_bytes, models_dir=app.models_dir)
    cached = svc.run(app2, sid, batch.proposal.n, batch.model["id"])
    assert cached == {**result, "cached": True} and len(batch.predictions) == 4


def context_units(app, sid):
    from src.rav.service.session import get_session, make_context
    return make_context(app, get_session(app, sid)).units


def test_separate_models_and_proposals_never_reuse_wrong_boxes(batch):
    run(batch.app, "run_batch_yolo", session_id=batch.session.id, n=batch.proposal.n, model_id=batch.model["id"])
    next_batch = run(batch.app, "propose", session_id=batch.session.id, recipe="random", batch_size=2, seed=5)
    assert svc.get_result(batch.app, batch.session.id, next_batch.n, batch.model["id"]) is None
    batch.file.write_bytes(checkpoint(b"another checkpoint"))
    model2 = reference_model.upload_model(batch.app, "new.pt", str(batch.file))["model"]
    assert svc.get_result(batch.app, batch.session.id, batch.proposal.n, model2["id"]) is None
    new = svc.run(batch.app, batch.session.id, next_batch.n, model2["id"])
    assert new["total_images"] == 2 and all(d["source"] == "yolo:" + model2["id"] for row in new["items"] for d in row["detections"])
    assert len(batch.predictions) == 6


def test_validation_and_busy_do_not_infer(batch):
    client = TestClient(create_app(batch.app))
    body = {"model_id": batch.model["id"]}
    assert client.post(endpoint(batch, 999), json=body).status_code == 404
    assert client.post(endpoint(batch), json={"model_id": "missing"}).status_code == 404
    assert client.post(endpoint(batch), json={**body, "unit_ids": ["anything"]}).status_code == 422
    batch.app.batch_yolo_lock.acquire()
    try:
        response = client.post(endpoint(batch), json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "yolo_busy"
    finally:
        batch.app.batch_yolo_lock.release()
    assert not batch.loaded


def test_empty_batch_rejected_without_model_loading(batch):
    run(batch.app, "update_state", session_id=batch.session.id,
        changes=[{"unit_id": u.id, "action": "drop"} for u in context_units(batch.app, batch.session.id)])
    empty = run(batch.app, "propose", session_id=batch.session.id, recipe="random", batch_size=16)
    with pytest.raises(Exception, match="1–256"):
        svc.run(batch.app, batch.session.id, empty.n, batch.model["id"])
    assert not batch.loaded


@pytest.mark.parametrize("change", ["model", "source", "exception"])
def test_mid_run_change_or_exception_never_publishes_partial(batch, monkeypatch, change):
    def fail(path, model_id, units, ctx, output_dir):
        if change == "exception":
            raise RuntimeError("secret path must not leak")
        if change == "model":
            path.write_bytes(b"X" + path.read_bytes()[1:])
        else:
            video = batch.app.videos_dir / "a.mp4"
            video.write_bytes(b"X" + video.read_bytes()[1:])
        return [{"unit_id": u.id, "detections": []} for u in units]
    monkeypatch.setattr(pipeline, "predict_batch", fail)
    client = TestClient(create_app(batch.app))
    response = client.post(endpoint(batch), json={"model_id": batch.model["id"]})
    assert response.status_code in (409, 422) and "secret" not in response.text
    assert not list(batch.app.output_dir.joinpath("yolo_batches").glob("*"))
    assert not batch.app.batch_yolo_lock.locked()


def test_cache_corruption_and_changed_model_rejected(batch):
    svc.run(batch.app, batch.session.id, batch.proposal.n, batch.model["id"])
    path = next(batch.app.output_dir.joinpath("yolo_batches").glob("*.json"))
    value = json.loads(path.read_text())
    value["result"]["items"].pop()
    path.write_text(json.dumps(value))
    client = TestClient(create_app(batch.app))
    assert client.get(endpoint(batch), params={"model_id": batch.model["id"]}).json()["error"]["code"] == "yolo_cache_invalid"
    model_path = batch.app.models_dir / (batch.model["id"] + ".pt")
    model_path.unlink()
    assert client.get(endpoint(batch), params={"model_id": batch.model["id"]}).json()["error"]["code"] == "model_file_missing"


def test_invalid_boxes_are_rejected_not_cached(batch, monkeypatch):
    class Invalid:
        def predict(self, **kw):
            return [detection([[.8, .2, .1, .7]])]
    monkeypatch.setattr(pipeline, "load_model", lambda *a: Invalid())
    with pytest.raises(Exception, match="diện tích"):
        svc.run(batch.app, batch.session.id, batch.proposal.n, batch.model["id"])
    assert not list(batch.app.output_dir.joinpath("yolo_batches").glob("*"))


@pytest.mark.parametrize("task,version", [("segment", pipeline.ENGINE_VERSION), ("detect", "wrong")])
def test_loader_rejects_wrong_task_or_version_before_images(batch, monkeypatch, task, version):
    # This minimal fake tests task/version validation, not Ultralytics' runtime thread configuration.
    monkeypatch.delenv("RAV_MODEL_THREADS", raising=False)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(
        __version__=version, settings=SimpleNamespace(update=lambda x: None),
        YOLO=lambda path: SimpleNamespace(task=task)))
    with pytest.raises(pipeline.YoloError):
        LOAD_MODEL(batch.file, batch.app.output_dir)
    assert not batch.decoded
