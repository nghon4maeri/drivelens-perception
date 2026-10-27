"""Upload opaque .pt checkpoints, bounded container checks/SHA and immutable catalog (CONTRACTS §16).

Input: upload temp path, display filename/name. Output: model metadata; never unpickles or runs uploaded code.
Verify full bytes before selecting a model; training dataset association is declared in the session reference.
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import App, ServiceError


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path(app: App, model: dict) -> Path:
    return app.models_dir / f"{model['id']}.pt"


def dto(app: App, model: dict) -> dict:
    path = _path(app, model)
    try:
        available = path.is_file() and path.stat().st_size == model["size_bytes"]
    except FileNotFoundError:
        available = False
    return {**model, "available": available,
            "validation": "container_only", "inference_ready": False}


def get_model(app: App, model_id: str) -> dict:
    model = app.db.get_model(model_id)
    if model is None:
        raise ServiceError("model_not_found", "Không có checkpoint tham chiếu này.", 404)
    return model


def verify_model(app: App, model_id: str) -> dict:
    model = get_model(app, model_id)
    path = _path(app, model)
    try:
        if path.stat().st_size != model["size_bytes"] or _sha(path) != model["sha256"]:
            raise ServiceError("model_file_changed", "File checkpoint đã đổi; không dùng như model cũ.", 409)
    except FileNotFoundError:
        raise ServiceError("model_file_missing", "File checkpoint không còn trong kho model.", 409) from None
    return model


def upload_model(app: App, filename: str, path: str, name: str | None = None) -> dict:
    filename = Path(filename.replace("\\", "/")).name
    source = Path(path)
    if Path(filename).suffix.lower() != ".pt":
        raise ServiceError("unsupported_model_format", "Chỉ nhận checkpoint .pt định dạng PyTorch ZIP.")
    size = source.stat().st_size
    if size > app.max_model_bytes:
        raise ServiceError("model_too_large", "Checkpoint vượt giới hạn RAV_MAX_MODEL_MB.", 413)
    if not size:
        raise ServiceError("invalid_model_file", "Checkpoint rỗng.")
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            names = {item.filename for item in entries}
            if not any(n.endswith("/data.pkl") and n.removesuffix("data.pkl") + "version" in names for n in names):
                raise ServiceError("invalid_model_file", "File không có cấu trúc checkpoint PyTorch ZIP (data.pkl/version).")
            if sum(item.file_size for item in entries) > app.max_model_bytes:
                raise ServiceError("model_too_large", "Kích thước giải nén checkpoint vượt giới hạn.", 413)
    except (zipfile.BadZipFile, OSError):
        raise ServiceError("invalid_model_file", "Checkpoint .pt không phải PyTorch ZIP hợp lệ; chưa hỗ trợ legacy pickle.") from None
    sha = _sha(source)
    row = {"id": "ym-" + sha, "sha256": sha, "size_bytes": size, "filename": filename,
           "name": name or Path(filename).stem[:200], "format": "pytorch_zip", "created_at": datetime.now(UTC).isoformat()}
    # local-only: one App/API per model store; LOCAL_SINGLE_USER_LIMITS M1.
    with app.model_upload_lock:
        existing = app.db.get_model(row["id"])
        if existing:
            verify_model(app, existing["id"])
            if app.cloud_storage:
                app.cloud_storage.upload_raw(_path(app, existing), 'models', existing['sha256'])
            return {"model": dto(app, existing), "created": False}
        app.models_dir.mkdir(parents=True, exist_ok=True)
        target = _path(app, row)
        temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        created_file = False
        try:
            if target.exists():
                if target.stat().st_size != size or _sha(target) != sha:
                    raise ServiceError("model_file_changed", "File trong kho model không khớp checksum.", 409)
            else:
                with source.open("rb") as src, temp.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                if temp.stat().st_size != size or _sha(temp) != sha:
                    raise ServiceError("model_file_changed", "Checkpoint thay đổi trong lúc upload.", 409)
                temp.replace(target)
                created_file = True
            if app.cloud_storage:
                ref = app.cloud_storage.upload_raw(target, 'models', sha)
                app.cloud_storage.put_json(f'models/{sha}.json', {**row, 'object':ref})
            app.db.add_model(row)
        except BaseException:
            if created_file:
                target.unlink(missing_ok=True)
            raise
        finally:
            temp.unlink(missing_ok=True)
    return {"model": dto(app, row), "created": True}
