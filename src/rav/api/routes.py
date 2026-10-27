"""Route REST `/api/v1/...` sinh từ bảng `(method, path, command)` + một handler chung (architecture §5.1–5.2, CONTRACTS §11.1, #49).

Handler gom path param + query + body JSON thành `Input` của command, chạy command trong threadpool, trả Output nguyên dạng.
Thêm endpoint = thêm một dòng vào `ROUTES`. Upload (multipart) và media (file) ở `upload.py`, `media.py`; ảnh frame và zip export trả file ngay trong handler chung.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response

from ..commands import COMMANDS, App, ServiceError

ROUTES = [
    ('GET', '/cloud/status', 'get_cloud_status'),
    ("POST", "/sessions/{session_id}/proposal-jobs", "queue_proposal"),
    ("GET", "/sessions/{session_id}/proposal-jobs", "list_proposal_jobs"),
    ("POST", "/sessions/{session_id}/proposals/{n}/yolo", "run_batch_yolo"),
    ("GET", "/sessions/{session_id}/proposals/{n}/yolo", "get_batch_yolo"),
    ("GET", "/models", "list_reference_models"),
    ("GET", "/models/{model_id}", "get_reference_model"),
    ("POST", "/models/{model_id}/verify", "verify_reference_model"),
    ("GET", "/datasets/import-folders", "list_import_folders"),
    ("POST", "/datasets/import", "import_dataset"),
    ("GET", "/datasets", "list_datasets"),
    ("GET", "/datasets/versions/{version_id}", "get_dataset_version"),
    ("POST", "/datasets/versions/{version_id}/prepare", "queue_prepare_dataset"),
    ("POST", "/datasets/versions/{version_id}/verify", "verify_dataset_version"),
    ("GET", "/datasets/versions/{version_id}/samples", "list_dataset_samples"),
    ("GET", "/datasets/versions/{version_id}/images/{key}", "get_image"),
    ("GET", "/videos", "list_videos"),
    ("GET", "/videos/{video_id}/frames/{idx}", "get_frame"),
    ("POST", "/videos/{video_id}/prepare", "queue_prepare"),                  # P1 nền (#56)
    ("POST", "/videos/{video_id}/prepare-clip", "queue_prepare_clip"),
    ("GET", "/sessions", "list_sessions"),
    ("POST", "/sessions", "create_session"),
    ("GET", "/sessions/{session_id}", "get_session"),
    ("POST", "/sessions/{session_id}/videos", "add_videos_to_session"),
    ("GET", "/sessions/{session_id}/units", "list_units"),
    ("GET", "/sessions/{session_id}/state", "get_state"),
    ("PATCH", "/sessions/{session_id}/state", "update_state"),
    ("POST", "/sessions/{session_id}/proposals", "propose"),
    ("GET", "/sessions/{session_id}/proposals", "list_proposals"),
    ("GET", "/sessions/{session_id}/proposals/{n}", "get_proposal"),
    ("GET", "/sessions/{session_id}/proposals/{n}/explain", "explain_proposal"),   # giải thích cấp đợt (#61)
    ("POST", "/sessions/{session_id}/evidence", "get_evidence"),             # bằng chứng hiển thị, chỉ đọc cache (#59)
    ("POST", "/sessions/{session_id}/snapshots", "create_snapshot"),           # snapshot bất biến (CONTRACTS §14.2)
    ("GET", "/sessions/{session_id}/snapshots", "list_snapshots"),
    ("GET", "/snapshots/{snapshot_id}", "get_snapshot"),
    ("GET", "/snapshots/{snapshot_id}/download", "export_snapshot"),
    ("POST", "/snapshots/{snapshot_id}/verify", "verify_snapshot"),
    ("POST", "/sessions/{session_id}/export", "export"),
    ("GET", "/exports", "list_exports"),
    ("GET", "/exports/{session_id}/{export_id}/download", "get_export_file"),   # trả file .zip (#54)
    ("GET", "/recipes", "list_recipes"),
    ("GET", "/components/{kind}/{name}", "describe_component"),
    ("GET", "/sessions/{session_id}/views/{name}", "get_view"),
    ("GET", "/sessions/{session_id}/metrics", "get_metrics"),
    ("POST", "/sessions/{session_id}/metrics", "get_metrics"),        # metric tự chọn cần body; không ghi gì
]
QUERY_AS_PARAMS = {"get_view"}   # query của view = `params` của view (khoá lặp → list), không phải trường của Input

IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}   # ảnh frame: video_id = hash nội dung → không đổi


class _TemporaryFileResponse(FileResponse):
    """Transport của ZIP snapshot: dọn cả khi gửi lỗi/client ngắt; boot quét orphan nếu crash."""

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


async def run_command(name: str, request: Request, app: App):
    """Gom input, validate bằng `Input` (sai → ValidationError → 422), chạy command ngoài event loop."""
    cmd = COMMANDS[name]
    if name in QUERY_AS_PARAMS:
        params: dict = {}
        for k, v in request.query_params.multi_items():
            params[k] = [*params[k], v] if isinstance(params.get(k), list) else [params[k], v] if k in params else v
        data = {"params": params, **request.path_params}
    else:
        data = {**request.query_params, **request.path_params}
    if request.method != "GET":
        raw = await request.body()
        if raw:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ServiceError("validation_error", f"body không phải JSON: {e}") from None
            data = {**body, **data} if isinstance(body, dict) else data
    return await run_in_threadpool(cmd.run, cmd.Input.model_validate(data), app)


def _endpoint(name: str, app: App):
    async def endpoint(request: Request):
        out = await run_command(name, request, app)
        if name == "get_frame":
            return Response(out.data, media_type=out.media_type, headers=IMMUTABLE)
        if name == "get_image":
            return Response(out.data, media_type=out.media_type, headers={"Cache-Control": "no-cache"})
        if name == "get_export_file":
            # local-only: ai gọi được API là tải được (LOCAL_SINGLE_USER_LIMITS #8)
            return FileResponse(out.path, media_type="application/zip", filename=out.filename)
        if name == "export_snapshot":
            return _TemporaryFileResponse(out.path, media_type="application/zip", filename=out.filename,
                                          headers={"Cache-Control": "no-store"})
        return JSONResponse(out.model_dump(mode="json"))
    return endpoint


def router(app: App) -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    for method, path, name in ROUTES:
        cmd = COMMANDS[name]
        r.add_api_route(path, _endpoint(name, app), methods=[method], name=f"{name}_{method.lower()}", summary=cmd.description,
                        response_model=None if name in ("get_frame", "get_image", "get_export_file", "export_snapshot") else cmd.Output)
    return r
