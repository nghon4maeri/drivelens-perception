"""Import phiên mẫu SYSTEM từ SQLite local vào Postgres pilot (operator, CONTRACTS §21.12; identity spec §9; quyết định #90).

Input: inventory (`docs/setup/pilot_demo_inventory.json`), SQLite local (chỉ đọc), thư mục video, prefix S3 pilot.
Mặc định **dry-run**: dựng kế hoạch (nguồn SYSTEM + mẫu + sự kiện đã lọc), in `inventory_hash`, ghi plan vào `output/pilot-import/`.
Ghi thật: `--apply --operator-confirm <inventory_hash>` → PUT raw lên S3 (kèm SHA-256, bỏ qua nếu đã có đúng kích thước)
rồi một transaction Postgres (role operator). Chạy lại cùng hash → no-op. Không chép `.env`, không ghi đường dẫn máy vào DB.
`--features` (DEP5a, CONTRACTS §21.14, #94): kèm đặc trưng DINO/CLIP đã tính local (FieldStore `--fields`) của nguồn SYSTEM —
kiểm ordered IDs khớp decoder, shape/dtype/finite/L2 — thành artifact FEATURE scope SYSTEM trong cùng kế hoạch/hash.
Seed đã import không kèm đặc trưng thì cần `seed_version` mới (receipt theo seed_version).
Chạy:
  python scripts/pilot_import_demo.py --prefix pilot-rav26 --features
  python scripts/pilot_import_demo.py --prefix pilot-rav26 --features --apply --operator-confirm <hash> --database-url postgresql://...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rav.store.pilot_db import canonical_hash  # noqa: E402
from src.rav.store.pilot_templates import apply_plan, project_events  # noqa: E402

NAMESPACE = uuid5(NAMESPACE_URL, "https://rav26.local/pilot-demo")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_entry(path: Path, seed_version: str, prefix: str) -> dict:
    """Metadata bằng đúng probe + decoder của core (id nội dung = sha256[:16], n_units theo pyav 4 fps)."""
    from src.rav.worker.handlers import _decoder, _ext, _probe

    video = _probe(path)
    sha = _sha_file(path)
    if video.id != sha[:16]:
        raise SystemExit(f"{path.name}: id nội dung không khớp SHA-256")
    ext = _ext(path)
    return {"id": str(uuid5(NAMESPACE, f"{seed_version}:source:{video.id}")), "core_source_id": video.id,
            "name": f"{video.id}.{ext}", "sha256": sha, "size_bytes": path.stat().st_size, "ext": ext,
            "raw_key": f"{prefix}/system/{seed_version}/sources/{video.id}/raw/{sha}/video.{ext}",
            "metadata": {"width": video.meta["width"], "height": video.meta["height"],
                         "duration_ms": round(video.duration_s * 1000), "n_frames": video.meta["n_frames"],
                         "n_units": len(_decoder().frames(video)), "fps": video.fps}}


def _check_table(table, ids: list[str], dim: int | None) -> None:
    import numpy as np

    values = np.asarray(table.values)
    if list(table.unit_ids) != ids:
        raise SystemExit(f"{table.key.name}: ordered unit IDs không khớp decoder")
    if dim is None:
        if values.dtype != np.uint64 or values.shape != (len(ids),):
            raise SystemExit(f"{table.key.name}: pHash phải uint64 ({len(ids)},)")
        return
    if (values.dtype != np.float32 or values.shape != (len(ids), dim) or not np.isfinite(values).all()
            or not np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-4)):
        raise SystemExit(f"{table.key.name}: cần float32 ({len(ids)},{dim}) hữu hạn, chuẩn hóa L2")


def feature_entries(sources: list[dict], files: dict[str, Path], fields_root: Path, seed: str,
                    prefix: str) -> tuple[list[dict], dict[str, bytes]]:
    """Artifact FEATURE SYSTEM từ FieldStore local; thiếu field thì bỏ feature đó (in cảnh báo), không tự tính."""
    from src.rav.components.features.clip_b32 import ClipB32
    from src.rav.components.features.dinov2_s import DinoV2S
    from src.rav.components.features.phash import PHash
    from src.rav.errors import ServiceError
    from src.rav.store.field_store import FieldStore
    from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC
    from src.rav.store.pilot_features import input_hash
    from src.rav.worker.gpu_handlers import build_context, feature_npz

    store, entries, blobs = FieldStore(fields_root), [], {}
    spec = {k: DEFAULT_SAMPLE_SPEC[k] for k in ("decoder", "units")}
    for s in sources:
        core = s["core_source_id"]
        try:
            ctx = build_context(files[core], {"core_source_id": core, "spec": spec, "n_units": s["metadata"]["n_units"]})
        except ServiceError as exc:
            raise SystemExit(f"{core}: {exc.message}") from None
        if ctx._execution.spec_hash != DEFAULT_SAMPLE_SPEC["spec_hash"]:
            raise SystemExit("spec_hash decoder/unit khác spec pilot")
        ids = [u.id for u in ctx.units]
        for feature, comps in (("DINO", ((DinoV2S(), 384), (PHash(), None))), ("CLIP", ((ClipB32(), 512),))):
            tables = [store.get(c.key, (core, DEFAULT_SAMPLE_SPEC["spec_hash"])) for c, _ in comps]
            if any(tb is None for tb in tables):
                print(f"cảnh báo: {core} thiếu {feature} trong {fields_root.name}; bỏ qua", file=sys.stderr)
                continue
            for tb, (_, dim) in zip(tables, comps, strict=True):
                _check_table(tb, ids, dim)
            data, meta = feature_npz(feature, tuple(tables))
            sha = hashlib.sha256(data).hexdigest()
            ih = input_hash({"core_source_id": core, "raw_sha256": s["sha256"]}, feature)
            entries.append({"id": str(uuid5(NAMESPACE, f"{seed}:feature:{core}:{feature}")), "source_id": s["id"],
                            "core_source_id": core, "feature": feature, "input_hash": ih, "sha256": sha, "size_bytes": len(data),
                            "object_key": f"{prefix}/system/{seed}/features/{core}/{feature}/{sha}.npz",
                            "manifest": {"source_id": s["id"], "core_source_id": core, "feature": feature, "input_hash": ih,
                                         "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"], "n_units": len(ids), "npz_sha256": sha,
                                         "runtime": {"origin": "operator_import"}, **meta}})
            blobs[sha] = data
    return entries, blobs


def build_plan(inventory: dict, sqlite_path: Path, videos_dir: Path, prefix: str) -> tuple[dict, dict[str, Path]]:
    """Trả (plan công khai, core_source_id → file local). Plan không chứa đường dẫn máy."""
    seed = inventory["seed_version"]
    db = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    by_core: dict[str, Path] = {}
    for f in sorted(videos_dir.glob("*")):
        if f.is_file():
            by_core.setdefault(_sha_file(f)[:16], f)
    sources: dict[str, dict] = {}
    templates = []
    for t in inventory["templates"]:
        row = db.execute("SELECT spec FROM session WHERE id = ?", (t["legacy_session_id"],)).fetchone()
        if row is None:
            raise SystemExit(f"không có phiên {t['legacy_session_id']} trong {sqlite_path.name}")
        spec = json.loads(row["spec"])
        if (spec.get("reference") or {}).get("dataset_version_ids") or (spec.get("reference") or {}).get("model_id"):
            raise SystemExit(f"{t['template_key']}: phiên có tham chiếu dataset/model — không import quan hệ chưa xác minh")
        pool = spec["pool"]["video_ids"]
        for core in pool:
            if core not in by_core:
                raise SystemExit(f"{t['template_key']}: thiếu file video {core} trong {videos_dir}")
            sources.setdefault(core, _source_entry(by_core[core], seed, prefix))
        sid = t["legacy_session_id"]
        proposals = [{"n": r["n"], "created_at": r["created_at"], "snapshot": json.loads(r["snapshot"]),
                      "picks": json.loads(r["picks"])} for r in db.execute(
                          "SELECT n, created_at, snapshot, picks FROM proposal WHERE session_id = ? ORDER BY n", (sid,))]
        decisions = [dict(r) for r in db.execute("SELECT seq, unit_id, action, actor, proposal_n, decided_at, reason "
                                                 "FROM decision WHERE session_id = ? ORDER BY seq", (sid,))]
        snapshots = [dict(r) for r in db.execute("SELECT n, n_members, members_hash, decision_seq, created_by, created_at "
                                                 "FROM snapshot WHERE session_id = ? ORDER BY n", (sid,))]
        events = project_events(proposals, decisions, snapshots)
        frozen = {"name": t["name"], "goal": spec["goal"], "target_k": spec["target_k"], "pool": {"video_ids": pool},
                  "reference": {"dataset_version_ids": []}}
        mapping = [{"core_source_id": c, "source_id": sources[c]["id"]} for c in pool]
        templates.append({"id": str(uuid5(NAMESPACE, f"{seed}:template:{t['template_key']}")), "template_key": t["template_key"],
                          "name": t["name"], "legacy_session_id": sid, "frozen_spec": frozen, "source_mapping": mapping,
                          "events": events, "manifest_hash": canonical_hash({"frozen_spec": frozen, "mapping": mapping,
                                                                             "events": [e["payload_hash"] for e in events]})})
    plan = {"seed_version": seed, "sources": [sources[c] for c in sorted(sources)], "templates": templates}
    return plan, {c: by_core[c] for c in sources}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", type=Path, default=ROOT / "docs/setup/pilot_demo_inventory.json")
    ap.add_argument("--sqlite", type=Path, default=ROOT / "output/rav.sqlite")
    ap.add_argument("--videos", type=Path, default=ROOT / "data/videos")
    ap.add_argument("--prefix", default=os.environ.get("RAV_PILOT_S3_PREFIX"))
    ap.add_argument("--database-url", default=os.environ.get("RAV_PILOT_MIGRATE_DATABASE_URL"))
    ap.add_argument("--features", action="store_true", help="kèm đặc trưng DINO/CLIP local của nguồn SYSTEM (§21.14)")
    ap.add_argument("--fields", type=Path, default=ROOT / "output/fields")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--operator-confirm")
    args = ap.parse_args()
    if not args.prefix:
        print("cần --prefix hoặc RAV_PILOT_S3_PREFIX (key S3 nằm trong kế hoạch)", file=sys.stderr)
        return 1
    plan, files = build_plan(json.loads(args.inventory.read_text(encoding="utf-8")), args.sqlite, args.videos, args.prefix)
    blobs: dict[str, bytes] = {}
    if args.features:
        plan["features"], blobs = feature_entries(plan["sources"], files, args.fields, plan["seed_version"], args.prefix)
    inventory_hash = canonical_hash(plan)
    out = ROOT / "output/pilot-import" / f"plan-{plan['seed_version']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"inventory_hash": inventory_hash, **plan}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"inventory_hash": inventory_hash, "sources": len(plan["sources"]),
                      "templates": [{"key": t["template_key"], "events": len(t["events"])} for t in plan["templates"]],
                      "features": [f"{f['core_source_id']}:{f['feature']}" for f in plan.get("features", [])],
                      "plan": str(out.relative_to(ROOT))}, ensure_ascii=False, indent=1))
    if not args.apply:
        print("dry-run: chưa ghi gì. Ghi thật: --apply --operator-confirm <inventory_hash>")
        return 0
    if args.operator_confirm != inventory_hash:
        print("operator-confirm không khớp inventory_hash hiện tại; không ghi gì", file=sys.stderr)
        return 2
    if not args.database_url:
        print("cần --database-url (role operator)", file=sys.stderr)
        return 1
    import psycopg
    from psycopg.rows import dict_row

    from src.rav.store.pilot_storage import PilotStorage

    storage = PilotStorage.from_env()
    if storage.prefix != args.prefix:
        print("prefix kế hoạch khác RAV_PILOT_S3_PREFIX của storage; không ghi gì", file=sys.stderr)
        return 2
    for s in plan["sources"]:
        head = storage.head(s["raw_key"])
        if head is None or head["ContentLength"] != s["size_bytes"]:
            storage.put_file(s["raw_key"], files[s["core_source_id"]], s["sha256"], f"video/{s['ext']}")
    for f in plan.get("features", []):
        head = storage.head(f["object_key"])
        if head is None or head["ContentLength"] != f["size_bytes"]:
            tmp = ROOT / "output/pilot-import" / f"{f['sha256']}.npz"
            tmp.write_bytes(blobs[f["sha256"]])
            try:
                storage.put_file(f["object_key"], tmp, f["sha256"], "application/octet-stream")
            finally:
                tmp.unlink(missing_ok=True)
    with psycopg.connect(args.database_url, row_factory=dict_row) as conn, conn.transaction():
        result = apply_plan(conn.cursor(), plan, inventory_hash)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
