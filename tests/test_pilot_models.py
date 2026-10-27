"""Admin upload model SYSTEM (CONTRACTS §21.33): PostgreSQL thật + S3 moto + worker MODEL_VERIFY.

Kiểm thử nạp checkpoint bằng `verify_checkpoint` giả (tất định); test cuối dùng `data/models/yolo11n.pt` thật nếu có.
Chạy: đặt `RAV_PILOT_TEST_DATABASE_URL` (tests/conftest.py) rồi `python -m pytest tests/test_pilot_models.py -q`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.rav.errors import ServiceError
from src.rav.store.pilot_analysis import PilotAnalysis
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_models import (
    ModelCompleteIn,
    ModelUploadIn,
    PilotModels,
    model_row,
    publish_in_tx,
    staging_key,
)
from src.rav.store.pilot_runtime import PilotRuntime, WorkerContext
from src.rav.worker.analysis_handlers import make_analysis_handlers
from src.rav.worker.dispatcher import PilotDispatcher
from tests.conftest import POLICY_CONFIG
from tests.test_pilot_media import actor

YOLO = Path(__file__).resolve().parents[1] / "data/models/yolo11n.pt"
INFO = {"engine_version": "8.3.228", "params": {"imgsz": 640}, "task": "detect", "class_map": {"0": "car"}}


def test_upload_input_and_publish_reject_tampered_result():
    with pytest.raises(ValidationError):
        ModelUploadIn(filename="m.onnx", declared_bytes=10)
    with pytest.raises(ValidationError):
        ModelUploadIn(filename="m.pt", declared_bytes=(1 << 30) + 1)
    sha = "a" * 64
    payload = {"prefix": "p", "size_bytes": 5, "name": "n", "filename": "m.pt", "train_dataset": "COCO", "uploaded_by": "u"}
    row = model_row("p", sha, 5, "n", "m.pt", "COCO", INFO, uploaded_by="u")
    assert row["raw_key"] == f"p/system/models/yolo/{sha}.pt" and row["core_model_id"] == f"ym-{sha}"
    for bad in ({**row, "name": "khác"}, {**row, "raw_key": "p/system/models/yolo/x.pt"}, {**row, "task": "segment"},
                {**row, "size_bytes": 6}, {**row, "validation": "CONTAINER_ONLY"}):
        with pytest.raises(ServiceError) as e:   # kiểm trước khi chạm DB
            publish_in_tx(None, {"payload": payload}, None, {"model": bad})
        assert e.value.code == "result_invalid"


@pytest.fixture
def md(pg, s3_storage, tmp_path, monkeypatch):
    policy = pg.policy(dict(POLICY_CONFIG, cpu_base_ms=600_000))   # nạp checkpoint thật tốn ~15 s CPU
    admin, user = pg.profile(policy_id=policy), pg.profile(policy_id=policy)
    monkeypatch.setenv("RAV_PILOT_OPERATOR_IDS", str(admin))
    pg.slot("cpu-1", "CPU")
    wdb = PilotDB(pg.worker_url)
    d = PilotDispatcher(PilotRuntime(wdb), WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), "CPU", heartbeat_s=0.2)
    for kind, fn in make_analysis_handlers(s3_storage, wdb, tmp_path / "work").items():
        d.register_handler(kind, fn)
    api = PilotDB(pg.api_url)
    return SimpleNamespace(pg=pg, s3=s3_storage, admin=admin, user=user, d=d, models=PilotModels(api, lambda: s3_storage),
                           ana=PilotAnalysis(api))


def _upload(md, data: bytes, name="YOLO test", dataset="COCO"):
    up = md.models.create_upload(actor(md.admin), ModelUploadIn(filename="best.pt", declared_bytes=len(data)))
    assert up["transfer"]["method"] == "POST"
    key = staging_key(md.s3.prefix, up["upload_id"])
    md.s3.client.put_object(Bucket=md.s3.bucket, Key=key, Body=data)   # thay cho browser POST lên presigned URL
    job = md.models.complete(actor(md.admin), up["upload_id"], ModelCompleteIn(filename="best.pt", name=name, train_dataset=dataset))
    assert job.kind == "MODEL_VERIFY"
    assert md.models.complete(actor(md.admin), up["upload_id"],
                              ModelCompleteIn(filename="best.pt", name=name, train_dataset=dataset)).id == job.id
    assert md.d.run_once()
    return key, md.pg.sql("SELECT state, error FROM rav_pilot_job WHERE id = %s", (job.id,))[0]


def test_admin_upload_verifies_and_publishes_system_model(md, monkeypatch):
    monkeypatch.setattr("src.rav.pipeline.batch_yolo.verify_checkpoint", lambda path, out: INFO)
    with pytest.raises(ServiceError) as e:
        md.models.create_upload(actor(md.user), ModelUploadIn(filename="best.pt", declared_bytes=10))
    assert e.value.status == 403
    with pytest.raises(ServiceError) as e:
        md.models.complete(actor(md.admin), uuid4(), ModelCompleteIn(filename="best.pt", name="x", train_dataset="COCO"))
    assert e.value.code == "upload_invalid"

    data = b"fake checkpoint bytes"
    sha = hashlib.sha256(data).hexdigest()
    key, job = _upload(md, data)
    assert job["state"] == "SUCCEEDED", job["error"]
    models = md.ana.list_models(actor(md.user))   # người dùng thường thấy model SYSTEM vừa thêm
    assert len(models) == 1
    m = models[0]
    assert str(m.id) == model_row("", sha, 0, "", "", "", INFO)["id"]
    assert m.sha256 == sha and m.validation == "INFERENCE_VERIFIED" and m.provenance["train_dataset"] == "COCO"
    assert m.provenance["uploaded_by"] == str(md.admin)
    assert md.s3.head(key) is None and md.s3.head(f"{md.s3.prefix}/system/models/yolo/{sha}.pt")["ContentLength"] == len(data)

    _, again = _upload(md, data, name="tên khác")   # cùng bytes → giữ bản cũ, không đổi metadata
    assert again["state"] == "SUCCEEDED"
    assert [x.name for x in md.ana.list_models(actor(md.user))] == ["YOLO test"]


def test_unloadable_checkpoint_fails_without_model(md, monkeypatch):
    def broken(path, out):
        raise RuntimeError("không unpickle được")
    monkeypatch.setattr("src.rav.pipeline.batch_yolo.verify_checkpoint", broken)
    key, job = _upload(md, b"not a checkpoint")
    assert job["state"] == "FAILED" and job["error"]["code"] == "unsupported_yolo_model"
    assert md.ana.list_models(actor(md.admin)) == [] and md.s3.head(key) is None


@pytest.mark.skipif(not YOLO.exists(), reason="cần data/models/yolo11n.pt để nạp checkpoint thật")
def test_admin_upload_real_checkpoint(md):
    _, job = _upload(md, YOLO.read_bytes(), name="YOLO11n COCO")
    assert job["state"] == "SUCCEEDED", job["error"]
    m = md.ana.list_models(actor(md.user))[0]
    assert m.task == "detect" and len(m.class_map) == 80
