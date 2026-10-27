"""`POST /api/v1/videos`: nhận multipart rồi gọi command `upload_video` (giới hạn dung lượng, `video_id` = hash nội dung,
mở thử bằng decoder — architecture §5.1).

File ghi ra `output/tmp/` theo khúc 1 MB, vượt `max_upload_bytes` thì dừng ngay (không đợi nhận hết mới kiểm, #49); luôn xoá file tạm.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..commands import COMMANDS, App, ServiceError

CHUNK = 1 << 20


def router(app: App) -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    cmd = COMMANDS["upload_video"]

    @r.post("/models", name="upload_reference_model", response_model=COMMANDS["upload_reference_model"].Output)
    async def upload_model(file: UploadFile, name: str | None = Form(None)):
        tmp = app.output_dir / "tmp" / f"model-upload-{uuid.uuid4().hex}.pt"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = 0
            with tmp.open("wb") as dst:
                while chunk := await file.read(CHUNK):
                    size += len(chunk)
                    if size > app.max_model_bytes:
                        raise ServiceError("model_too_large", "Checkpoint vượt giới hạn RAV_MAX_MODEL_MB.", 413)
                    await run_in_threadpool(dst.write, chunk)
            model_cmd = COMMANDS["upload_reference_model"]
            inp = model_cmd.Input(filename=file.filename or "checkpoint.pt", path=str(tmp), name=name)
            out = await run_in_threadpool(model_cmd.run, inp, app)
            return JSONResponse(out.model_dump(mode="json"))
        finally:
            tmp.unlink(missing_ok=True)
            await file.close()

    @r.post("/videos", name="upload_video", summary=cmd.description, response_model=cmd.Output)
    async def upload_video(file: UploadFile):
        tmp = app.output_dir / "tmp" / f"upload-{uuid.uuid4().hex}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = 0
            with tmp.open("wb") as f:
                while chunk := await file.read(CHUNK):
                    size += len(chunk)
                    if size > app.max_upload_bytes:
                        raise ServiceError("file_too_large", f"file vượt giới hạn {app.max_upload_bytes >> 20} MB", 400)
                    f.write(chunk)
            inp = cmd.Input(filename=file.filename or "video.mp4", path=str(tmp))
            return JSONResponse((await run_in_threadpool(cmd.run, inp, app)).model_dump(mode="json"))
        finally:
            tmp.unlink(missing_ok=True)

    return r
