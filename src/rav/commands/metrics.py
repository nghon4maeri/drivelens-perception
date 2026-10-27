"""`get_metrics(session_id, metrics?, on?)`: tiến độ + metric chọn qua config (architecture §5.3 MetricsDTO; #43, #50)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..core.types import ComponentConfig
from ..service import App
from ..service import metrics as svc
from .base import Command, command
from .sessions import SessionIdIn


class GetMetricsIn(SessionIdIn):
    metrics: list[ComponentConfig] | None = Field(None, description="[{type, params}]; bỏ trống = nd_rate_at_k + keep_rate_by_recipe")
    on: Literal["kept"] | int = Field("kept", description='"kept" = tập đang keep; số n = picks của proposal n')


class Progress(BaseModel):
    target_k: int
    kept: int
    dropped: int
    proposals: int


class MetricResult(BaseModel):
    type: str
    params: dict
    values: dict[str, float]      # dict phẳng #42: <tên>, by_video/<id>, recipe/<tên>, independent


class MetricsDTO(BaseModel):
    progress: Progress
    on: Literal["kept"] | int
    results: list[MetricResult]


@command
class GetMetrics(Command):
    name = "get_metrics"
    description = ("Chỉ số phiên: tiến độ kept / target_k và các metric chọn qua config (mặc định ND-Rate@K pHash trên tập keep + keep rate "
                   "theo recipe); mỗi kết quả có cờ independent (metric không dùng chung field với recipe).")
    Input, Output = GetMetricsIn, MetricsDTO

    def run(self, inp: GetMetricsIn, app: App) -> MetricsDTO:
        return MetricsDTO.model_validate(svc.get_metrics(app, inp.session_id, inp.metrics, inp.on))
