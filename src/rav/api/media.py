"""`GET /media/videos/{video_id}`: file video để tua (Range, qua `FileResponse`); ảnh frame / thumbnail đi qua command `get_frame` (#37).

URL theo `video_id` (hash nội dung), không lộ tên file / thư mục; command `get_video_file` tra ra file (#49).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from ..commands import COMMANDS, App


def router(app: App) -> APIRouter:
    r = APIRouter(prefix="/media")
    cmd = COMMANDS["get_video_file"]

    # local-only: media không kiểm soát truy cập (LOCAL_SINGLE_USER_LIMITS #7)
    @r.get("/videos/{video_id}", name="get_video_file", summary=cmd.description)
    async def video_file(video_id: str):
        out = await run_in_threadpool(cmd.run, cmd.Input(video_id=video_id), app)
        return FileResponse(out.path, media_type=out.media_type)

    return r
