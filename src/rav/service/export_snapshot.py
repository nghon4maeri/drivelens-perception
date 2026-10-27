"""S8: snapshot_id → ZIP tạm, kiểm SHA-256 nguồn và trích đúng frame, không sinh ảnh lâu dài.

Input là manifest snapshot bất biến; output path/filename cho command export_snapshot.
API dọn file sau gửi; command caller ngoài API chịu trách nhiệm dọn path sau đọc.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
import zipfile
from collections import Counter
from pathlib import Path

from PIL import Image

from ..core.registry import REGISTRY, discover
from ..core.types import Frame
from ..pipeline import datasets, prepare
from . import App, ServiceError
from . import dataset as dataset_svc
from .session import get_session


def cleanup_orphans(app: App) -> None:
    """Chỉ quét namespace ZIP tạm snapshot; một API local trên một output (LOCAL_LIMITS)."""
    # local-only: không dùng chung output giữa nhiều API process.
    root = app.output_dir / "tmp"
    for path in root.glob("snapshot-*.zip"):
        if re.fullmatch(r"snapshot-[0-9a-f]{32}\.zip", path.name) and path.is_file() and not path.is_symlink():
            path.unlink(missing_ok=True)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for data in iter(lambda: f.read(1 << 20), b""):
            digest.update(data)
    return digest.hexdigest()


class _LimitedFile(io.FileIO):
    """Giới hạn cả ghi ảnh và metadata/central directory của ZIP, không chỉ kiểm sau ghi."""

    def __init__(self, path: Path, limit: int):
        super().__init__(path, "w")
        self.limit = limit

    def write(self, data):
        if self.tell() + len(data) > self.limit:
            raise ServiceError("export_too_large", "ZIP snapshot vượt giới hạn dung lượng cấu hình", 413)
        return super().write(data)


def _unavailable(sources: list[dict]) -> None:
    raise ServiceError("source_unavailable", "Nguồn snapshot bị thiếu hoặc đã thay đổi; không thể xuất đủ mẫu.",
                       409, {"sources": sources})


def _sources(app: App, snapshot: dict, members: list[dict]) -> dict:
    row = get_session(app, snapshot["session_id"])
    catalog = prepare.catalog(row["spec"])
    counts = Counter((m["source_kind"], m["source_id"]) for m in members)
    groups = {}
    for m in members:
        groups.setdefault((m["source_kind"], m["source_id"]), []).append(m)
    resolved, errors = {}, []
    for (kind, source_id), group in groups.items():
        problem = None
        try:
            if kind == "video":
                provenance = group[0]["provenance"]["video"]
                video = catalog.get(source_id)
                if video is None:
                    # ID đổi khi raw bị thay: kiểm tên đóng băng để phân biệt changed với missing.
                    name = provenance.get("name")
                    root = Path(row["spec"]["source"]["params"]["root"])
                    candidate = root / name if name and Path(name).name == name else None
                    problem = "changed" if candidate is not None and candidate.exists() else "missing"
                else:
                    actual_sha = _sha(Path(video.uri))
                    if any(m["provenance"]["video"]["sha256"] != actual_sha for m in group):
                        problem = "changed"
                    else:
                        resolved[(kind, source_id)] = video
            else:
                version = dataset_svc.get_version(app, source_id)
                check = dataset_svc.verify(app, source_id)
                if not check["ok"]:
                    problem = "missing" if check["missing"] else "changed"
                else:
                    root = datasets.resolve_folder(app.datasets_dir, version["folder"])
                    for m in group:
                        image = m["provenance"]["image"]
                        raw = datasets.inside(root, root / image["relpath"])
                        if _sha(raw) != image["sha256"]:
                            problem = "changed"
                            break
                        resolved[(kind, m["unit_id"])] = raw
        except FileNotFoundError:
            problem = "missing"
        except (OSError, datasets.DatasetImportError, ServiceError):
            problem = "changed"
        if problem:
            errors.append({"source_id": source_id, "kind": kind, "problem": problem, "n_samples": counts[(kind, source_id)]})
    if errors:
        _unavailable(errors)
    return resolved


def _manifest(app: App, snapshot_id: str) -> tuple[dict, list[dict]]:
    snapshot = app.db.snapshot(snapshot_id)
    if snapshot is None:
        raise ServiceError("snapshot_not_found", "Không có snapshot đã chọn", 404)
    return snapshot, app.db.snapshot_members(snapshot_id)


def verify_snapshot(app: App, snapshot_id: str) -> dict:
    """Kiểm nguồn cho FE trước download; GET download luôn kiểm lại nguồn."""
    snapshot, members = _manifest(app, snapshot_id)
    _sources(app, snapshot, members)
    return {"ok": True}


def export_snapshot(app: App, snapshot_id: str) -> dict:
    """Dùng snapshot DB trực tiếp, không dựng lại kept/context của phiên hiện tại."""
    snapshot, members = _manifest(app, snapshot_id)
    sources = _sources(app, snapshot, members)  # kiểm toàn bộ trước tạo ZIP
    discover()
    decoder = REGISTRY.build("decoder", snapshot["context"]["sample_spec"]["decoder"])
    dest = app.output_dir / "tmp" / f"snapshot-{uuid.uuid4().hex}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    exported = []
    try:
        with _LimitedFile(dest, app.max_snapshot_zip_bytes) as f, zipfile.ZipFile(f, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for m in members:
                provenance = m["provenance"]
                if m["source_kind"] == "video":
                    video = sources[("video", m["source_id"])]
                    frame = Frame(video.id, provenance["frame_idx"], provenance["t0"])
                    try:
                        pixels = decoder.read_exact(video, frame)
                    except NotImplementedError as e:
                        raise ServiceError("unsupported_decoder", "Decoder không hỗ trợ xác nhận frame_idx khi export snapshot") from e
                    except IndexError as e:
                        raise ServiceError("frame_mismatch", str(e), 409) from e
                    image = io.BytesIO()
                    Image.fromarray(pixels).save(image, "PNG")
                    data, suffix = image.getvalue(), ".png"
                    raw_sha = provenance["video"]["sha256"]
                else:
                    raw = sources[("image_set", m["unit_id"])]
                    data, suffix = raw.read_bytes(), raw.suffix.lower()
                    raw_sha = provenance["image"]["sha256"]
                    if hashlib.sha256(data).hexdigest() != raw_sha:
                        _unavailable([{"source_id": m["source_id"], "kind": "image_set", "problem": "changed", "n_samples": 1}])
                filename = f"images/{m['ord']:06d}{suffix}"
                z.writestr(filename, data)
                exported.append({**m, "file": filename, "source_sha256": raw_sha})
            _sources(app, snapshot, members)  # nguồn/nhãn đổi giữa lúc trích → không phục vụ ZIP
            z.writestr("manifest.json", json.dumps({"snapshot": snapshot, "members": exported}, ensure_ascii=False, sort_keys=True))
            rows = io.StringIO(newline="")
            writer = csv.writer(rows)
            writer.writerow(["ord", "unit_id", "source_kind", "source_id", "file", "source_sha256", "frame_idx", "t0", "provenance"])
            for m in exported:
                writer.writerow([m[k] for k in ("ord", "unit_id", "source_kind", "source_id", "file", "source_sha256")]
                                + [m["provenance"].get("frame_idx"), m["provenance"].get("t0"),
                                   json.dumps(m["provenance"], ensure_ascii=False, sort_keys=True)])
            z.writestr("manifest.csv", rows.getvalue().encode("utf-8"))
        return {"path": str(dest), "filename": f"rav26_snapshot_{snapshot['session_id'][:8]}_{snapshot['n']}.zip"}
    except FileNotFoundError:
        dest.unlink(missing_ok=True)
        _sources(app, snapshot, members)  # trả source_unavailable nếu raw biến mất trong khi xuất
        raise
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
