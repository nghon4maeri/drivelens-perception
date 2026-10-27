"""Import checkpoint YOLO detection thành model SYSTEM của pilot (operator, CONTRACTS §21.17, #97).

Input: file `.pt` (vd `data/models/yolo11n.pt`), tên hiển thị, dataset train gốc (provenance, ghi đúng sự thật — COCO nano không phải model train BDD).
Mặc định **dry-run**: kiểm SHA-256, nạp bằng Ultralytics đúng phiên bản pin, chạy thử một ảnh để xác minh task detect → in kế hoạch + `plan_hash`.
Ghi thật: `--apply --operator-confirm <plan_hash> --database-url ...` → PUT `<prefix>/system/models/yolo/<sha>.pt` (kèm SHA) rồi INSERT
`rav_pilot_model_asset` (`validation=INFERENCE_VERIFIED` chỉ khi chạy thử thành công). Chạy lại cùng file → no-op.
Chạy:
  python scripts/pilot_import_model.py --file data/models/yolo11n.pt --name "YOLO11n COCO (Ultralytics)" --train-dataset COCO
  python scripts/pilot_import_model.py ... --apply --operator-confirm <plan_hash> --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rav.store.pilot_db import canonical_hash  # noqa: E402
from src.rav.store.pilot_models import insert_model_in_tx, model_row  # noqa: E402


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(path: Path, name: str, train_dataset: str, prefix: str) -> dict:
    """Kiểm SHA + nạp/chạy thử một ảnh xám (chung code với job MODEL_VERIFY, §21.33)."""
    from src.rav.pipeline import batch_yolo

    info = batch_yolo.verify_checkpoint(path, ROOT / "output" / "pilot-import")
    return model_row(prefix, _sha(path), path.stat().st_size, name, path.name, train_dataset, info)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--train-dataset", required=True, help="dataset train gốc của checkpoint (vd COCO)")
    ap.add_argument("--prefix", default=os.environ.get("RAV_PILOT_S3_PREFIX"))
    ap.add_argument("--database-url", default=os.environ.get("RAV_PILOT_MIGRATE_DATABASE_URL"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--operator-confirm")
    args = ap.parse_args()
    if not args.prefix:
        print("cần --prefix hoặc RAV_PILOT_S3_PREFIX", file=sys.stderr)
        return 1
    plan = build_plan(args.file, args.name, args.train_dataset, args.prefix)
    plan_hash = canonical_hash(plan)
    print(json.dumps({"plan_hash": plan_hash, **{k: plan[k] for k in ("id", "name", "sha256", "size_bytes", "task", "raw_key", "checkpoint_revision")},
                      "classes": len(plan["class_map"])}, ensure_ascii=False, indent=1))
    if not args.apply:
        print("dry-run: chưa ghi gì. Ghi thật: --apply --operator-confirm <plan_hash>")
        return 0
    if args.operator_confirm != plan_hash:
        print("operator-confirm không khớp plan_hash; không ghi gì", file=sys.stderr)
        return 2
    if not args.database_url:
        print("cần --database-url (role operator)", file=sys.stderr)
        return 1
    import psycopg

    from src.rav.store.pilot_storage import PilotStorage

    storage = PilotStorage.from_env()
    if storage.prefix != args.prefix:
        print("prefix kế hoạch khác RAV_PILOT_S3_PREFIX của storage; không ghi gì", file=sys.stderr)
        return 2
    head = storage.head(plan["raw_key"])
    if head is None or head["ContentLength"] != plan["size_bytes"]:
        storage.put_file(plan["raw_key"], args.file, plan["sha256"], "application/octet-stream")
    with psycopg.connect(args.database_url) as conn, conn.transaction(), conn.cursor() as cur:
        insert_model_in_tx(cur, plan)
    print(json.dumps({"model_id": plan["id"], "imported": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
