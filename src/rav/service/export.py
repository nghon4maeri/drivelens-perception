"""Gọi exporter cho các unit đã keep của phiên; liệt kê / đóng zip các lần xuất (CONTRACTS §5 dòng 12, #46, #48, #54).

Mỗi lần xuất một thư mục mới `output/exports/<session_id>/<export_id>/` (giữ mọi lần) + `meta.json`; liệt kê bằng cách đọc các
`meta.json` (không có bảng DB). Zip tạo lần đầu khi tải (`<export_id>.zip` cạnh thư mục) rồi dùng lại.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..core.registry import REGISTRY, discover
from . import App, ServiceError
from .review_state import get_state
from .session import get_session, make_context

_EXPORT_ID = re.compile(r"\d{8}T\d{9}Z")
_SESSION_ID = re.compile(r"[0-9a-f]{32}")


def _root(app: App) -> Path:
    return app.output_dir / "exports"


def _dir(app: App, sid: str, export_id: str) -> Path:
    # id ghép vào đường dẫn → chỉ nhận đúng mẫu (không có "..", "/")
    if not _SESSION_ID.fullmatch(sid) or not _EXPORT_ID.fullmatch(export_id):
        raise ServiceError("export_not_found", f"không có lần xuất {sid}/{export_id}", 404)
    d = _root(app) / sid / export_id
    if not (d / "meta.json").is_file():
        raise ServiceError("export_not_found", f"không có lần xuất {sid}/{export_id}", 404)
    return d


def export(app: App, sid: str, fmt: str) -> dict:
    """Xuất các unit đang kept vào thư mục mới; trả meta của lần xuất (kèm `path` file chính)."""
    discover()
    row = get_session(app, sid)
    try:
        exporter = REGISTRY.build("exporter", {"type": fmt})
    except KeyError as e:
        raise ServiceError("exporter_not_found", str(e.args[0]), 404) from None
    kept = get_state(app, sid).kept
    ctx = make_context(app, row)
    units = [u for u in ctx.units if u.id in kept]
    now = datetime.now(UTC)
    export_id = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"
    # local-only: ghi ra đĩa của máy chạy backend, giữ mọi lần, không dọn (LOCAL_SINGLE_USER_LIMITS #8)
    dest = _root(app) / sid / export_id
    dest.mkdir(parents=True)
    path = exporter.export(units, dest, ctx)
    meta = {"id": export_id, "session_id": sid, "format": fmt, "n_frames": len(units), "created_at": now.isoformat(),
            "main_file": path.relative_to(dest).as_posix()}
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {**meta, "path": path}


def list_exports(app: App, sid: str | None = None) -> list[dict]:
    """Meta các lần xuất (một phiên hoặc mọi phiên), mới nhất trước."""
    pattern = f"{sid}/*/meta.json" if sid else "*/*/meta.json"
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in _root(app).glob(pattern)] if _root(app).is_dir() else []
    return sorted(metas, key=lambda m: m["created_at"], reverse=True)


def export_zip(app: App, sid: str, export_id: str) -> Path:
    """File zip (CSV + ảnh) của một lần xuất; tạo lần đầu rồi dùng lại (lần xuất không bao giờ bị sửa)."""
    d = _dir(app, sid, export_id)
    zip_path = d.with_suffix(".zip")
    if not zip_path.is_file():
        tmp = shutil.make_archive(str(d.with_name(d.name + ".tmp")), "zip", root_dir=d)
        Path(tmp).replace(zip_path)   # ghi xong mới đổi tên → không phục vụ zip dở
    return zip_path
