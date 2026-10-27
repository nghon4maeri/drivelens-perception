"""`export(session_id, format)`, `list_exports(session_id?)`, `get_export_file(session_id, export_id)`
(CONTRACTS §5 dòng 12, §11.2; architecture §5.3 ExportDTO; #46, #48, #54)."""

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel

from ..service import App
from ..service import export as svc
from ..service import export_snapshot as svc_snapshot
from ..service import session as svc_session
from .base import Command, command
from .sessions import SessionIdIn


class ExportIn(SessionIdIn):
    format: str = Field("csv_images", description="tên exporter")


class ExportDTO(BaseModel):
    id: str
    session_id: str
    session_name: str | None
    goal: str
    format: str
    n_frames: int
    created_at: str
    download_url: str


class ExportOut(ExportDTO):
    path: str   # file chính trên máy chạy backend (CLI / debug); UI tải qua download_url


def export_dto(app: App, meta: dict, sessions: dict[str, dict]) -> dict:
    sid = meta["session_id"]
    row = sessions.get(sid) or svc_session.get_session(app, sid)
    return {"id": meta["id"], "session_id": sid, "session_name": row["name"], "goal": row["spec"]["goal"], "format": meta["format"],
            "n_frames": meta["n_frames"], "created_at": meta["created_at"],
            "download_url": f"/api/v1/exports/{sid}/{meta['id']}/download"}   # route get_export_file (architecture §5.2)


@command
class Export(Command):
    name = "export"
    description = "Xuất frame đã keep của phiên (CSV + ảnh) để gán nhãn; mỗi lần một bản mới, giữ mọi lần (tải .zip qua download_url)."
    Input, Output = ExportIn, ExportOut
    mutates = needs_confirm = True

    def run(self, inp: ExportIn, app: App) -> ExportOut:
        meta = svc.export(app, inp.session_id, inp.format)
        return ExportOut(**export_dto(app, meta, {}), path=str(meta["path"]))


class ListExportsIn(BaseModel):
    session_id: str | None = Field(None, description="chỉ lấy các lần xuất của phiên này; bỏ trống = mọi phiên")


class ExportList(RootModel[list[ExportDTO]]):
    pass


@command
class ListExports(Command):
    name = "list_exports"
    description = "Liệt kê các lần xuất (mọi phiên hoặc một phiên), mới nhất trước, kèm link tải .zip."
    Input, Output = ListExportsIn, ExportList

    def run(self, inp: ListExportsIn, app: App) -> ExportList:
        sessions = {r["id"]: r for r in app.db.list_sessions()}
        return ExportList([ExportDTO(**export_dto(app, m, sessions)) for m in svc.list_exports(app, inp.session_id)])


class ExportFileIn(SessionIdIn):
    export_id: str


class ExportFileOut(BaseModel):
    path: str
    filename: str


@command
class GetExportFile(Command):
    name = "get_export_file"
    description = "Đường dẫn file .zip (CSV + ảnh) của một lần xuất cho route tải về; client chỉ thấy URL download."
    Input, Output = ExportFileIn, ExportFileOut

    def run(self, inp: ExportFileIn, app: App) -> ExportFileOut:
        path = svc.export_zip(app, inp.session_id, inp.export_id)
        return ExportFileOut(path=str(path), filename=f"rav26_{inp.session_id[:8]}_{inp.export_id}.zip")


class ExportSnapshotIn(BaseModel):
    snapshot_id: str


@command
class ExportSnapshot(Command):
    name = "export_snapshot"
    description = "Tạo ZIP tạm từ snapshot bất biến, kiểm SHA-256 nguồn và frame_idx; API dọn sau khi tải."
    Input, Output = ExportSnapshotIn, ExportFileOut

    def run(self, inp: ExportSnapshotIn, app: App) -> ExportFileOut:
        return ExportFileOut(**svc_snapshot.export_snapshot(app, inp.snapshot_id))


class VerifySnapshotOut(BaseModel):
    ok: bool


@command
class VerifySnapshot(Command):
    name = "verify_snapshot"
    description = "Kiểm nguồn snapshot trước tải; trả source_unavailable có danh sách nguồn thiếu/đổi."
    Input, Output = ExportSnapshotIn, VerifySnapshotOut

    def run(self, inp: ExportSnapshotIn, app: App) -> VerifySnapshotOut:
        return VerifySnapshotOut(**svc_snapshot.verify_snapshot(app, inp.snapshot_id))
