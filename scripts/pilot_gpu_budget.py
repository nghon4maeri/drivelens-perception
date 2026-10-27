"""Ghi tay snapshot ngân sách GPU Kaggle (operator, dự phòng khi worker không đọc được SDK) — CONTRACTS §21.14, #94.

Input: account Kaggle của slot GPU, số ms GPU còn lại đã xem trên Kaggle, mốc reset, operator UUID, tham chiếu bằng chứng.
Output: một dòng `rav_pilot_gpu_budget_snapshot` source=OPERATOR (bất biến). Snapshot mới hơn của worker vẫn được ưu tiên theo thời gian.
Chạy:
  python scripts/pilot_gpu_budget.py --database-url postgresql://... --account <kaggle> --remaining-hours 20.5 \
      --reset-at 2026-10-17T00:00:00Z --operator-id <uuid> --evidence "ảnh quota Kaggle 2026-10-10 15:00"
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rav.store import pilot_gpu  # noqa: E402
from src.rav.store.pilot_db import PilotDB  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=os.environ.get("RAV_PILOT_MIGRATE_DATABASE_URL"))
    ap.add_argument("--account", required=True)
    ap.add_argument("--remaining-hours", type=float, required=True)
    ap.add_argument("--reset-at", required=True, help="ISO 8601, vd 2026-10-17T00:00:00Z")
    ap.add_argument("--operator-id", type=UUID, required=True)
    ap.add_argument("--evidence", required=True, help="mô tả/tham chiếu bằng chứng (không dán token)")
    args = ap.parse_args()
    if not args.database_url:
        print("cần --database-url (role operator)", file=sys.stderr)
        return 1
    if args.remaining_hours < 0:
        print("--remaining-hours phải ≥ 0", file=sys.stderr)
        return 1
    reset = datetime.fromisoformat(args.reset_at.replace("Z", "+00:00"))
    sid = pilot_gpu.record_snapshot(PilotDB(args.database_url), provider_account=args.account, source="OPERATOR",
                                    remaining_ms=round(args.remaining_hours * 3_600_000), reset_at=reset,
                                    evidence_ref=args.evidence[:500], evidence={"entered_by": "operator_cli"},
                                    operator_id=args.operator_id)
    print(f"snapshot {sid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
