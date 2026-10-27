"""Bằng chứng hiển thị cho từng unit (CONTRACTS §8.0, #59): khung detector (`det.*`), nhãn cảnh (`tag.*`), telemetry quanh t0.

Chỉ đọc cache (`Context.cached`) — không tính field nặng; nguồn nào chưa có thì trả None để UI ẩn. Nhãn ẩn của dataset không đi qua đây.
"""

from __future__ import annotations

from ..core.registry import REGISTRY, discover
from . import App, ServiceError
from .session import get_session, make_context

TELEMETRY_WINDOW_S = 3.0   # cùng cửa sổ ±3 s của clip ngữ cảnh trên UI


def _fields(prefix: str) -> list[str]:
    """Tên field (theo `provides`) của mọi feature đã đăng ký có tiền tố `prefix`."""
    return sorted({p for n in REGISTRY.names("feature") if (p := REGISTRY.get("feature", n).provides).startswith(prefix)})


def get_evidence(app: App, sid: str, unit_ids: list[str]) -> list[dict]:
    discover()
    ctx = make_context(app, get_session(app, sid))
    by_id = {u.id: u for u in ctx.units}
    missing = [i for i in unit_ids if i not in by_id]
    if missing:
        raise ServiceError("unit_not_found", f"phiên {sid}: không có unit {missing[:5]}", 404)
    units = [by_id[i] for i in unit_ids]
    out = [{"unit_id": u.id, "detections": None, "tags": None, "telemetry": None} for u in units]

    for name in _fields("det."):
        table = ctx.cached(name, units)
        if table is not None:
            for row, dets in zip(out, table.values):
                row["detections"] = (row["detections"] or []) + [{**d, "source": name} for d in dets]
    for name in _fields("tag."):
        table = ctx.cached(name, units)
        if table is not None:
            for row, tags in zip(out, table.values):
                row["tags"] = {**(row["tags"] or {}), **tags}

    source = REGISTRY.build("source", app.spec["source"])
    samples = {v: source.telemetry(ctx.videos[v]) for v in dict.fromkeys(u.video_id for u in units)}
    for row, u in zip(out, units):
        series = samples[u.video_id]
        if series is not None:
            row["telemetry"] = [s for s in series if abs(s["t"] - u.t0) <= TELEMETRY_WINDOW_S]
    return out
