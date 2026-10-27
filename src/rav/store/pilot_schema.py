"""Schema PostgreSQL của pilot: áp migration (chỉ operator) và kiểm version khi API/worker chạy (CONTRACTS §21.11).

- `apply_migrations(url)`: chạy lần lượt `migrations/001_*.sql` → `004_*.sql` trong một transaction mỗi file, giữ advisory lock,
  ghi `rav_pilot_schema_migration(version, checksum)`. File đã áp cùng checksum thì bỏ qua; checksum khác → lỗi (không sửa ngầm).
- `schema_problem(cur)`: None nếu DB có đủ migration cùng checksum, ngược lại trả mô tả để API/worker trả 503 `schema_not_ready`.
Chạy: `python scripts/pilot_migrate.py --database-url postgresql://...` (operator, không chạy khi API boot).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATIONS = ("001_pilot_identity.sql", "002_pilot_runtime.sql", "003_pilot_storage.sql", "004_pilot_worker_objects.sql",
              "005_pilot_features.sql", "006_pilot_curation.sql",
              "007_pilot_analysis.sql", "009_pilot_operations.sql", "010_pilot_frames.sql", "011_pilot_propose_publish.sql",
              "012_pilot_claim.sql", "013_pilot_cpu_optional.sql", "014_pilot_checkpoint.sql", "015_pilot_multipart.sql",
              "016_pilot_progress.sql", "017_pilot_export.sql", "018_pilot_session_reference_grant.sql",
              "019_pilot_storage_optional.sql", "020_pilot_vps_upload.sql", "021_pilot_model_verify.sql",
              "022_pii_cache_index.sql", "023_pilot_signup_bootstrap.sql", "024_pilot_sample_catalog.sql",
              "025_pilot_system_ingest.sql", "026_pilot_retire_legacy_signup_trigger.sql", "027_pilot_sample_reference.sql")
LOCK_KEY = 0x52415632   # "RAV2": advisory lock cho migration


def migration_checksums() -> dict[str, str]:
    """Băm nội dung đã chuẩn hóa xuống dòng: checkout Windows (CRLF) và container Linux (LF) cho cùng checksum."""
    return {name: hashlib.sha256((MIGRATIONS_DIR / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in MIGRATIONS}


def apply_migrations(url: str) -> list[str]:
    """Áp các migration còn thiếu; trả tên các file vừa áp. Checksum lệch với bản đã áp → RuntimeError."""
    import psycopg

    applied_now = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            for name, checksum in migration_checksums().items():
                exists = conn.execute("SELECT to_regclass('public.rav_pilot_schema_migration') IS NOT NULL").fetchone()[0]
                row = conn.execute("SELECT checksum FROM rav_pilot_schema_migration WHERE version = %s",
                                   (name,)).fetchone() if exists else None
                if row:
                    if row[0] != checksum:
                        raise RuntimeError(f"{name} đã áp với checksum khác ({row[0][:12]} != {checksum[:12]}); "
                                           "không sửa migration đã áp, tạo migration mới")
                    continue
                with conn.transaction():
                    conn.execute((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO rav_pilot_schema_migration (version, checksum) VALUES (%s, %s)", (name, checksum))
                applied_now.append(name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return applied_now


def schema_problem(cur) -> str | None:
    """Kiểm DB đã có đủ migration đúng checksum (đọc bằng cursor của caller)."""
    cur.execute("SELECT to_regclass('public.rav_pilot_schema_migration') IS NOT NULL AS ok")
    if not _first(cur.fetchone()):
        return "chưa có bảng rav_pilot_schema_migration"
    cur.execute("SELECT version, checksum FROM rav_pilot_schema_migration")
    applied = {r["version"] if isinstance(r, dict) else r[0]: r["checksum"] if isinstance(r, dict) else r[1]
               for r in cur.fetchall()}
    for name, checksum in migration_checksums().items():
        if name not in applied:
            if name in ("011_pilot_propose_publish.sql", "012_pilot_claim.sql", "013_pilot_cpu_optional.sql", "014_pilot_checkpoint.sql", "015_pilot_multipart.sql", "016_pilot_progress.sql", "017_pilot_export.sql"):   # staged additive
                continue
            return f"thiếu migration {name}"
        if applied[name] != checksum:
            return f"{name} lệch checksum"
    return None


def _first(row):
    return next(iter(row.values())) if isinstance(row, dict) else row[0]
