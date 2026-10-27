"""Kiểm Unit per_frame baseline từ core ID và metadata nguồn; trả index và thời điểm, không import ML."""

import math
import re

from ..errors import ServiceError

_UNIT = re.compile(r"(?P<core>.+):(?P<t0>\d+\.\d{3})-(?P<t1>\d+\.\d{3})")


def frame_index(unit_id: str, core_source_id: str, metadata: dict) -> tuple[int, float]:
    """Chỉ nhận ID chuẩn trên lưới PyAV cố định 4 fps, trong phạm vi frame đã xác minh."""
    m = _UNIT.fullmatch(unit_id)
    if not m or m["core"] != core_source_id or m["t0"] != m["t1"]:
        raise ServiceError("unit_not_in_source", "unit không thuộc nguồn", 422)
    fps, n = metadata.get("fps"), metadata.get("n_frames")
    if (type(fps) not in (int, float) or not math.isfinite(fps) or fps <= 0
            or type(n) is not int or n <= 0):
        raise ServiceError("source_unavailable", "nguồn chưa có metadata decoder đã xác minh", 409)
    t = float(m["t0"])
    if not math.isfinite(t) or t >= n / fps:
        raise ServiceError("unit_not_in_source", "unit ngoài phạm vi nguồn", 422)
    idx = round(t * fps)
    step = max(fps / 4.0, 1.0)
    if f"{idx / fps:.3f}" != m["t0"] or not 0 <= idx < n or round(round(idx / step) * step) != idx:
        raise ServiceError("unit_not_in_source", "unit không nằm trên lưới frame của nguồn", 422)
    return idx, idx / fps
