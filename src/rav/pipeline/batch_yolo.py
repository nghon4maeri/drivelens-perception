"""Predict YOLO detection on explicit ordered proposal anchors only (CONTRACTS §17).

Input: managed .pt path, units, Context, fixed params. Output: unit_id/detections; no raw or predictions saved here.
Uses PIL RGB + pixels_exact for Unicode paths and correct frame index; never passes a video/folder to YOLO.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image

from ..timing import timing

ENGINE_VERSION = "8.3.228"
PARAMS = {"imgsz": 640, "conf": 0.25, "iou": 0.7, "max_det": 300, "device": "cpu"}
_MODEL_LOCK = threading.RLock()


@lru_cache(maxsize=1)
def _resident_model(path: str, sha: str, engine: str, output_dir: str, loader):
    return loader(Path(path), Path(output_dir))


def _model(path: Path, output_dir: Path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    sha = digest.hexdigest()
    if len(path.stem) == 64 and all(c in "0123456789abcdef" for c in path.stem) and path.stem != sha:
        raise YoloError("Checkpoint cache không khớp SHA-256")
    return _resident_model(str(path.resolve()), sha, ENGINE_VERSION, str(output_dir.resolve()), load_model)


def verify_checkpoint(path: Path, output_dir: Path) -> dict:
    """Nạp checkpoint + detect một ảnh xám: chứng minh task detect và đúng engine pin (import model SYSTEM §21.17, §21.33)."""
    model = load_model(path, output_dir)
    detect(model, Image.new("RGB", (640, 384), (127, 127, 127)), "verify")
    return {"engine_version": ENGINE_VERSION, "params": PARAMS, "task": model.task,
            "class_map": {str(k): v for k, v in model.names.items()}}


class YoloError(ValueError):
    """Safe error from this pipeline; library errors are handled by the service."""


def load_model(path: Path, output_dir: Path):
    # local-only: settings stay in the one API's output; LOCAL_SINGLE_USER_LIMITS M2.
    config_dir = (output_dir / "ultralytics").resolve()
    (config_dir / "Ultralytics").mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(config_dir)
    os.environ["YOLO_AUTOINSTALL"] = "false"
    import ultralytics

    if ultralytics.__version__ != ENGINE_VERSION:
        raise YoloError("Phiên bản Ultralytics không khớp requirements; cài lại dependencies.")
    if os.environ.get("RAV_MODEL_THREADS"):
        # select_device('cpu') resets Torch to this pinned library constant on first predict.
        from ultralytics.utils import torch_utils
        torch_utils.NUM_THREADS = int(os.environ["RAV_MODEL_THREADS"])
        if torch_utils.NUM_THREADS <= 0:
            raise YoloError("RAV_MODEL_THREADS phải dương")
    ultralytics.settings.update({"sync": False, "runs_dir": str((output_dir / "ultralytics-runs").resolve())})
    model = ultralytics.YOLO(str(path))
    if model.task != "detect":
        raise YoloError("Chỉ hỗ trợ YOLO detection; checkpoint này thuộc task khác.")
    return model


def predict_batch(path: Path, model_id: str, units, ctx, output_dir: Path) -> list[dict]:
    # A model owner serializes local callers too: Ultralytics predictor is mutable.
    with _MODEL_LOCK:
        return _predict_batch(path, model_id, units, ctx, output_dir)


def _predict_batch(path: Path, model_id: str, units, ctx, output_dir: Path) -> list[dict]:
    with timing('worker.yolo.model_load'):
        model = _model(path, output_dir)
    items = []
    for unit in units:
        if unit.anchor is None:
            raise YoloError("Mẫu không có ảnh anchor để chạy YOLO.")
        with timing('worker.yolo.exact_decode'):
            image = Image.fromarray(ctx.pixels_exact(unit.anchor))
        with timing('worker.yolo.inference'):
            detections = detect(model, image, model_id)
        items.append({"unit_id": unit.id, "detections": detections})
    return items


def detect(model, image, model_id):
    """Validate predictions from one PIL image into normalized detection evidence."""
    results = model.predict(source=image, **PARAMS, verbose=False, save=False, save_txt=False)
    if len(results) != 1 or results[0].boxes is None:
        raise YoloError("Kết quả YOLO không đúng một ảnh detection.")
    result, detections = results[0], []
    boxes, scores, classes = (result.boxes.xyxyn.cpu().tolist(), result.boxes.conf.cpu().tolist(),
                              result.boxes.cls.cpu().tolist())
    if not len(boxes) == len(scores) == len(classes) or len(boxes) > PARAMS["max_det"]:
        raise YoloError("Số box/score/class của YOLO không khớp.")
    for box, score, cls in zip(boxes, scores, classes):
        if (len(box) != 4 or not all(math.isfinite(x) for x in [*box, score, cls])
                or not 0 <= score <= 1 or cls != int(cls) or not all(-1e-6 <= x <= 1 + 1e-6 for x in box)):
            raise YoloError("YOLO trả box/score/class không hợp lệ.")
        box = [min(1., max(0., x)) for x in box]
        if box[0] >= box[2] or box[1] >= box[3]:
            raise YoloError("YOLO trả box không có diện tích.")
        detections.append({"label": str(result.names[int(cls)]), "score": score, "box": box,
                           "source": "yolo:" + model_id})
    return detections
