"""Backend-only cloud connectivity status: safe flags and health, no credentials or signed URLs."""
from __future__ import annotations

from pydantic import BaseModel

from ..store.embedding_jobs import JobStoreError
from ..store.s3 import CloudStorageError
from .base import Command, command


class Empty(BaseModel):
    pass


class CloudStatus(BaseModel):
    storage_backend: str
    job_backend: str
    s3_ready: bool
    jobs_ready: bool
    error: str | None = None


@command
class GetCloudStatus(Command):
    name = 'get_cloud_status'
    description = 'Kiểm kết nối storage/job database ở backend; không trả credential.'
    Input, Output = Empty, CloudStatus

    def run(self, inp, app):
        out = CloudStatus(storage_backend=app.storage_backend,job_backend=app.job_backend,s3_ready=False,jobs_ready=False)
        try:
            if app.storage_backend == 's3':
                app.cloud_storage.client.list_objects_v2(Bucket=app.cloud_storage.bucket,MaxKeys=1)
                out.s3_ready = True
                app.embedding_jobs.list(active=True)
                out.jobs_ready = True
        except (CloudStorageError,JobStoreError) as exc:
            out.error = str(exc)
        except Exception:
            out.error = 'Cloud chưa kết nối; kiểm cấu hình/quyền/kết nối.'
        return out
