"""Reference checkpoint commands: upload/list/get/verify, opaque storage only (CONTRACTS §16)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from ..service import App
from ..service import reference_model as svc
from .base import Command, command
from .videos import Empty


class ModelDTO(BaseModel):
    id: str
    name: str
    filename: str
    sha256: str
    size_bytes: int
    format: Literal["pytorch_zip"]
    created_at: str
    available: bool
    validation: Literal["container_only"] = "container_only"
    inference_ready: Literal[False] = False


class UploadModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    filename: str = Field(min_length=1, max_length=255)
    path: str
    name: str | None = Field(None, min_length=1, max_length=200)


class UploadModelOut(BaseModel):
    model: ModelDTO
    created: bool


class ModelIdIn(BaseModel):
    model_id: str


class ModelList(RootModel[list[ModelDTO]]):
    pass


@command
class UploadReferenceModel(Command):
    name = "upload_reference_model"
    description = "Lưu checkpoint .pt theo SHA256; chỉ kiểm container, chưa nạp/chạy YOLO."
    Input, Output = UploadModelIn, UploadModelOut
    mutates = needs_confirm = True

    def run(self, inp: UploadModelIn, app: App) -> UploadModelOut:
        return UploadModelOut(**svc.upload_model(app, **inp.model_dump()))


@command
class ListReferenceModels(Command):
    name = "list_reference_models"
    description = "Các checkpoint tham chiếu đã lưu, chưa chạy inference."
    Input, Output = Empty, ModelList

    def run(self, inp: Empty, app: App) -> ModelList:
        return ModelList([svc.dto(app, row) for row in app.db.list_models()])


@command
class GetReferenceModel(Command):
    name = "get_reference_model"
    description = "Metadata checkpoint tham chiếu; không nạp model."
    Input, Output = ModelIdIn, ModelDTO

    def run(self, inp: ModelIdIn, app: App) -> ModelDTO:
        return ModelDTO(**svc.dto(app, svc.get_model(app, inp.model_id)))


class VerifyModelOut(BaseModel):
    ok: Literal[True] = True


@command
class VerifyReferenceModel(Command):
    name = "verify_reference_model"
    description = "Kiểm SHA256 đầy đủ của checkpoint đã lưu, không chạy model."
    Input, Output = ModelIdIn, VerifyModelOut

    def run(self, inp: ModelIdIn, app: App) -> VerifyModelOut:
        svc.verify_model(app, inp.model_id)
        return VerifyModelOut()
