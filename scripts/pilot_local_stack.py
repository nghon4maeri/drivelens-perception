"""Stack pilot giả lập trên máy để smoke/dev (CONTRACTS §21.16, #96) — KHÔNG dùng cho môi trường thật.

`setup`: tạo database smoke trên Postgres portable, áp migration, bật LOGIN cho role API/worker (mật khẩu local), seed policy + 3 tài khoản
A/B/C + slot CPU/MEDIA, tạo bucket trên `moto server` (S3 giả) kèm CORS, import phiên mẫu + đặc trưng SYSTEM từ dữ liệu local,
import model YOLO SYSTEM nếu có `data/models/yolo11n.pt` (#97).
`token <A|B|C>`: in access token HS256 (12 giờ) để đăng nhập tab Access Token của `vite dev`. `env`: in biến môi trường cho API/worker/FE.
Chạy (PowerShell, đã `. .\\scripts\\use_project_storage.ps1`):
  python -m moto.server -H 127.0.0.1 -p 5005          # cửa sổ riêng
  python scripts/pilot_local_stack.py setup
  python scripts/pilot_local_stack.py env              # copy env cho uvicorn / worker / vite
  python scripts/pilot_local_stack.py token A
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# local-only: secret/mật khẩu/endpoint dưới đây chỉ cho stack giả lập trên máy dev (LOCAL_SINGLE_USER_LIMITS).
ADMIN = os.environ.get("RAV_SMOKE_ADMIN_URL", "postgresql://postgres@127.0.0.1:54329/postgres")
DB = "rav_pilot_smoke"
ROLE_PASSWORD = "rav-pilot-smoke"
JWT_SECRET = "rav-local-smoke-hs256-secret-not-for-production"
ISSUER_BASE = "http://127.0.0.1:54321"
S3 = "http://127.0.0.1:5005"
BUCKET, PREFIX = "rav-pilot-smoke", "pilot-smoke"
USERS = {name: str(uuid5(NAMESPACE_URL, f"rav-smoke:{name}")) for name in ("A", "B", "C")}

POLICY = {
    "cpu_base_ms": 900_000, "gpu_base_ms": 3_600_000, "pending_per_user": 5, "heavy_active_per_user": 2,
    "storage_user_bytes": 2 << 30, "storage_global_bytes": 8 << 30, "max_video_bytes": 500 << 20, "max_duration_ms": 900_000,
    "max_width": 3840, "max_height": 2160, "max_decoded_pixels": 10**11, "max_zip_bytes": 256 << 20, "max_media_requests": 40,
    "lease_ms": 60_000, "heartbeat_ms": 10_000, "queue_deadline_ms": 3_600_000, "stop_deadline_ms": 30_000, "cleanup_deadline_ms": 60_000,
    "user_get_ttl_ms": 300_000, "upload_ttl_ms": 900_000, "remote_url_ttl_ms": 3_600_000, "staging_grace_ms": 600_000,
    "gpu_snapshot_fresh_ms": 600_000, "cost_snapshot_fresh_ms": 3_600_000, "gpu_headroom_ms": 600_000,
    "worker_rss_bytes": 6 << 30, "worker_temp_bytes": 8 << 30, "forecast_admission_usd": 1.0,
    "slot_inventory": {"CPU": ["cpu-1"], "MEDIA": ["media-1"]},
    "run_deadline_ms_by_kind": {"PROPOSE": 600_000, "RENDER_MEDIA": 180_000, "VALIDATE_UPLOAD": 600_000},
    "chunk_bounds_by_kind": {"PROPOSE": {"max_units": 20_000},
                             "VALIDATE_UPLOAD": {"frame_batch_size": 64, "frame_put_concurrency": 4, "max_frame_bytes": 2 << 30},
                             "PREPARE_FRAMES": {"frame_batch_size": 64, "frame_put_concurrency": 4, "max_frame_bytes": 2 << 30}},
    "profiles_by_kind": {
        "VALIDATE_UPLOAD": {"cpu_ms": 20_000, "gpu_ms": 0}, "PREPARE_FRAMES": {"cpu_ms": 20_000, "gpu_ms": 0}, "RENDER_MEDIA": {"cpu_ms": 3_000, "gpu_ms": 0, "output_bytes": 4 << 20},
        "PREPARE_DINO": {"cpu_ms": 5_000, "gpu_ms": 600_000, "output_bytes": 8 << 20},
        "PREPARE_CLIP": {"cpu_ms": 5_000, "gpu_ms": 600_000, "output_bytes": 8 << 20},
        "PROPOSE": {"cpu_ms": 30_000, "gpu_ms": 0}, "YOLO_BATCH": {"cpu_ms": 30_000, "gpu_ms": 0}, "METRICS": {"cpu_ms": 5_000, "gpu_ms": 0},
        "EXPORT_BUILD": {"cpu_ms": 30_000, "gpu_ms": 0}, "EXPORT_FINALIZE": {"cpu_ms": 30_000, "gpu_ms": 0},
        "DELETE_SOURCE": {"cpu_ms": 2_000, "gpu_ms": 0}},
}


def _swap(url: str, db: str | None = None, user: str | None = None) -> str:
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(url)
    host = p.hostname + (f":{p.port}" if p.port else "")
    netloc = f"{user}:{ROLE_PASSWORD}@{host}" if user else p.netloc
    return urlunsplit((p.scheme, netloc, f"/{db}" if db else p.path, p.query, p.fragment))


def env() -> dict[str, str]:
    s3 = {"RAV_S3_BUCKET": BUCKET, "RAV_PILOT_S3_PREFIX": PREFIX, "AWS_ENDPOINT_URL": S3, "AWS_DEFAULT_REGION": "us-east-1",
          "AWS_ACCESS_KEY_ID": "smoke", "AWS_SECRET_ACCESS_KEY": "smoke"}
    return {"api": {"DATABASE_URL": _swap(ADMIN, DB, "rav_pilot_api"), "SUPABASE_URL": ISSUER_BASE, "SUPABASE_JWT_SECRET": JWT_SECRET, **s3},
            "worker": {"RAV_PILOT_WORKER_DATABASE_URL": _swap(ADMIN, DB, "rav_pilot_worker"), "RAV_PILOT_HOST_BOOT_ID": str(uuid5(NAMESPACE_URL, f"boot:{int(time.time()) // 86400}")),
                       "RAV_PILOT_WORKDIR": "output/pilot-smoke-worker", **s3},
            "web": {"VITE_RAV_MODE": "pilot", "RAV_API": "http://127.0.0.1:8010"},
            "operator": {"RAV_PILOT_MIGRATE_DATABASE_URL": _swap(ADMIN, DB), **s3}}


def token(name: str) -> str:
    import jwt
    now = int(time.time())
    return jwt.encode({"sub": USERS[name], "iss": f"{ISSUER_BASE}/auth/v1", "aud": "authenticated", "role": "authenticated",
                       "iat": now, "exp": now + 12 * 3600}, JWT_SECRET, algorithm="HS256")


def setup() -> None:
    import boto3
    import psycopg

    from src.rav.store.pilot_schema import apply_migrations

    with psycopg.connect(ADMIN, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB,)).fetchone():
            c.execute(f'CREATE DATABASE "{DB}"')
    admin = _swap(ADMIN, DB)
    print("migration:", apply_migrations(admin))
    with psycopg.connect(admin, autocommit=True) as c:
        for role in ("rav_pilot_api", "rav_pilot_worker"):
            c.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{ROLE_PASSWORD}'")
        pid = uuid5(NAMESPACE_URL, "rav-smoke:policy")
        c.execute("INSERT INTO rav_pilot_quota_policy (id, version, config, enabled, signup_default) VALUES (%s, 'smoke-1', %s, true, true) "
                  "ON CONFLICT (version) DO UPDATE SET signup_default = true", (pid, json.dumps(POLICY)))
        for name, uid in USERS.items():
            c.execute("INSERT INTO rav_pilot_profile (user_id, active, display_name, policy_id) VALUES (%s, true, %s, %s) "
                      "ON CONFLICT (user_id) DO NOTHING", (uid, f"Smoke {name}", pid))
        for key, kind in (("cpu-1", "CPU"), ("media-1", "MEDIA")):
            c.execute("INSERT INTO rav_pilot_resource_slot (slot_key, kind, account_or_host, state) VALUES (%s, %s, 'smoke-host', 'FREE') "
                      "ON CONFLICT (slot_key) DO NOTHING", (key, kind))
    s3 = boto3.client("s3", endpoint_url=S3, region_name="us-east-1", aws_access_key_id="smoke", aws_secret_access_key="smoke")
    if BUCKET not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
        s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [{"AllowedOrigins": ["http://127.0.0.1:3000"],
                       "AllowedMethods": ["GET", "POST", "PUT", "HEAD"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 600}]})
    op = {**os.environ, **env()["operator"], "PYTHONUTF8": "1"}
    plan = subprocess.run([sys.executable, "scripts/pilot_import_demo.py", "--prefix", PREFIX, "--features"], cwd=ROOT, env=op,
                          capture_output=True, text=True, encoding="utf-8", check=True)
    h = json.loads(plan.stdout[:plan.stdout.rindex("}") + 1])["inventory_hash"]
    done = subprocess.run([sys.executable, "scripts/pilot_import_demo.py", "--prefix", PREFIX, "--features", "--apply",
                           "--operator-confirm", h, "--database-url", admin], cwd=ROOT, env=op, capture_output=True, text=True, encoding="utf-8")
    print("import mẫu:", done.stdout.strip().splitlines()[-1] if done.stdout.strip() else done.stderr.strip()[-400:])
    yolo = ROOT / "data/models/yolo11n.pt"
    if yolo.exists():
        args = ["scripts/pilot_import_model.py", "--file", str(yolo), "--name", "YOLO11n COCO (Ultralytics)", "--train-dataset", "COCO",
                "--prefix", PREFIX]
        dry = subprocess.run([sys.executable, *args], cwd=ROOT, env=op, capture_output=True, text=True, encoding="utf-8", check=True)
        mh = json.loads(dry.stdout[dry.stdout.index("{"):dry.stdout.rindex("}") + 1])["plan_hash"]
        res = subprocess.run([sys.executable, *args, "--apply", "--operator-confirm", mh, "--database-url", admin], cwd=ROOT, env=op,
                             capture_output=True, text=True, encoding="utf-8")
        print("import model:", res.stdout.strip().splitlines()[-1] if res.stdout.strip() else res.stderr.strip()[-400:])
    print("users:", USERS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("env")
    t = sub.add_parser("token")
    t.add_argument("user", choices=sorted(USERS))
    args = ap.parse_args()
    if args.cmd == "setup":
        setup()
    elif args.cmd == "env":
        print(json.dumps(env(), indent=1))
    else:
        print(token(args.user))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
