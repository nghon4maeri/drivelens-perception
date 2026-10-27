"""Commands dataset ảnh: import, liệt kê, phiên bản, mẫu và JPEG (CONTRACTS §14.4).

Pydantic in/out dùng chung cho REST và CLI import-dataset; service giữ logic, S5 bổ sung prepare/verify.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from ..service import App
from ..service import dataset as svc
from .base import Command, command
from .videos import Empty, FrameOut, PrepareStatus

LabelStatus = Literal["none", "labeled", "empty", "missing", "invalid", "conflict"]


class DatasetVersionDTO(BaseModel):
    id: str
    dataset_id: str
    n: int
    created_at: str
    folder: str
    annotation_format: Literal["none", "yolo"]
    classes: list[str] | None
    n_images: int
    label_counts: dict[str, int]
    class_counts: dict[str, int]
    prepared: bool
    prepare: PrepareStatus | None = None
    n_sessions_as_reference: int


class DatasetDTO(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: str
    versions: list[DatasetVersionDTO]


class DatasetList(RootModel[list[DatasetDTO]]):
    pass


class ImportFolderDTO(BaseModel):
    folder: str
    n_images_hint: int
    detected: Literal["none", "yolo"]
    classes: list[str] | None


class ImportFolders(RootModel[list[ImportFolderDTO]]):
    pass


class ImportDatasetIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    folder: str = Field(min_length=1)
    name: str | None = Field(None, min_length=1)
    dataset_id: str | None = Field(None, min_length=1)
    annotation_format: Literal["auto", "none", "yolo"] = "auto"
    description: str | None = None

    @model_validator(mode="after")
    def one_identity(self) -> Self:
        if (self.name is None) == (self.dataset_id is None):
            raise ValueError("cần đúng một trong name hoặc dataset_id")
        return self


class ImportDatasetOut(BaseModel):
    dataset: DatasetDTO
    version: DatasetVersionDTO
    created: bool
    report: dict


class VersionIdIn(BaseModel):
    version_id: str


class DatasetVersionDetailDTO(DatasetVersionDTO):
    report: dict


class SamplesIn(VersionIdIn):
    offset: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=500)
    label_status: LabelStatus | None = None


class SampleDTO(BaseModel):
    sample_id: str
    key: str
    relpath: str
    width: int
    height: int
    label_status: LabelStatus
    label_counts: dict[str, int] | None
    thumb_url: str


class SamplesOut(BaseModel):
    total: int
    items: list[SampleDTO]


class ImageIn(VersionIdIn):
    key: str
    max_side: int | None = Field(None, gt=0)


@command
class ListImportFolders(Command):
    name = "list_import_folders"
    description = "Thư mục cấp 1–2 dưới gốc dataset, số ảnh gợi ý và nhận diện nhãn trước khi import."
    Input, Output = Empty, ImportFolders

    def run(self, inp: Empty, app: App) -> ImportFolders:
        return ImportFolders(svc.list_import_folders(app))


@command
class ImportDataset(Command):
    name = "import_dataset"
    description = "Import ảnh/YOLO thành dataset version bất biến, dedup và báo nhãn xung đột; chỉ lưu tham chiếu raw."
    Input, Output = ImportDatasetIn, ImportDatasetOut
    mutates = needs_confirm = True

    def run(self, inp: ImportDatasetIn, app: App) -> ImportDatasetOut:
        return ImportDatasetOut(**svc.import_dataset(app, **inp.model_dump()))


@command
class ListDatasets(Command):
    name = "list_datasets"
    description = "Dataset và các version, mới nhất trước; ảnh dataset chỉ làm tham chiếu."
    Input, Output = Empty, DatasetList

    def run(self, inp: Empty, app: App) -> DatasetList:
        return DatasetList(svc.list_datasets(app))


@command
class GetDatasetVersion(Command):
    name = "get_dataset_version"
    description = "Thông tin một version dataset và báo cáo import đã đóng băng."
    Input, Output = VersionIdIn, DatasetVersionDetailDTO

    def run(self, inp: VersionIdIn, app: App) -> DatasetVersionDetailDTO:
        row = svc.get_version(app, inp.version_id)
        return DatasetVersionDetailDTO(**svc.version_dto(app, row), report=row["import_report"])


@command
class ListDatasetSamples(Command):
    name = "list_dataset_samples"
    description = "Mẫu của dataset version theo key, phân trang và lọc trạng thái nhãn."
    Input, Output = SamplesIn, SamplesOut

    def run(self, inp: SamplesIn, app: App) -> SamplesOut:
        return SamplesOut(**svc.list_samples(app, **inp.model_dump()))


@command
class GetImage(Command):
    name = "get_image"
    description = "Đọc ảnh raw của version, kiểm hash, EXIF transpose và trả JPEG hoặc thumbnail."
    Input, Output = ImageIn, FrameOut

    def run(self, inp: ImageIn, app: App) -> FrameOut:
        return FrameOut(data=svc.get_image(app, **inp.model_dump()))


class VerifyOut(BaseModel):
    ok: bool
    missing: list[str]
    changed: list[str]


@command
class VerifyDatasetVersion(Command):
    name = "verify_dataset_version"
    description = "Kiểm nguồn ảnh và nhãn so với manifest, không sửa version bất biến."
    Input, Output = VersionIdIn, VerifyOut

    def run(self, inp: VersionIdIn, app: App) -> VerifyOut:
        return VerifyOut(**svc.verify(app, inp.version_id))


@command
class QueuePrepareDataset(Command):
    name = "queue_prepare_dataset"
    description = "Xếp chuẩn bị feature dataset vào hàng đợi P1, xem tiến độ qua DatasetVersionDTO.prepare."
    Input, Output = VersionIdIn, DatasetVersionDTO
    mutates = True

    def run(self, inp: VersionIdIn, app: App) -> DatasetVersionDTO:
        svc.queue_prepare(app, inp.version_id)
        return DatasetVersionDTO(**svc.version_dto(app, svc.get_version(app, inp.version_id)))
