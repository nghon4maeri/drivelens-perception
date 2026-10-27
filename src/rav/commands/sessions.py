"""`list_sessions`, `get_session`, `create_session(pool, goal, target_k, name?)`, `add_videos_to_session` (#56), `list_units(session_id, video_id?)`
(CONTRACTS §11.2, architecture §5.3 SessionDTO, UnitDTO; #46, #48)."""

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel

from ..core.types import DatasetReference, PoolFilter
from ..service import App
from ..service import proposal as svc_proposal
from ..service import reference_model as svc_model
from ..service import review_state as svc_state
from ..service import session as svc
from .base import Command, command
from .models import ModelDTO
from .videos import Empty


class SessionDTO(BaseModel):
    id: str
    pool: PoolFilter
    name: str | None
    created_at: str
    target_k: int
    goal: str
    n_units: int
    n_kept: int
    n_dropped: int
    n_proposals: int
    reference: DatasetReference = Field(default_factory=DatasetReference)
    latest_snapshot: dict | None = None
    reference_model: ModelDTO | None = None


def session_dto(app: App, row: dict) -> SessionDTO:
    state = svc_state.get_state(app, row["id"])
    spec = row["spec"]
    return SessionDTO(id=row["id"], pool=spec["pool"], name=row["name"], created_at=row["created_at"],
                      target_k=spec["target_k"], goal=spec["goal"], n_units=row["n_units"], n_kept=len(state.kept),
                      n_dropped=len(state.dropped), n_proposals=len(svc_proposal.list_proposals(app, row["id"])),
                      reference=spec.get("reference", {}), latest_snapshot=svc.latest_snapshot(app, row),
                      reference_model=svc_model.dto(app, svc_model.get_model(app, spec["reference"]["model_id"]))
                      if spec.get("reference", {}).get("model_id") else None)


class SessionIdIn(BaseModel):
    session_id: str


class CreateSessionIn(BaseModel):
    pool: PoolFilter = Field(description="video_ids bỏ trống = mọi video đang có; sau khi tạo chỉ thêm video bằng add_videos_to_session")
    goal: str = Field(description="thế nào là đáng giữ; ghi vào mỗi decision")
    target_k: int = Field(gt=0, description="ngân sách: tổng số frame muốn chọn")
    name: str | None = None
    reference: DatasetReference = Field(default_factory=DatasetReference)


class SessionList(RootModel[list[SessionDTO]]):
    pass


@command
class CreateSession(Command):
    name = "create_session"
    description = "Bắt đầu phiên duyệt trên một pool video với goal và target_k. Video chưa chạy P1 sẽ được tính (chậm lần đầu)."
    Input, Output = CreateSessionIn, SessionDTO
    mutates = needs_confirm = True

    def run(self, inp: CreateSessionIn, app: App) -> SessionDTO:
        return session_dto(app, svc.create_session(app, inp.pool, inp.goal, inp.target_k, inp.name, inp.reference))


class AddVideosIn(SessionIdIn):
    video_ids: list[str] = Field(min_length=1, description="video đã chuẩn bị (P1 xong) để thêm vào pool")


@command
class AddVideosToSession(Command):
    name = "add_videos_to_session"
    description = "Thêm video đã chuẩn bị vào pool của phiên (không bớt). Video đang tiền xử lý → 409 video_preparing."
    Input, Output = AddVideosIn, SessionDTO
    mutates = needs_confirm = True

    def run(self, inp: AddVideosIn, app: App) -> SessionDTO:
        return session_dto(app, svc.add_videos_to_session(app, inp.session_id, inp.video_ids))


@command
class ListSessions(Command):
    name = "list_sessions"
    description = "Liệt kê mọi phiên (pool, goal, tiến độ kept / target_k) để chọn và mở lại."
    Input, Output = Empty, SessionList

    def run(self, inp: Empty, app: App) -> SessionList:
        return SessionList([session_dto(app, r) for r in svc.list_sessions(app)])


@command
class GetSession(Command):
    name = "get_session"
    description = "Mở lại một phiên: thông tin phiên và tiến độ."
    Input, Output = SessionIdIn, SessionDTO

    def run(self, inp: SessionIdIn, app: App) -> SessionDTO:
        return session_dto(app, svc.get_session(app, inp.session_id))


THUMB_SIDE = 320


class UnitDTO(BaseModel):
    id: str
    video_id: str
    t0: float
    t1: float
    thumb_url: str
    frame_url: str


class ListUnitsIn(SessionIdIn):
    video_id: str | None = Field(None, description="chỉ lọc hiển thị; pool không đổi")


class UnitList(RootModel[list[UnitDTO]]):
    pass


@command
class ListUnits(Command):
    name = "list_units"
    description = "Unit trong pool của phiên (thứ tự video, thời gian), kèm URL ảnh thumbnail / ảnh gốc; lọc theo video_id nếu cần."
    Input, Output = ListUnitsIn, UnitList

    def run(self, inp: ListUnitsIn, app: App) -> UnitList:
        units = []
        for u in svc.list_units(app, inp.session_id, inp.video_id):
            url = f"/api/v1/videos/{u.video_id}/frames/{u.anchor.idx}"   # route get_frame (architecture §5.2)
            units.append(UnitDTO(id=u.id, video_id=u.video_id, t0=u.t0, t1=u.t1,
                                 thumb_url=f"{url}?max_side={THUMB_SIDE}", frame_url=url))
        return UnitList(units)
