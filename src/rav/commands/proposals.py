"""`propose(recipe, config?, batch_size, seed?, return_scores?)` (#45, #48) và lịch sử proposal `list_proposals` (CONTRACTS §11.2, §10; architecture §5.3 ProposalDTO)."""

from __future__ import annotations

import secrets

from pydantic import BaseModel, Field, RootModel

from ..core.types import RecipeConfig
from ..proposal_response import batch_scores, proposal_response
from ..service import App, ServiceError
from ..service import proposal as svc
from .base import Command, command
from .sessions import SessionIdIn


class PickDTO(BaseModel):
    unit_id: str
    rank: int
    combined: float
    breakdown: dict[str, float]


class ProposalInput(BaseModel):
    recipe: str
    config: RecipeConfig | None
    config_hash: str
    kept_ids: list[str]
    dropped_ids: list[str]
    batch_size: int
    seed: int
    fields_used: list[str]            # "name@version"
    execution: dict | None = None
    strategy_params: dict | None = None
    strategy_version: str | None = None
    model_id: str | None = None


class ProposalDTO(BaseModel):
    n: int
    round: int
    created_at: str
    input: ProposalInput
    picks: list[PickDTO]
    relaxed: bool
    evidence: dict[str, list[dict]] = Field(default_factory=dict)
    scores: dict[str, list] | None = None   # dạng cột chỉ trên picks của batch
    strategy_trace: list[dict] = Field(default_factory=list)


def proposal_dto(record: dict) -> ProposalDTO:
    inp = {**record["input"], "fields_used": [f"{k['name']}@{k['version']}" for k in record["input"]["fields_used"]]}
    return ProposalDTO.model_validate(proposal_response({**record, "input": inp}))


class ProposeIn(SessionIdIn):
    recipe: str = Field(description="tên recipe có sẵn")
    config: RecipeConfig | None = Field(None, description="RecipeConfig ĐẦY ĐỦ đã chỉnh; config.name phải = recipe. Bỏ trống = recipe có sẵn")
    batch_size: int = Field(16, gt=0)
    seed: int | None = Field(None, description="bỏ trống = sinh ngẫu nhiên, lưu trong snapshot")
    return_scores: bool = Field(False, description="trả thêm điểm của các picks trong batch")
    strategy_params: dict | None = None
    model_id: str | None = None


class ProposalList(RootModel[list[ProposalDTO]]):
    pass


class GetProposalIn(SessionIdIn):
    n: int = Field(gt=0)
    return_scores: bool = False


@command
class GetProposal(Command):
    name = "get_proposal"
    description = "Đọc đợt đã lưu, tuỳ chọn điểm từ snapshot/cache; không chạy lại YOLO."
    Input, Output = GetProposalIn, ProposalDTO

    def run(self, inp, app):
        record = next((r for r in svc.list_proposals(app, inp.session_id) if r["n"] == inp.n), None)
        if record is None:
            raise ServiceError("proposal_not_found", "Không có đợt đề xuất này.", 404)
        dto = proposal_dto(record)
        if inp.return_scores:
            dto.scores = batch_scores(svc.scores(app, inp.session_id, record), record['picks'])
        return dto


@command
class Propose(Command):
    name = "propose"
    description = ("Đề xuất batch_size frame tiếp theo bằng một recipe (đổi recipe giữa các vòng được), trên state hiện tại "
                   "của phiên. Trả picks kèm điểm từng objective và snapshot input đã dùng.")
    Input, Output = ProposeIn, ProposalDTO
    mutates = True

    def run(self, inp: ProposeIn, app: App) -> ProposalDTO:
        seed = secrets.randbelow(2**31) if inp.seed is None else inp.seed
        record = svc.create_proposal(app, inp.session_id, inp.recipe, inp.config, inp.batch_size, seed,
                                     strategy_params=inp.strategy_params, model_id=inp.model_id)
        dto = proposal_dto(record)
        if inp.return_scores:
            dto.scores = batch_scores(svc.scores(app, inp.session_id, record), record['picks'])
        return dto


@command
class ListProposals(Command):
    name = "list_proposals"
    description = "Lịch sử đề xuất của phiên, theo thứ tự n."
    Input, Output = SessionIdIn, ProposalList

    def run(self, inp: SessionIdIn, app: App) -> ProposalList:
        return ProposalList([proposal_dto(r) for r in svc.list_proposals(app, inp.session_id)])
