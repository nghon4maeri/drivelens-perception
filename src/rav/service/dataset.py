"""Import/query dataset qua pipeline và store (CONTRACTS §14.4, quyết định #65).

Input: App + thư mục/version; output: DTO dict hoặc JPEG bytes. Chỉ lưu manifest tới raw;
CLI import-dataset và REST dùng cùng command. Chuẩn bị feature nằm ở slice S5.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from PIL import Image, ImageOps

from ..pipeline import datasets as pipeline
from . import App, ServiceError


def import_dataset(app: App, folder: str, name: str | None, dataset_id: str | None,
                   annotation_format: str, description: str | None) -> dict:
    dataset = None
    if dataset_id is not None:
        if app.db.dataset(dataset_id) is None:
            raise ServiceError("dataset_not_found", f"không có dataset {dataset_id!r}", 404)
    else:
        if any(d["name"] == name for d in app.db.datasets()):
            raise ServiceError("dataset_name_taken", f"tên dataset {name!r} đã được dùng", 409)
        dataset_id = "ds-" + uuid4().hex[:12]
        dataset = {"id": dataset_id, "name": name, "description": description,
                   "created_at": datetime.now(UTC).isoformat()}
    try:
        # local-only: người dùng tự đặt nguồn dưới datasets_dir; import/hash đồng bộ (LOCAL_SINGLE_USER_LIMITS #16).
        root = pipeline.resolve_folder(app.datasets_dir, folder)
        result = pipeline.scan(root, annotation_format, app.max_import_images)
    except pipeline.DatasetImportError as e:
        raise ServiceError(e.code, str(e)) from e
    version_id = "dv-" + hashlib.sha256(f"{dataset_id}\n{result['manifest_hash']}".encode()).hexdigest()[:16]
    row = {k: result[k] for k in ("manifest_hash", "manifest", "classes", "annotation_format", "import_report")}
    row.update(id=version_id, dataset_id=dataset_id, created_at=datetime.now(UTC).isoformat(),
               created_by="human:local", folder=root.relative_to(app.datasets_dir.resolve()).as_posix(),
               n_images=len(result["members"]))
    members = [{**m, "sample_id": f"{dataset_id}:{m['key']}"} for m in result["members"]]
    if app.cloud_storage:
        refs = {}
        for member in members:
            for path_key, hash_key in (('relpath','sha256'), ('label_relpath','label_sha256')):
                if member.get(path_key) and member.get(hash_key):
                    refs[member[path_key]] = app.cloud_storage.upload_raw(root / member[path_key], 'datasets', member[hash_key])
        by_key = {member['key']:member for member in members}
        for conflict in result['manifest']['conflicts']:
            for entry in conflict['copies']:
                refs[entry['relpath']] = app.cloud_storage.upload_raw(root / entry['relpath'], 'datasets', by_key[conflict['key']]['sha256'])
                if entry.get('label_relpath') and entry.get('label_sha256'):
                    refs[entry['label_relpath']] = app.cloud_storage.upload_raw(root / entry['label_relpath'], 'datasets', entry['label_sha256'])
        for filename in ('data.yaml','data.yml','classes.txt'):
            if (root / filename).is_file():
                refs[filename] = app.cloud_storage.upload_raw(root / filename, 'datasets')
        app.cloud_storage.put_json(f'datasets/{version_id}.json', {'version':row,'members':members,'objects':refs})
    try:
        version, created = app.db.add_dataset_version(dataset, row, members)
    except sqlite3.IntegrityError as e:
        if "dataset.name" in str(e):
            raise ServiceError("dataset_name_taken", f"tên dataset {name!r} đã được dùng", 409) from e
        raise
    if app.auto_prepare:
        queue_prepare(app, version["id"])
    return {"dataset": dataset_dto(app, app.db.dataset(dataset_id)), "version": version_dto(app, version),
            "created": created, "report": version["import_report"]}


def get_version(app: App, version_id: str) -> dict:
    row = app.db.dataset_version(version_id)
    if row is None:
        raise ServiceError("dataset_version_not_found", f"không có dataset version {version_id!r}", 404)
    return row


def version_dto(app: App, row: dict) -> dict:
    report = row["import_report"]
    fields = ("id", "dataset_id", "n", "created_at", "folder", "annotation_format", "classes", "n_images")
    return {**{k: row[k] for k in fields},
            "label_counts": {k: report["labels"][k] for k in ("none", "labeled", "empty", "missing", "invalid", "conflict")},
            "class_counts": report["class_counts"], "prepared": is_prepared(app, row),
            "prepare": app.dataset_prepare_jobs.get(row["id"]),
            "n_sessions_as_reference": sum(row["id"] in s["spec"].get("reference", {}).get("dataset_version_ids", [])
                                           for s in app.db.list_sessions())}


def dataset_dto(app: App, row: dict) -> dict:
    return {**row, "versions": [version_dto(app, v) for v in app.db.dataset_versions(row["id"])]}


def list_datasets(app: App) -> list[dict]:
    return [dataset_dto(app, row) for row in app.db.datasets()]


def list_samples(app: App, version_id: str, offset: int, limit: int, label_status: str | None) -> dict:
    get_version(app, version_id)
    total, members = app.db.dataset_samples(version_id, offset, limit, label_status)
    return {"total": total, "items": [{**{k: m[k] for k in ("sample_id", "key", "relpath", "width", "height",
                                                           "label_status", "label_counts")},
                                     "thumb_url": f"/api/v1/datasets/versions/{version_id}/images/{m['key']}?max_side=320"}
                                    for m in members]}


def get_image(app: App, version_id: str, key: str, max_side: int | None) -> bytes:
    version = get_version(app, version_id)
    member = app.db.dataset_image(version_id, key)
    if member is None:
        raise ServiceError("dataset_image_not_found", f"không có ảnh {key!r} trong version", 404)
    try:
        root = pipeline.resolve_folder(app.datasets_dir, version["folder"])
        path = pipeline.inside(root, root / member["relpath"])
        data = path.read_bytes()
    except FileNotFoundError as e:
        raise ServiceError("dataset_file_missing", f"không còn ảnh nguồn {member['relpath']}", 404) from e
    except pipeline.DatasetImportError as e:
        if not (app.datasets_dir / version["folder"]).exists():
            raise ServiceError("dataset_file_missing", "thư mục nguồn không còn tồn tại", 404) from e
        raise ServiceError(e.code, str(e)) from e
    if hashlib.sha256(data).hexdigest() != member["sha256"]:
        raise ServiceError("dataset_file_changed", f"ảnh nguồn đã đổi: {member['relpath']}", 409)
    with Image.open(io.BytesIO(data)) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGB")
        if max_side is not None:
            rgb.thumbnail((max_side, max_side))
        output = io.BytesIO()
        rgb.save(output, format="JPEG", quality=90)
    return output.getvalue()


def list_import_folders(app: App) -> list[dict]:
    root = app.datasets_dir
    if not root.is_dir():
        return []
    folders = []
    for first in sorted(root.iterdir()):
        if first.name.startswith(".") or not first.is_dir() or first.is_symlink():
            continue
        folders.append(first)
        folders.extend(p for p in sorted(first.iterdir()) if p.is_dir() and not p.name.startswith(".") and not p.is_symlink())
    out = []
    for folder in folders:
        try:
            folder = pipeline.inside(root, folder)
            detected, classes = pipeline.annotation(folder, "auto")
            n = sum(1 for _ in pipeline.image_files(folder))
        except pipeline.DatasetImportError:
            continue
        out.append({"folder": folder.relative_to(root.resolve()).as_posix(), "n_images_hint": n,
                    "detected": detected, "classes": classes})
    return out


def all_members(app: App, version_id: str) -> list[dict]:
    version = get_version(app, version_id)
    return app.db.dataset_samples(version_id, 0, version["n_images"], None)[1]


def is_prepared(app: App, version: dict) -> bool:
    return pipeline.is_prepared(version, all_members(app, version["id"]), app.fields)


def verify(app: App, version_id: str) -> dict:
    """Kiểm mọi nguồn member/conflict và cả nhãn missing nay xuất hiện; lớp đã đóng băng."""
    version = get_version(app, version_id)
    members = all_members(app, version_id)
    root = app.datasets_dir / version["folder"]
    missing, changed = set(), set()
    files = {}

    def add_image(m, relpath):
        files[relpath] = (m["sha256"], m["size_bytes"])

    def add_label(m):
        relpath = m["label_relpath"]
        if m["label_status"] == "missing" and m["relpath"].startswith("images/"):
            from pathlib import PurePosixPath
            relpath = (PurePosixPath("labels") / PurePosixPath(m["relpath"]).relative_to("images")).with_suffix(".txt").as_posix()
        if relpath:
            files[relpath] = (m["label_sha256"], None)

    by_key = {m["key"]: m for m in members}
    for m in members:
        add_image(m, m["relpath"])
        add_label(m)
    for group in version["manifest"]["conflicts"]:
        for copy in group["copies"]:
            add_image(by_key[group["key"]], copy["relpath"])
            add_label(copy)
    for relpath, (sha, size) in files.items():
        path = root / relpath
        try:
            pipeline.inside(app.datasets_dir, root)
            path = pipeline.inside(root, path)
            if sha is None:
                if path.exists():
                    changed.add(relpath)
                continue
            data = path.read_bytes()
            if size is not None and len(data) != size or hashlib.sha256(data).hexdigest() != sha:
                changed.add(relpath)
        except FileNotFoundError:
            if sha is not None:
                missing.add(relpath)
        except (pipeline.DatasetImportError, OSError):
            changed.add(relpath)
    return {"ok": not missing and not changed, "missing": sorted(missing), "changed": sorted(changed)}


def prepare_dataset(app: App, version_id: str, on_progress=None) -> None:
    version = get_version(app, version_id)
    report = verify(app, version_id)
    if not report["ok"]:
        code = "dataset_file_missing" if report["missing"] else "dataset_file_changed"
        raise ServiceError(code, f"nguồn dataset không còn khả dụng: {report}", 404 if report["missing"] else 409)
    ctx = pipeline.make_context(version, all_members(app, version_id),
                                pipeline.resolve_folder(app.datasets_dir, version["folder"]), app.fields, on_progress=on_progress)
    try:
        for name in pipeline.FIELDS:
            ctx.field(name)
    except FileNotFoundError as e:
        raise ServiceError("dataset_file_missing", "ảnh nguồn bị xoá trong lúc chuẩn bị", 404) from e


def queue_prepare(app: App, version_id: str) -> None:
    version = get_version(app, version_id)
    job = app.dataset_prepare_jobs.get(version_id)
    if job and job["state"] in ("queued", "running"):
        return
    # local-only: dùng cùng worker P1, trạng thái RAM (LOCAL_SINGLE_USER_LIMITS #15).
    app.dataset_prepare_jobs[version_id] = {"state": "queued", "stage": "verify", "done": 0,
                                            "total": version["n_images"], "started_at": None, "error": None}
    app.prepare_queue.put(lambda: _run_prepare(app, version_id))


def _run_prepare(app: App, version_id: str) -> None:
    job = app.dataset_prepare_jobs[version_id]
    job.update(state="running", started_at=datetime.now(UTC).isoformat())
    try:
        prepare_dataset(app, version_id, lambda name, done, total: job.update(stage=name, done=done, total=total))
        job.update(state="done")
    except Exception as e:
        job.update(state="error", error=f"{getattr(e, 'code', type(e).__name__)}: {e}")
