"""M1: multipart opaque checkpoint, dedup/integrity/rollback and frozen model–training references; no YOLO inference.

Synthetic PyTorch ZIPs deliberately contain invalid pickle; upload must never deserialize them.
Run python -m pytest tests/test_reference_model.py -q.
"""
import hashlib
import io
import os
import sqlite3
import zipfile
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from src.rav.api import create_app
from src.rav.service import App
from src.rav.service import dataset as dataset_svc
from src.rav.service import reference_model as svc
from src.rav.store.db import DB, SCHEMA_V1, SQL_V2, SQL_V3

from .test_dataset_prepare_reference import fake_embedding  # noqa: F401
from .test_datasets import image, import_new
from .test_p5a import app as video_app  # noqa: F401
from .test_p5a import run, video_id


def checkpoint(payload=b"invalid pickle, must not execute", complete=True):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("archive/data.pkl"), payload, compress_type=zipfile.ZIP_DEFLATED)
        if complete:
            archive.writestr(zipfile.ZipInfo("archive/version"), b"3\n", compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


@pytest.fixture
def app(tmp_path):
    return App(tmp_path / "videos", tmp_path / "output", 1 << 20,
               models_dir=tmp_path / "mô hình", datasets_dir=tmp_path / "datasets")


def upload(client, data=None, filename="best.pt", name=None):
    return client.post("/api/v1/models", files={"file": (filename, checkpoint() if data is None else data)},
                       data={} if name is None else {"name": name})


def test_multipart_dedup_metadata_immutable_and_unicode_storage(app):
    client = TestClient(create_app(app))
    assert client.get("/api/v1/models").json() == []
    body = checkpoint()
    first = upload(client, body, "C:\\fakepath\\mô hình.pt", "YOLO đã train").json()
    model = first["model"]
    assert first["created"] and model["name"] == "YOLO đã train" and model["filename"] == "mô hình.pt"
    assert model["id"] == "ym-" + hashlib.sha256(body).hexdigest()
    assert model["validation"] == "container_only" and not model["inference_ready"] and model["available"]
    assert (app.models_dir / (model["id"] + ".pt")).read_bytes() == body
    repeated = upload(client, body, "../../renamed.pt", "khác").json()
    assert not repeated["created"] and repeated["model"] == model
    assert client.get("/api/v1/models").json() == [model]
    assert client.get("/api/v1/models/" + model["id"]).json() == model
    assert client.post("/api/v1/models/" + model["id"] + "/verify").json() == {"ok": True}
    assert len(list(app.models_dir.iterdir())) == 1 and not list((app.output_dir / "tmp").iterdir())
    for sql in ("UPDATE model_checkpoint SET name='changed'", "DELETE FROM model_checkpoint"):
        with closing(sqlite3.connect(app.db.path)) as c, pytest.raises(sqlite3.DatabaseError, match="immutable"):
            c.execute(sql)


@pytest.mark.parametrize("data,filename,code", [
    (b"", "empty.pt", "invalid_model_file"),
    (b"pickle or garbage", "legacy.pt", "invalid_model_file"),
    (checkpoint(complete=False), "missing.pt", "invalid_model_file"),
    (checkpoint(), "model.onnx", "unsupported_model_format"),
])
def test_invalid_upload_never_registers_or_leaves_files(app, data, filename, code):
    client = TestClient(create_app(app))
    response = upload(client, data, filename)
    assert response.status_code == 422 and response.json()["error"]["code"] == code
    assert app.db.list_models() == [] and not app.models_dir.exists()
    assert not list((app.output_dir / "tmp").iterdir())


def test_limits_raw_and_uncompressed_and_form_validation_cleanup(app):
    app.max_model_bytes = 1024
    client = TestClient(create_app(app))
    for data in (b"x" * 1025, checkpoint(b"x" * 2048)):
        response = upload(client, data)
        assert response.status_code == 413 and response.json()["error"]["code"] == "model_too_large"
    assert upload(client, name=" " * 5).status_code == 422
    assert client.post("/api/v1/models").status_code == 422
    assert not list((app.output_dir / "tmp").iterdir()) and app.db.list_models() == []


def test_full_checksum_catches_same_size_mtime_change_and_missing(app):
    client = TestClient(create_app(app))
    model = upload(client).json()["model"]
    path = app.models_dir / (model["id"] + ".pt")
    old_stat, raw = path.stat(), path.read_bytes()
    path.write_bytes(b"X" + raw[1:])
    os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    # list does only a cheap existence/size check; verify and session selection check every byte.
    assert client.get("/api/v1/models").json()[0]["available"]
    url = "/api/v1/models/" + model["id"]
    assert client.post(url + "/verify").json()["error"]["code"] == "model_file_changed"
    assert upload(client).json()["error"]["code"] == "model_file_changed"
    path.unlink()
    assert not client.get(url).json()["available"]
    assert client.post(url + "/verify").json()["error"]["code"] == "model_file_missing"
    assert client.get("/api/v1/models/missing").status_code == 404
    assert app.db.get_model(model["id"]) is not None


def test_failed_db_write_rolls_back_new_file_and_reuses_matching_orphan(app, tmp_path, monkeypatch):
    source = tmp_path / "upload.pt"
    source.write_bytes(checkpoint())
    db = app.db
    original = db.add_model
    def fail(row):
        raise sqlite3.OperationalError("test transaction failure")
    monkeypatch.setattr(db, "add_model", fail)
    with pytest.raises(sqlite3.OperationalError):
        svc.upload_model(app, "best.pt", str(source))
    assert not list(app.models_dir.iterdir()) and db.list_models() == []
    model_id = "ym-" + hashlib.sha256(source.read_bytes()).hexdigest()
    orphan = app.models_dir / (model_id + ".pt")
    orphan.write_bytes(source.read_bytes())
    with pytest.raises(sqlite3.OperationalError):
        svc.upload_model(app, "best.pt", str(source))
    assert orphan.read_bytes() == source.read_bytes()
    monkeypatch.setattr(db, "add_model", original)
    assert svc.upload_model(app, "best.pt", str(source))["created"]
    assert len(list(app.models_dir.iterdir())) == 1


def test_model_selection_requires_training_reference_and_freezes_snapshot(video_app, tmp_path, fake_embedding):  # noqa: F811
    app = video_app
    app.models_dir, app.datasets_dir = tmp_path / "models", tmp_path / "datasets"
    client = TestClient(create_app(app))
    model = upload(client).json()["model"]
    body = {"pool": {"video_ids": [video_id(app, "a.mp4")]}, "goal": "bổ sung train", "target_k": 5,
            "reference": {"model_id": model["id"], "dataset_version_ids": []}}
    assert client.post("/api/v1/sessions", json=body).json()["error"]["code"] == "model_training_reference_required"
    image(app.datasets_dir / "tập ảnh", "a.png")
    one = import_new(app)
    body["reference"]["dataset_version_ids"] = [one.version.id]
    assert client.post("/api/v1/sessions", json=body).status_code == 409
    dataset_svc.prepare_dataset(app, one.version.id)
    response = client.post("/api/v1/sessions", json=body)
    assert response.status_code == 200
    session = response.json()
    assert session["reference"] == body["reference"] and session["reference_model"] == model
    proposal = run(app, "propose", session_id=session["id"], recipe="diversity", batch_size=2)
    run(app, "update_state", session_id=session["id"], changes=[{"unit_id": proposal.picks[0].unit_id, "action": "keep"}])
    snapshot = run(app, "create_snapshot", session_id=session["id"])
    before = run(app, "get_snapshot", snapshot_id=snapshot.id)
    assert before.context["session"]["reference"] == body["reference"]
    image(app.datasets_dir / "tập ảnh", "b.png", 20)
    run(app, "import_dataset", folder="tập ảnh", dataset_id=one.dataset.id)
    upload(client, checkpoint(b"new bytes"), "new.pt")
    assert client.get("/api/v1/sessions/" + session["id"]).json()["reference"] == body["reference"]
    path = app.models_dir / (model["id"] + ".pt")
    path.write_bytes(b"X" + path.read_bytes()[1:])
    assert client.post("/api/v1/sessions", json=body).json()["error"]["code"] == "model_file_changed"
    assert run(app, "get_snapshot", snapshot_id=snapshot.id).context == before.context
    old = run(app, "create_session", pool=body["pool"], goal="g", target_k=5)
    assert old.reference_model is None and old.reference.model_id is None
    assert app.db.get_session(old.id)["spec"]["reference"] == {"dataset_version_ids": []}


def test_v3_migration_preserves_dataset_and_env_configuration(tmp_path, monkeypatch):
    path = tmp_path / "rav.sqlite"
    with closing(sqlite3.connect(path)) as c:
        c.executescript(SCHEMA_V1 + SQL_V2 + SQL_V3)
        c.execute("PRAGMA user_version=3")
        c.execute("INSERT INTO dataset (id, name, description, created_at) VALUES ('d', 'train', NULL, 't')")
        c.commit()
    db = DB(path)
    assert db.backup is not None
    with closing(sqlite3.connect(db.backup)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    with closing(sqlite3.connect(path)) as c:
        assert c.execute("SELECT id, name, description, created_at FROM dataset").fetchall() == [("d", "train", None, "t")]
        assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    assert db.list_models() == []
    monkeypatch.setenv("RAV_MODELS_DIR", "data/custom-models")
    monkeypatch.setenv("RAV_MAX_MODEL_MB", "7")
    configured = App.from_env()
    assert configured.models_dir.as_posix() == "data/custom-models" and configured.max_model_bytes == 7 << 20
