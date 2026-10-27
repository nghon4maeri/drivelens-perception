"""`get_view`: dựng view theo tên trên ctx của phiên (CONTRACTS §5 dòng 14, #50). View lấy qua registry, không import `components/`."""

from __future__ import annotations

from pydantic import ValidationError

from ..core.registry import REGISTRY, discover
from ..core.types import ViewSpec
from . import App, ServiceError
from .proposal import session_context


def get_view(app: App, sid: str, name: str, params: dict) -> ViewSpec:
    discover()
    try:
        view = REGISTRY.build("view", {"type": name, "params": params})
    except KeyError as e:
        raise ServiceError("view_not_found", str(e.args[0]), 404) from None
    except ValidationError as e:
        raise ServiceError("invalid_params", str(e)) from None
    ctx = session_context(app, sid)
    try:
        return view.build(ctx)
    except (KeyError, ValueError) as e:     # vd proposal_n / video_id không có, field không vẽ được
        raise ServiceError("invalid_params", str(e.args[0] if e.args else e)) from None
