"""`explain_proposal(session_id, n)`: giải thích cấp đợt cho UI — chỉ dữ liệu thật của đề xuất đã lưu (CONTRACTS §11.2; #61)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..service import App
from ..service import explain as svc
from .base import Command, command
from .sessions import SessionIdIn


class ComponentDoc(BaseModel):
    role: str
    type: str
    doc: str


class NearestKept(BaseModel):
    unit_id: str
    kept_unit_id: str
    similarity: float = Field(description="cos trên similarity_field; cao = giống frame đã giữ")


class VideoSpread(BaseModel):
    video_id: str
    n: int
    t_min: float
    t_max: float


class ProposalExplainDTO(BaseModel):
    n: int
    recipe: str
    description: str
    goal: str
    uses_goal: bool = Field(description="có objective nào của recipe đọc mục tiêu phiên không")
    semantic_query: dict | None = None
    components: list[ComponentDoc]
    n_picks: int
    n_kept_at_proposal: int = Field(description="số frame đã giữ lúc đề xuất (phạm vi của nearest_kept)")
    relaxed: bool
    similarity_field: str
    field_used_by_recipe: bool = Field(description="False = recipe không đọc embedding; độ tương đồng chỉ là số đo thêm")
    nearest_kept: list[NearestKept]
    spread: list[VideoSpread]


class ExplainIn(SessionIdIn):
    n: int = Field(ge=1)


@command
class ExplainProposal(Command):
    name = "explain_proposal"
    description = "Giải thích một đợt đề xuất: recipe và cách chấm, có dùng mục tiêu phiên không, độ tương đồng tới frame đã giữ gần nhất, độ trải."
    Input, Output = ExplainIn, ProposalExplainDTO

    def run(self, inp: ExplainIn, app: App) -> ProposalExplainDTO:
        return ProposalExplainDTO(**svc.explain_proposal(app, inp.session_id, inp.n))
