"""Seed kho dữ liệu mẫu SYSTEM (video + FRAMES + DINO + CLIP + published=true) cho pilot (CONTRACTS §21.36).

Làm gì:
- Đọc video mẫu hợp lệ (mặc định `data/videos/commaai-sample.mp4`), xác minh SHA-256 và lưới unit chuẩn (`DEFAULT_SAMPLE_SPEC`).
- Ghi raw video, bộ ảnh `THUMBNAIL`/`FRAME` và đặc trưng `DINO`/`CLIP` lên S3 ở nhánh `system/<seed_version>/...`.
- Ghi idempotent `rav_pilot_source_asset` (`scope='SYSTEM', owner_id=NULL, protected=true, published=true`),
  `rav_pilot_frame_set` + `rav_pilot_frame`, và `rav_pilot_artifact` (`DINO`, `CLIP`) ở trạng thái `READY`.

Input: đường dẫn file video, tên/mô tả hiển thị, `PilotDB` + `PilotStorage` (hoặc biến môi trường).
Output: dict tóm tắt `{source_id, core_source_id, sha256, n_units, frames_ready, dino_ready, clip_ready, published}`.
Cách chạy:
  python scripts/pilot_seed_sample_catalog.py --video data/videos/commaai-sample.mp4 --apply
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rav.core.types import FieldTable  # noqa: E402
from src.rav.store import pilot_frames  # noqa: E402
from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB  # noqa: E402
from src.rav.store.pilot_features import FIELDS  # noqa: E402
from src.rav.store.pilot_features import input_hash as feature_input_hash  # noqa: E402
from src.rav.store.pilot_storage import PilotStorage  # noqa: E402
from src.rav.worker.gpu_handlers import feature_npz  # noqa: E402
from src.rav.worker.handlers import _decoder, _ext, _probe  # noqa: E402


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _encode_jpeg(rgb, max_side: int | None) -> tuple[bytes, int, int, str]:
    from PIL import Image

    img = Image.fromarray(rgb)
    if max_side is not None:
        img.thumbnail((max_side, max_side))
    w, h = img.size
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    data = buf.getvalue()
    return data, w, h, hashlib.sha256(data).hexdigest()


def _build_feature_tables(ctx, feature_name: str) -> tuple[FieldTable, ...]:
    """Run the real registered components; missing models fail before publication."""
    from src.rav.components.features.clip_b32 import ClipB32
    from src.rav.components.features.dinov2_s import DinoV2S
    from src.rav.components.features.phash import PHash

    features = (DinoV2S(), PHash()) if feature_name == "DINO" else (ClipB32(),)
    return tuple(feature.compute(ctx.units, ctx) for feature in features)


def seed_sample_video(
    db: PilotDB,
    storage: PilotStorage,
    video_path: Path,
    *,
    name: str = "Comma.ai Dashcam Sample",
    description: str = "Video mẫu dashcam đã chuẩn bị sẵn ảnh frame, DINOv2 và CLIP để tạo phiên ngay.",
    seed_version: str = "sample-catalog-v1",
) -> dict[str, Any]:
    """Chuẩn bị idempotent một video mẫu SYSTEM đủ FRAMES + DINO + CLIP và bật published=true."""
    from psycopg.types.json import Jsonb

    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy video mẫu: {video_path}")

    sha = _file_sha256(video_path)
    size_bytes = video_path.stat().st_size
    video = _probe(video_path)
    ext = _ext(video_path)
    dec = _decoder()
    from src.rav.pipeline.prepare import make_context

    ctx = make_context([video], {"source": None, **DEFAULT_SAMPLE_SPEC}, None)
    n_units = len(ctx.units)
    duration_ms = round(video.duration_s * 1000)
    metadata = {
        "width": video.meta["width"],
        "height": video.meta["height"],
        "duration_ms": duration_ms,
        "n_frames": video.meta["n_frames"],
        "n_units": n_units,
        "fps": video.fps,
    }
    source_id = uuid5(NAMESPACE_URL, f"rav-pilot-system-source:{video.id}")
    set_id = uuid5(NAMESPACE_URL, f"rav-pilot-system-frames:{video.id}:{pilot_frames.ENCODING_VERSION}")
    raw_key = storage.system_raw_key(seed_version, video.id, sha, ext)

    head = storage.head(raw_key)
    if head is None or head["ContentLength"] != size_bytes:
        storage.put_file(raw_key, video_path, sha, f"video/{ext}")

    # Decode frames once for both JPEG materialization and feature extraction
    unit_ids: list[str] = []
    frame_items: list[dict[str, Any]] = []
    total_frame_bytes = 0

    for unit in ctx.units:
        idx = unit.anchor.idx
        uid_str = unit.id
        unit_ids.append(uid_str)
        rgb = dec.read_exact(video, unit.anchor)
        for variant, max_side in (("THUMBNAIL", pilot_frames.THUMBNAIL_SIDE), ("FRAME", None)):
            jpeg_bytes, w, h, f_sha = _encode_jpeg(rgb, max_side)
            fkey = storage.system_frame_key(seed_version, video.id, set_id, idx, variant, f_sha)
            if storage.head(fkey) is None:
                storage.put_bytes(fkey, jpeg_bytes, f_sha, "image/jpeg")
            total_frame_bytes += len(jpeg_bytes)
            frame_items.append({
                "variant": variant,
                "frame_idx": idx,
                "unit_id": uid_str,
                "object_key": fkey,
                "sha256": f_sha,
                "size_bytes": len(jpeg_bytes),
                "width": w,
                "height": h,
            })

    src_stub = {
        "id": source_id,
        "core_source_id": video.id,
        "scope": "SYSTEM",
        "owner_id": None,
        "raw_sha256": sha,
        "metadata": metadata,
    }
    feature_records: list[dict[str, Any]] = []
    for feat in ("DINO", "CLIP"):
        tables = _build_feature_tables(ctx, feat)
        npz_bytes, meta = feature_npz(feat, tables)
        npz_sha = hashlib.sha256(npz_bytes).hexdigest()
        ih = feature_input_hash(src_stub, feat)
        fkey = storage.system_feature_key(seed_version, video.id, feat, npz_sha)
        if storage.head(fkey) is None:
            storage.put_bytes(fkey, npz_bytes, npz_sha, "application/octet-stream")
        art_id = uuid5(NAMESPACE_URL, f"rav-pilot-system-feature:{video.id}:{feat}:{ih}")
        manifest = {
            "source_id": str(source_id),
            "core_source_id": video.id,
            "feature": feat,
            "input_hash": ih,
            "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"],
            "n_units": n_units,
            "npz_sha256": npz_sha,
            "fields": list(FIELDS[feat]),
            **meta,
        }
        feature_records.append({
            "id": art_id,
            "feature": feat,
            "input_hash": ih,
            "object_key": fkey,
            "sha256": npz_sha,
            "size_bytes": len(npz_bytes),
            "manifest": manifest,
        })

    with db.backend() as cur:
        cur.execute(
            "SELECT id, raw_sha256, raw_key FROM rav_pilot_source_asset WHERE core_source_id = %s AND scope = 'SYSTEM' "
            "AND availability <> 'DELETED' FOR UPDATE",
            (video.id,),
        )
        existing = cur.fetchone()
        if existing:
            source_id = existing["id"]
            cur.execute(
                "UPDATE rav_pilot_source_asset SET name = %s, description = %s, published = true, availability = 'READY' "
                "WHERE id = %s",
                (name, description, source_id),
            )
        else:
            cur.execute(
                "INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, published, "
                "name, description, raw_key, raw_sha256, size_bytes, provenance, metadata, availability) "
                "VALUES (%s, %s, 'VIDEO', 'SYSTEM', NULL, true, true, %s, %s, %s, %s, %s, %s, %s, 'READY')",
                (
                    source_id,
                    video.id,
                    name,
                    description,
                    raw_key,
                    sha,
                    size_bytes,
                    Jsonb({"seed_version": seed_version}),
                    Jsonb(metadata),
                ),
            )
        src_row = {**src_stub, "id": source_id}
        if not pilot_frames.ready_set_in_tx(cur, source_id, sha):
            frames_payload = {
                "frame_set_id": str(set_id),
                "n_units": n_units,
                "total_bytes": total_frame_bytes,
                "encoding_version": pilot_frames.ENCODING_VERSION,
                "sample_spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"],
                "items": frame_items,
            }
            pilot_frames.validate_set(src_row, frames_payload)
            pilot_frames.insert_set_in_tx(cur, src_row, frames_payload, None, None)

        for rec in feature_records:
            rec["manifest"]["source_id"] = str(source_id)
            cur.execute(
                "SELECT id FROM rav_pilot_artifact WHERE kind = 'FEATURE' AND state = 'READY' AND source_id = %s "
                "AND variant = %s AND input_hash = %s AND scope = 'SYSTEM'",
                (source_id, rec["feature"], rec["input_hash"]),
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO rav_pilot_artifact (id, owner_id, scope, source_id, kind, variant, input_hash, "
                    "object_key, sha256, size_bytes, state, manifest) "
                    "VALUES (%s, NULL, 'SYSTEM', %s, 'FEATURE', %s, %s, %s, %s, %s, 'READY', %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (
                        rec["id"],
                        source_id,
                        rec["feature"],
                        rec["input_hash"],
                        rec["object_key"],
                        rec["sha256"],
                        rec["size_bytes"],
                        Jsonb(rec["manifest"]),
                    ),
                )

    return {
        "source_id": str(source_id),
        "core_source_id": video.id,
        "sha256": sha,
        "n_units": n_units,
        "frames_ready": True,
        "dino_ready": True,
        "clip_ready": True,
        "published": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, default=ROOT / "data" / "videos" / "commaai-sample.mp4")
    ap.add_argument("--name", default="Comma.ai Dashcam Sample")
    ap.add_argument("--description", default="Video mẫu dashcam đã chuẩn bị sẵn ảnh frame, DINOv2 và CLIP để tạo phiên ngay.")
    ap.add_argument("--seed-version", default="sample-catalog-v1")
    ap.add_argument("--database-url", default=os.environ.get("RAV_PILOT_DATABASE_URL") or os.environ.get("RAV_PILOT_MIGRATE_DATABASE_URL"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not args.video.is_file():
        print(f"Không tìm thấy file video: {args.video}", file=sys.stderr)
        return 1
    if not args.apply:
        sha = _file_sha256(args.video)
        video = _probe(args.video)
        print(json.dumps({
            "dry_run": True,
            "video": str(args.video),
            "core_source_id": video.id,
            "sha256": sha,
            "size_bytes": args.video.stat().st_size,
            "name": args.name,
        }, ensure_ascii=False, indent=2))
        return 0
    if not args.database_url:
        print("Cần --database-url hoặc RAV_PILOT_DATABASE_URL khi chạy --apply", file=sys.stderr)
        return 1

    db = PilotDB(args.database_url)
    storage = PilotStorage.from_env()
    summary = seed_sample_video(
        db,
        storage,
        args.video,
        name=args.name,
        description=args.description,
        seed_version=args.seed_version,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
