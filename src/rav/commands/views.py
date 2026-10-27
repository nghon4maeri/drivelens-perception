"""`get_view(session_id, name, params)` → ViewSpec (CONTRACTS §5 dòng 14, §11.2; #50)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..service import App
from ..service import views as svc
from .base import Command, command
from .sessions import SessionIdIn


class GetViewIn(SessionIdIn):
    name: str = Field(description="tên view: embedding_map | timeline | duplicate_pairs | …")
    params: dict = Field(default_factory=dict, description="tham số của view (describe_component kind=view)")


class ViewSpecDTO(BaseModel):
    type: str
    data: dict


@command
class GetView(Command):
    name = "get_view"
    description = ("Dữ liệu cho một biểu đồ của phiên: embedding_map (bản đồ 2D), timeline (đề xuất / keep theo thời gian), "
                   "duplicate_pairs (cặp gần trùng theo khoảng cách pHash để kiểm ngưỡng).")
    Input, Output = GetViewIn, ViewSpecDTO

    def run(self, inp: GetViewIn, app: App) -> ViewSpecDTO:
        spec = svc.get_view(app, inp.session_id, inp.name, inp.params)
        return ViewSpecDTO(type=spec.type, data=spec.data)
