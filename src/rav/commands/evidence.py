"""`get_evidence(session_id, unit_ids)`: bằng chứng hiển thị của các unit — detector / nhãn cảnh / telemetry, chỉ đọc cache
(CONTRACTS §8.0, §11.2; architecture §5.3 EvidenceDTO; #59)."""

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel

from ..service import App
from ..service import evidence as svc
from .base import Command, command
from .sessions import SessionIdIn


class Detection(BaseModel):
    label: str
    score: float
    box: list[float] = Field(description="[x1, y1, x2, y2] chuẩn hoá 0–1 theo ảnh")
    source: str = Field(description="field det.<model> sinh ra khung này")


class TelemetrySample(BaseModel):
    t: float
    speed_kmh: float | None = None
    brake: bool | None = None
    steer_deg: float | None = None
    lat: float | None = None
    lon: float | None = None


class EvidenceDTO(BaseModel):
    unit_id: str
    detections: list[Detection] | None   # None = chưa có field det.* trong cache
    tags: dict[str, str] | None          # None = chưa có field tag.*
    telemetry: list[TelemetrySample] | None   # None = nguồn không có telemetry; mẫu trong t0 ± 3 s


class EvidenceIn(SessionIdIn):
    unit_ids: list[str] = Field(min_length=1, max_length=256)


class EvidenceList(RootModel[list[EvidenceDTO]]):
    pass


@command
class GetEvidence(Command):
    name = "get_evidence"
    description = "Bằng chứng hiển thị của các unit (khung detector, nhãn cảnh, telemetry quanh t0); chỉ đọc cache, nguồn chưa có trả null."
    Input, Output = EvidenceIn, EvidenceList

    def run(self, inp: EvidenceIn, app: App) -> EvidenceList:
        return EvidenceList([EvidenceDTO(**e) for e in svc.get_evidence(app, inp.session_id, inp.unit_ids)])
