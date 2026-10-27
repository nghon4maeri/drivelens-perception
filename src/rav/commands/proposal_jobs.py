"""Queue/list durable proposal jobs through the shared command boundary (CONTRACTS §18)."""
import secrets

from pydantic import BaseModel, RootModel

from ..service import proposal_jobs as svc
from .base import Command, command
from .proposals import ProposeIn
from .sessions import SessionIdIn


class ProposalJobDTO(BaseModel):
    id: str
    session_id: str
    state: str
    stage: str
    done: int
    total: int
    error: str | None
    proposal_n: int | None
    created_at: str


@command
class QueueProposal(Command):
    name = "queue_proposal"
    description = "Xếp đề xuất nền theo snapshot phiên; component/recipe khai báo các bước, không sửa quyết định."
    Input, Output = ProposeIn, ProposalJobDTO
    mutates = True

    def run(self, inp, app):
        seed = secrets.randbelow(2**31) if inp.seed is None else inp.seed
        return ProposalJobDTO(**svc.queue(app, inp.session_id, inp.recipe, inp.config, inp.batch_size, seed,
                                          strategy_params=inp.strategy_params, model_id=inp.model_id))


class JobList(RootModel[list[ProposalJobDTO]]):
    pass


@command
class ListProposalJobs(Command):
    name = "list_proposal_jobs"
    description = "Trạng thái proposal job đã lưu; đọc không chạy model hoặc đề xuất."
    Input, Output = SessionIdIn, JobList

    def run(self, inp, app):
        return JobList([ProposalJobDTO(**j) for j in svc.list_jobs(app, inp.session_id)])
