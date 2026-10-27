"""Explicit YOLO inference on proposal picks, and read-only cached results (CONTRACTS §17)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel

from ..service import App
from ..service import batch_yolo as svc
from .base import Command, command
from .evidence import Detection


class BatchYoloIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    n: int = Field(gt=0)
    model_id: str = Field(min_length=1)


class YoloItem(BaseModel):
    unit_id: str
    detections: list[Detection]


class YoloBatchDTO(BaseModel):
    session_id: str
    proposal_n: int
    model_id: str
    model_name: str
    model_sha256: str
    engine_version: str
    params: dict
    created_at: str
    total_images: int
    items: list[YoloItem]
    cached: bool


class OptionalYoloBatch(RootModel[YoloBatchDTO | None]):
    pass


@command
class RunBatchYolo(Command):
    name = "run_batch_yolo"
    description = "Chạy YOLO detection chỉ trên picks của một đợt, không infer toàn dataset hoặc sửa proposal."
    Input, Output = BatchYoloIn, YoloBatchDTO
    mutates = True

    def run(self, inp: BatchYoloIn, app: App) -> YoloBatchDTO:
        return YoloBatchDTO(**svc.run(app, inp.session_id, inp.n, inp.model_id))


@command
class GetBatchYolo(Command):
    name = "get_batch_yolo"
    description = "Đọc kết quả YOLO đã lưu của đúng đợt/model, không chạy inference."
    Input, Output = BatchYoloIn, OptionalYoloBatch

    def run(self, inp: BatchYoloIn, app: App) -> OptionalYoloBatch:
        return OptionalYoloBatch(svc.get_result(app, inp.session_id, inp.n, inp.model_id))
