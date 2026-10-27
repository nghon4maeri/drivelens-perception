"""Áp migration PostgreSQL của pilot (chỉ operator, CONTRACTS §21.11).

Input: `--database-url` (role có quyền DDL, vd postgres/owner trên Supabase) hoặc biến `RAV_PILOT_MIGRATE_DATABASE_URL`.
Output: in các file vừa áp; checksum lệch với bản đã áp → thoát mã 2, không sửa gì.
Chạy: `python scripts/pilot_migrate.py --database-url postgresql://...`
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rav.store.pilot_schema import apply_migrations  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default=os.environ.get("RAV_PILOT_MIGRATE_DATABASE_URL"))
    args = ap.parse_args()
    if not args.database_url or not args.database_url.startswith(("postgresql://", "postgres://")):
        print("cần --database-url postgresql://... (không in giá trị)", file=sys.stderr)
        return 1
    try:
        applied = apply_migrations(args.database_url)
    except RuntimeError as e:
        print(f"lỗi: {e}", file=sys.stderr)
        return 2
    print("đã áp: " + (", ".join(applied) if applied else "không có migration mới"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
