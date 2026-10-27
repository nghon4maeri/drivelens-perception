"""Session service: video, phiên, review_state, proposal, export. Đọc/ghi qua store/, gọi pipeline/.
Không có logic chọn frame.

`App` = cấu hình chạy chung (đường dẫn, giới hạn; đọc từ biến môi trường) + field store + DB; command nhận nó làm tham số `app`.
`ServiceError(code, message, status)` = lỗi nghiệp vụ; API đổi thành `{"error": {"code", "message"}}` (architecture §5.1).
"""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from ..errors import ServiceError  # noqa: F401  (re-export, giữ `from ..service import ServiceError`)
from ..pipeline.prepare import DEFAULT_SPEC
from ..store.db import DB
from ..store.field_store import FieldStore


@dataclass
class App:
    videos_dir: Path
    output_dir: Path
    max_upload_bytes: int
    auto_prepare: bool = False   # dataset import tự prepare; video chỉ chạy explicit (#76)
    prepare_jobs: dict = field(default_factory=dict)   # video_id → trạng thái job P1 nền (service/video.py)
    datasets_dir: Path = field(default_factory=lambda: Path("data/datasets"))
    max_import_images: int = 50_000
    dataset_prepare_jobs: dict = field(default_factory=dict)
    max_snapshot_zip_bytes: int = 256 << 20
    clip_prepare_jobs: dict = field(default_factory=dict)
    clip_job_lock: object = field(default_factory=threading.Lock)
    models_dir: Path = field(default_factory=lambda: Path("data/models"))
    max_model_bytes: int = 500 << 20
    model_upload_lock: object = field(default_factory=threading.Lock)
    batch_yolo_lock: object = field(default_factory=threading.Lock)
    proposal_lock: object = field(default_factory=threading.RLock)
    storage_backend: str = 'local'
    job_backend: str = 'local'
    runtime_mode: str = 'local'
    cloud_stop: object = field(default_factory=threading.Event)
    cloud_scheduled: set = field(default_factory=set)

    @classmethod
    def from_env(cls) -> App:
        return cls(Path(os.environ.get("RAV_VIDEOS_DIR", "data/videos")),
                   Path(os.environ.get("RAV_OUTPUT_DIR", "output")),
                   int(os.environ.get("RAV_MAX_UPLOAD_MB", "500")) << 20,
                   os.environ.get("RAV_AUTO_PREPARE", "1") != "0",
                   datasets_dir=Path(os.environ.get("RAV_DATASETS_DIR", "data/datasets")),
                   max_import_images=int(os.environ.get("RAV_MAX_IMPORT_IMAGES", "50000")),
                   max_snapshot_zip_bytes=int(os.environ.get("RAV_MAX_SNAPSHOT_ZIP_MB", "256")) << 20,
                   models_dir=Path(os.environ.get("RAV_MODELS_DIR", "data/models")),
                   max_model_bytes=int(os.environ.get("RAV_MAX_MODEL_MB", "500")) << 20,
                   storage_backend=os.environ.get('RAV_STORAGE_BACKEND','local'),
                   job_backend=os.environ.get('RAV_JOB_BACKEND','local'),
                   runtime_mode=os.environ.get('RAV_RUNTIME_MODE', 'local'))

    @cached_property
    def cloud_storage(self):
        from ..store.s3 import S3Store
        if self.storage_backend == 'local':
            return None
        if self.storage_backend != 's3':
            raise ValueError('RAV_STORAGE_BACKEND phải là local hoặc s3.')
        return S3Store.from_repo()

    @cached_property
    def embedding_jobs(self):
        from ..store.embedding_jobs import LocalJobs, SupabaseJobs
        if self.job_backend == 'local':
            return LocalJobs(self.output_dir / 'embedding-jobs')
        if self.job_backend == 'supabase':
            return SupabaseJobs.from_repo()
        raise ValueError('RAV_JOB_BACKEND phải là local hoặc supabase.')

    @property
    def spec(self) -> dict:
        """Spec P1 mặc định (MVP: một cách tách frame); phiên chép nó vào session spec lúc tạo (CONTRACTS §7)."""
        return {**DEFAULT_SPEC, "source": {"type": "local_folder", "params": {"root": str(self.videos_dir)}}}

    @cached_property
    def prepare_queue(self) -> queue.Queue:
        """Hàng đợi việc P1 nền: một thread daemon chạy lần lượt (tắt server không phải chờ P1 xong)."""
        # local-only: P1 nền chạy trong tiến trình API, một worker, trạng thái trong RAM (LOCAL_SINGLE_USER_LIMITS #15)
        q: queue.Queue = queue.Queue()

        def worker() -> None:
            while True:
                q.get()()

        threading.Thread(target=worker, name="p1-worker", daemon=True).start()
        return q

    @cached_property
    def fields(self) -> FieldStore:
        return FieldStore(self.output_dir / "fields")

    @cached_property
    def db(self) -> DB:
        return DB(self.output_dir / "rav.sqlite")


def publication_guard(fn):
    """Serialize state/pool/proposal publication in the single local App (§18)."""
    from functools import wraps

    @wraps(fn)
    def guarded(app, *args, **kwargs):
        # local-only: one API/output; shared RLock keeps proposal snapshot checks atomic with review.
        with app.proposal_lock:
            return fn(app, *args, **kwargs)
    return guarded
