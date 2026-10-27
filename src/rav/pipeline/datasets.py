"""Quét nguồn ảnh và nhãn YOLO, trả manifest/report để service lưu (CONTRACTS §14.4).

Input: thư mục dưới gốc dataset, format nhãn, giới hạn ảnh. Output: thành viên, hash, report;
không ghi DB, không sao chép raw. CLI đi qua command import-dataset.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path, PureWindowsPath

import yaml
from PIL import Image, ImageOps

EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
FIELDS = ("emb.dinov2_s", "hash.phash")


class DatasetImportError(ValueError):
    """Lỗi dữ liệu import, service chuyển sang mã HTTP tương ứng."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise DatasetImportError("invalid_folder", "đường dẫn nguồn nằm ngoài gốc dataset")
    return resolved


def resolve_folder(root: Path, folder: str) -> Path:
    path = Path(folder)
    if not folder or path.is_absolute() or PureWindowsPath(folder).drive or ".." in PureWindowsPath(folder).parts:
        raise DatasetImportError("invalid_folder", "folder phải tương đối, không chứa '..'")
    resolved = inside(root, root / path)
    if resolved == root.resolve() or not resolved.is_dir():
        raise DatasetImportError("invalid_folder", "thư mục import không tồn tại hoặc là gốc dataset")
    return resolved


def image_files(root: Path):
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in dirs:
            inside(root, Path(current) / name)
        for name in sorted(files):
            if not name.startswith(".") and Path(name).suffix.lower() in EXTENSIONS:
                path = Path(current) / name
                inside(root, path)
                yield path


def annotation(root: Path, requested: str) -> tuple[str, list[str] | None]:
    if requested == "none":
        return "none", None
    classes = None
    try:
        txt, config = root / "classes.txt", root / "data.yaml"
        if txt.exists():
            inside(root, txt)
            classes = [s.strip() for s in txt.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
        elif config.exists():
            inside(root, config)
            content = yaml.safe_load(config.read_text(encoding="utf-8-sig"))
            names = content.get("names") if isinstance(content, dict) else None
            if isinstance(names, dict):
                if any(isinstance(k, bool) or str(k) not in {str(i) for i in range(len(names))} for k in names):
                    raise ValueError("chỉ số lớp YAML phải liên tục từ 0")
                indexed = {int(k): v for k, v in names.items()}
                names = [indexed[i] for i in range(len(indexed))]
            classes = names
        if not isinstance(classes, list) or not classes or any(not isinstance(c, str) or not c.strip() for c in classes):
            classes = None
    except DatasetImportError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, KeyError, TypeError):
        classes = None
    if requested == "yolo" and classes is None:
        raise DatasetImportError("invalid_annotation", "YOLO cần classes.txt hoặc data.yaml với danh sách lớp hợp lệ")
    if requested == "yolo" or classes is not None and (root / "labels").is_dir():
        inside(root, root / "labels")
        return "yolo", classes
    return "none", None


def label(root: Path, relpath: str, classes: list[str] | None) -> tuple[dict, str | None]:
    out = {"label_status": "none", "label_relpath": None, "label_sha256": None, "label_counts": None}
    if classes is None:
        return out, None
    parts = Path(relpath).parts
    if parts[0] != "images":
        out["label_status"] = "missing"
        return out, None
    path = root / "labels" / Path(*parts[1:]).with_suffix(".txt")
    inside(root, path)
    if not path.is_file():
        out["label_status"] = "missing"
        return out, None
    data = path.read_bytes()
    out.update(label_relpath=path.relative_to(root).as_posix(), label_sha256=hashlib.sha256(data).hexdigest())
    try:
        counts = Counter()
        for line in data.decode("utf-8-sig").splitlines():
            if not line.strip():
                continue
            values = [float(v) for v in line.split()]
            if len(values) != 5 or not all(math.isfinite(v) for v in values):
                raise ValueError("nhãn phải có đúng 5 số hữu hạn: cls cx cy w h")
            cls, cx, cy, w, h = values
            if not cls.is_integer() or not 0 <= cls < len(classes):
                raise ValueError("chỉ số lớp không hợp lệ")
            if not all(0 <= v <= 1 for v in (cx, cy, w, h)) or w <= 0 or h <= 0:
                raise ValueError("toạ độ ngoài [0,1] hoặc kích thước box không dương")
            counts[classes[int(cls)]] += 1
        out.update(label_status="labeled" if counts else "empty", label_counts=dict(counts) if counts else None)
        return out, None
    except (ValueError, UnicodeError) as e:
        out["label_status"] = "invalid"
        return out, str(e)


def scan(root: Path, requested: str, max_images: int) -> dict:
    fmt, classes = annotation(root, requested)
    groups = defaultdict(list)
    skipped, invalid, duplicates, conflicts = [], [], [], []
    seen = 0
    files = []
    for path in image_files(root):
        files.append(path)
        if len(files) > max_images:
            raise DatasetImportError("too_many_images", f"số file ảnh vượt giới hạn {max_images}")
    seen = len(files)
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        relpath = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                size = ImageOps.exif_transpose(image).size
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            skipped.append({"relpath": relpath, "reason": str(e)})
            continue
        sha = hashlib.sha256(data).hexdigest()
        labels, reason = label(root, relpath, classes)
        if reason:
            invalid.append({"relpath": relpath, "reason": reason})
        groups[sha].append({"key": sha[:16], "relpath": relpath, "sha256": sha,
                            "size_bytes": len(data), "width": size[0], "height": size[1], **labels})
    if not groups:
        raise DatasetImportError("empty_dataset", "không có ảnh đọc được")
    members = []
    for sha, copies in groups.items():
        member = dict(copies[0])
        if len(copies) > 1:
            if len({(c["label_status"], c["label_sha256"]) for c in copies}) > 1:
                member.update(label_status="conflict", label_relpath=None, label_sha256=None, label_counts=None)
                conflicts.append({"key": sha[:16], "copies": [{k: c[k] for k in
                                  ("relpath", "label_relpath", "label_status", "label_sha256")} for c in copies]})
            else:
                duplicates.append({"kept": member["relpath"], "dropped": [c["relpath"] for c in copies[1:]]})
        members.append(member)
    members.sort(key=lambda m: m["key"])
    counts = Counter()
    for m in members:
        counts.update(m["label_counts"] or {})
    labels = {s: sum(m["label_status"] == s for m in members)
              for s in ("none", "labeled", "empty", "missing", "invalid", "conflict")}
    report = {"n_files_seen": seen, "n_images": len(members), "skipped": skipped[:200], "n_skipped": len(skipped),
              "duplicates": duplicates[:200], "n_duplicates": sum(len(d["dropped"]) for d in duplicates),
              "conflicts": conflicts[:200], "n_conflicts": len(conflicts),
              "labels": {**labels, "invalid_examples": invalid[:200], "n_invalid_examples": len(invalid)},
              "class_counts": dict(counts), "annotation_detected": fmt}
    manifest = {"format": 1, "annotation_format": fmt, "classes": classes,
                "members": [[m[k] for k in ("key", "relpath", "sha256", "label_sha256")] for m in members],
                "conflicts": sorted(conflicts, key=lambda c: c["key"])}
    return {"members": members, "manifest": manifest, "annotation_format": fmt, "classes": classes, "import_report": report,
            "manifest_hash": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()}


def make_context(version: dict, members: list[dict], root: Path, store, **kwargs):
    """Context ảnh từ manifest bất biến; không quét lại raw để định nghĩa lại các mẫu."""
    from ..core.context import Context
    from ..core.execution import Execution, image_spec_hash
    from ..core.registry import discover
    from ..core.types import ImageRef, ImageSetRef, Unit

    discover()
    units = []
    for m in members:
        ref = ImageRef(version["id"], m["key"], m["relpath"])
        units.append(Unit(m["sample_id"], None, None, None, "image", ref, (ref,)))
    source = ImageSetRef(version["id"], version["dataset_id"], str(root))
    return Context(sorted(units, key=lambda u: u.order_key), images={source.id: source},
                   execution=Execution(store, image_spec_hash()), **kwargs)


def is_prepared(version: dict, members: list[dict], store) -> bool:
    from ..core.execution import image_spec_hash
    from ..core.registry import REGISTRY, discover

    discover()
    expected = {m["sample_id"] for m in members}
    for name in FIELDS:
        table = store.get(REGISTRY.feature_for(name)().key, (version["id"], image_spec_hash()))
        if table is None or len(table.unit_ids) != len(expected) or set(table.unit_ids) != expected:
            return False
    return True
