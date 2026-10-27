"""`get_state`, `update_state(changes)`: keep / drop / clear; chặn keep vượt target_k (CONTRACTS §9.7, architecture §5.3 ReviewStateDTO)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..core.types import ReviewState
from ..service import App
from ..service import review_state as svc
from .base import Command, command
from .sessions import SessionIdIn


class DecisionDTO(BaseModel):
    unit_id: str
    action: Literal["keep", "drop", "clear"]
    actor: str
    goal: str
    proposal_n: int | None = None
    shown_at: datetime | None = None
    decided_at: datetime
    reason: str | None = None
    confidence: float | None = None


class ReviewStateDTO(BaseModel):
    kept_ids: list[str]
    dropped_ids: list[str]
    history: list[DecisionDTO]


def state_dto(state: ReviewState) -> ReviewStateDTO:
    return ReviewStateDTO(kept_ids=sorted(state.kept), dropped_ids=sorted(state.dropped),
                          history=[DecisionDTO.model_validate(d, from_attributes=True) for d in state.history])


class Change(BaseModel):
    unit_id: str
    action: Literal["keep", "drop", "clear"]
    proposal_n: int | None = Field(None, description="quyết định cho proposal nào (None nếu sửa ngoài proposal)")
    shown_at: datetime | None = None
    reason: str | None = None


class UpdateStateIn(SessionIdIn):
    changes: list[Change]


@command
class GetState(Command):
    name = "get_state"
    description = "Xem review_state của phiên: unit đã keep / drop và log quyết định."
    Input, Output = SessionIdIn, ReviewStateDTO

    def run(self, inp: SessionIdIn, app: App) -> ReviewStateDTO:
        return state_dto(svc.get_state(app, inp.session_id))


@command
class UpdateState(Command):
    name = "update_state"
    description = ("Keep / drop / clear (bỏ quyết định) một lô unit, áp theo thứ tự. Từ chối cả lô nếu có unit ngoài pool "
                   "hoặc số keep vượt target_k.")
    Input, Output = UpdateStateIn, ReviewStateDTO
    mutates = needs_confirm = True

    def run(self, inp: UpdateStateIn, app: App) -> ReviewStateDTO:
        return state_dto(svc.update_state(app, inp.session_id, [c.model_dump() for c in inp.changes]))
