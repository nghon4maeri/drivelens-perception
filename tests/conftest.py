"""Fixture dùng chung: PostgreSQL thật cho test pilot (CONTRACTS §21.11).

`RAV_PILOT_TEST_DATABASE_URL` = URL superuser tới một server Postgres test (vd cluster portable `data/pg-test`,
`postgresql://postgres@127.0.0.1:54329/postgres`; CI dùng service postgres). Thiếu → test pilot skip kèm lý do.
Mỗi phiên pytest tạo database riêng, áp migration bằng `apply_migrations`, bật LOGIN cho `rav_pilot_api`/`rav_pilot_worker`
(chỉ cluster test), xóa database khi xong. Mỗi test bắt đầu với bảng rỗng (TRUNCATE không chạy trigger bất biến theo hàng).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest

PG_ENV = "RAV_PILOT_TEST_DATABASE_URL"
ROLE_PASSWORD = "rav-pilot-test"   # chỉ cluster test cục bộ/CI

POLICY_CONFIG = {   # số giả cho test — không phải số đo DEP1
    "cpu_base_ms": 10_000, "gpu_base_ms": 5_000, "pending_per_user": 5, "heavy_active_per_user": 2,
    "storage_user_bytes": 1 << 30, "storage_global_bytes": 4 << 30, "max_video_bytes": 500 << 20, "max_duration_ms": 900_000,
    "max_width": 3840, "max_height": 2160, "max_decoded_pixels": 10**10, "max_zip_bytes": 256 << 20, "max_media_requests": 60,
    "lease_ms": 30_000, "heartbeat_ms": 10_000, "queue_deadline_ms": 3_600_000, "stop_deadline_ms": 60_000,
    "cleanup_deadline_ms": 60_000, "user_get_ttl_ms": 300_000, "upload_ttl_ms": 900_000, "remote_url_ttl_ms": 900_000,
    "staging_grace_ms": 3_600_000, "gpu_snapshot_fresh_ms": 600_000, "cost_snapshot_fresh_ms": 3_600_000,
    "gpu_headroom_ms": 60_000, "worker_rss_bytes": 4 << 30, "worker_temp_bytes": 8 << 30, "forecast_admission_usd": 2.0,
    "slot_inventory": {"CPU": ["cpu-1"]}, "run_deadline_ms_by_kind": {"PROPOSE": 600_000},
    "chunk_bounds_by_kind": {"PROPOSE": {"max_units": 4000},
                             "VALIDATE_UPLOAD": {"frame_batch_size": 8, "frame_put_concurrency": 4, "max_frame_bytes": 256 << 20},
                             "PREPARE_FRAMES": {"frame_batch_size": 8, "frame_put_concurrency": 4, "max_frame_bytes": 256 << 20}},
    "profiles_by_kind": {k: {"cpu_ms": 1_000, "gpu_ms": 2_000 if k in ("PREPARE_DINO", "PREPARE_CLIP") else 0}
                         for k in ("VALIDATE_UPLOAD", "RENDER_MEDIA", "PREPARE_DINO", "PREPARE_CLIP", "PROPOSE", "YOLO_BATCH",
                                   "METRICS", "EXPORT_BUILD", "EXPORT_FINALIZE", "DELETE_SOURCE", "PREPARE_FRAMES",
                                   "MODEL_VERIFY")},
}


def _swap(url: str, *, db: str | None = None, user: str | None = None, password: str | None = None) -> str:
    p = urlsplit(url)
    host = p.hostname + (f":{p.port}" if p.port else "")
    if user:
        netloc = f"{user}:{password}@{host}" if password else f"{user}@{host}"
    else:
        netloc = p.netloc
    return urlunsplit((p.scheme, netloc, f"/{db}" if db else p.path, p.query, p.fragment))


@dataclass
class PilotPG:
    admin_url: str
    api_url: str
    worker_url: str

    def sql(self, query: str, params=()) -> list[dict]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.admin_url, autocommit=True, row_factory=dict_row) as c:
            cur = c.execute(query, params or None)
            return cur.fetchall() if cur.description else []

    # ---- seed (operator) ----
    def policy(self, config: dict | None = None, enabled: bool = True, version: str | None = None,
               signup_default: bool = True) -> UUID:
        pid = uuid4()
        use_default = bool(signup_default and enabled)
        if use_default:
            self.sql("UPDATE rav_pilot_quota_policy SET signup_default = false WHERE signup_default = true")
        self.sql("INSERT INTO rav_pilot_quota_policy (id, version, config, enabled, signup_default) VALUES (%s, %s, %s, %s, %s)",
                 (pid, version or f"test-{pid.hex[:8]}", json.dumps(POLICY_CONFIG if config is None else config), enabled, use_default))
        return pid

    def profile(self, user_id: UUID | None = None, policy_id: UUID | None = None, active: bool = True) -> UUID:
        uid = user_id or uuid4()
        self.sql("INSERT INTO rav_pilot_profile (user_id, active, display_name, policy_id) VALUES (%s, %s, %s, %s)",
                 (uid, active, f"user-{uid.hex[:6]}", policy_id))
        return uid

    def source(self, owner: UUID | None, core: str, *, kind: str = "VIDEO", availability: str = "READY",
               n_units: int | None = 10, name: str | None = None, description: str = "", published: bool = True) -> UUID:
        sid = uuid4()
        scope = "PRIVATE" if owner else "SYSTEM"
        meta = {"n_units": n_units, "fps": 4, "n_frames": n_units,
                "duration_ms": n_units * 250} if n_units is not None else {}
        self.sql("INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, name, description, "
                 "published, raw_key, raw_sha256, size_bytes, metadata, availability) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                 (sid, core, kind, scope, owner, owner is None, name or f"{core}.mp4", description, published,
                  f"private/{sid}/raw", "a" * 64, 1234, json.dumps(meta), availability))
        return sid

    def slot(self, key: str, kind: str = "CPU", account: str = "test-host") -> None:
        self.sql("INSERT INTO rav_pilot_resource_slot (slot_key, kind, account_or_host, state) VALUES (%s, %s, %s, 'FREE')",
                 (key, kind, account))

    def bonus(self, owner: UUID, operator: UUID, resource: str, ms: int) -> UUID:
        bid = uuid4()
        self.sql("INSERT INTO rav_pilot_quota_bonus (id, owner_id, resource, granted_ms, operator_id, reason, adjustment_key) "
                 "VALUES (%s, %s, %s, %s, %s, 'test', %s)", (bid, owner, resource, ms, operator, f"adj-{bid}"))
        return bid


@pytest.fixture(scope="session")
def pilot_pg_session():
    base = os.environ.get(PG_ENV)
    if not base:
        pytest.skip(f"cần {PG_ENV} trỏ tới PostgreSQL thật để chạy test pilot (CONTRACTS §21.11)")
    import psycopg

    from src.rav.store.pilot_schema import apply_migrations

    dbname = f"rav_pilot_t_{uuid4().hex[:10]}"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{dbname}"')
    admin = _swap(base, db=dbname)
    try:
        apply_migrations(admin)
        with psycopg.connect(admin, autocommit=True) as c:
            for role in ("rav_pilot_api", "rav_pilot_worker"):
                c.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{ROLE_PASSWORD}'")
        yield PilotPG(admin, _swap(admin, user="rav_pilot_api", password=ROLE_PASSWORD),
                      _swap(admin, user="rav_pilot_worker", password=ROLE_PASSWORD))
    finally:
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.fixture
def pg(pilot_pg_session: PilotPG) -> PilotPG:
    tables = [r["tablename"] for r in pilot_pg_session.sql(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'rav\\_pilot\\_%' "
        "AND tablename <> 'rav_pilot_schema_migration'")]
    pilot_pg_session.sql("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY")
    return pilot_pg_session


# ------------------------------------------------------------------------------
# DEP4: S3 giả (moto) và video nhỏ thật
# ------------------------------------------------------------------------------
S3_BUCKET, S3_PREFIX = "rav-pilot-test-bucket", "pilot-test"


@pytest.fixture
def s3_storage():
    """PilotStorage trên moto: bucket riêng, prefix pilot; không chạm S3 thật."""
    import boto3
    from moto import mock_aws

    from src.rav.store.pilot_storage import PilotStorage

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
        client.create_bucket(Bucket=S3_BUCKET)
        yield PilotStorage(client, S3_BUCKET, S3_PREFIX)


def make_video(path, seconds=3, fps=10, shift=0, size=(64, 48)):
    """Video nhỏ mpeg4, mỗi frame một màu (đọc lại kiểm được đúng frame)."""
    import av
    import numpy as np

    w, h = size
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
        for i in range(seconds * fps):
            img = np.zeros((h, w, 3), np.uint8)
            img[:, :, 0] = (i * 8 + shift) % 256
            img[:, : (i % w) + 1, 1] = 200
            out.mux(stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")))
        out.mux(stream.encode())
    return path
