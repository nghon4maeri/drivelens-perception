"""`create_snapshot`, `list_snapshots`, `get_snapshot` — snapshot bất biến của collection (CONTRACTS §11.2, §14.2; architecture §5.3 SnapshotDTO)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, RootModel

from ..service import App
from ..service import snapshot as svc
from .base import Command, command
from .sessions import SessionIdIn


class SnapshotDTO(BaseModel):
    id: str
    session_id: str
    n: int
    created_at: str
    created_by: str
    note: str | None
    n_members: int
    members_hash: str
    context_hash: str
    decision_seq: int
    is_current: bool        # cả hai hash khớp collection + context hiện tại
    decisions_after: int    # số quyết định ghi thêm sau khi chốt


class CreateSnapshotOut(SnapshotDTO):
    created: bool           # False = collection + context không đổi → trả snapshot mới nhất


class MemberDTO(BaseModel):
    ord: int
    unit_id: str
    source_kind: Literal["video", "image_set"]
    source_id: str
    provenance: dict


class SnapshotDetailDTO(SnapshotDTO):
    context: dict
    members: list[MemberDTO]


class SnapshotList(RootModel[list[SnapshotDTO]]):
    pass


def _dto(cls, snap: dict, **extra):
    return cls(**{k: snap[k] for k in SnapshotDTO.model_fields}, **extra)


class CreateSnapshotIn(SessionIdIn):
    note: str | None = None


class SnapshotIdIn(BaseModel):
    snapshot_id: str


@command
class CreateSnapshot(Command):
    name = "create_snapshot"
    description = ("Chốt collection (frame đang keep) + context phiên thành snapshot bất biến, chỉ tham chiếu raw (không sinh ảnh). "
                   "Không đổi gì so với snapshot mới nhất → trả lại nó. Collection rỗng → 422.")
    Input, Output = CreateSnapshotIn, CreateSnapshotOut
    mutates = needs_confirm = True

    def run(self, inp: CreateSnapshotIn, app: App) -> CreateSnapshotOut:
        snap, created = svc.create_snapshot(app, inp.session_id, inp.note)
        return _dto(CreateSnapshotOut, snap, created=created)


@command
class ListSnapshots(Command):
    name = "list_snapshots"
    description = "Snapshot của phiên, mới nhất trước, kèm còn hiện hành không (collection / context đã đổi chưa)."
    Input, Output = SessionIdIn, SnapshotList

    def run(self, inp: SessionIdIn, app: App) -> SnapshotList:
        return SnapshotList([_dto(SnapshotDTO, s) for s in svc.list_snapshots(app, inp.session_id)])


@command
class GetSnapshot(Command):
    name = "get_snapshot"
    description = "Một snapshot: context đóng băng + danh sách mẫu kèm provenance (nguồn raw, vị trí frame, quyết định, recipe)."
    Input, Output = SnapshotIdIn, SnapshotDetailDTO

    def run(self, inp: SnapshotIdIn, app: App) -> SnapshotDetailDTO:
        snap, members = svc.get_snapshot(app, inp.snapshot_id)
        return _dto(SnapshotDetailDTO, snap, context=snap["context"], members=[MemberDTO(**m) for m in members])
