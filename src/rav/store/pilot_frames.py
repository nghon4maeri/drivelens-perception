"""Ảnh lưu sẵn trên S3 cho thumbnail/frame (CONTRACTS §21.20, quyết định #119).

- `FRAME_ENCODING` / `ENCODING_VERSION`: cách encode cố định; đổi tham số = version mới, bộ ảnh mới (không ghi đè).
- `stored_variant`: map `(variant, max_side)` của media request về ảnh có sẵn (THUMBNAIL ≤ 320 hoặc FRAME đầy đủ).
- `lookup_in_tx` / `frame_by_id_in_tx`: tìm ảnh trong frame set READY của nguồn (đúng raw/spec/encoding); không decode, không ghi DB.
- `readiness_in_tx`: `PrepState` của bộ ảnh cho `SourceDTO.frames` (đọc bằng role backend, lọc owner/SYSTEM tường minh).
- Hook worker: `bounds` (policy → payload), `publish_set_in_tx` (chèn bộ đủ + persisted một lần), `accept_in_tx` (ACCEPT receipt ảnh).
Import nhẹ (không numpy/PIL) để API dùng được.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from ..errors import ServiceError
from . import pilot_storage
from .pilot_db import DEFAULT_SAMPLE_SPEC, PrepState, canonical_hash
from .pilot_runtime import ACTIVE

_SHA = re.compile(r"[0-9a-f]{64}")

THUMBNAIL_SIDE = 320
FRAME_ENCODING = {"format": "JPEG", "quality": 85, "thumbnail": {"max_side": THUMBNAIL_SIDE, "resize": "PIL.Image.thumbnail"},
                  "source": "exact_rgb_anchor"}
ENCODING_VERSION = canonical_hash(FRAME_ENCODING)[:16]
VARIANTS = ("THUMBNAIL", "FRAME")


def stored_variant(variant: str, max_side: int | None) -> str:
    """`thumbnail` trống/≤ 320 → THUMBNAIL; lớn hơn → FRAME (client tự thu nhỏ); `frame` luôn FRAME."""
    variant = variant.upper()
    if variant not in VARIANTS:
        raise ServiceError("validation_error", "variant ảnh phải là thumbnail hoặc frame", 422)
    if variant == "THUMBNAIL" and (max_side is None or max_side <= THUMBNAIL_SIDE):
        return "THUMBNAIL"
    return "FRAME"


def _ready_set_sql() -> str:
    return ("s.state = 'READY' AND s.raw_sha256 = src.raw_sha256 AND s.sample_spec_hash = %s AND s.encoding_version = %s")


def lookup_in_tx(cur, src: dict, variant: str, frame_idx: int) -> dict | None:
    """Ảnh của unit trong frame set READY của nguồn `src` (đã khóa/kiểm quyền bởi caller)."""
    variant = stored_variant(variant, None)
    cur.execute("SELECT f.id, f.object_key, f.sha256 FROM rav_pilot_frame_set s "
                "JOIN rav_pilot_source_asset src ON src.id = s.source_id "
                "JOIN rav_pilot_frame f ON f.frame_set_id = s.id "
                f"WHERE s.source_id = %s AND {_ready_set_sql()} AND f.variant = %s AND f.frame_idx = %s",
                (src["id"], DEFAULT_SAMPLE_SPEC["spec_hash"], ENCODING_VERSION, variant, frame_idx))
    return cur.fetchone()


def frame_by_id_in_tx(cur, uid: UUID, frame_id: UUID) -> dict | None:
    """Ảnh theo id cho `GET /media/requests/{id}`: chỉ của owner hoặc SYSTEM được phép đọc; trả cả trạng thái set/nguồn để caller quyết cấp URL."""
    from .pilot_db import PilotDB

    cur.execute("SELECT f.id, f.object_key, f.sha256, s.state AS set_state, src.id AS source_id, src.scope, src.owner_id, "
                "src.published, src.availability FROM rav_pilot_frame f "
                "JOIN rav_pilot_frame_set s ON s.id = f.frame_set_id JOIN rav_pilot_source_asset src ON src.id = s.source_id "
                "WHERE f.id = %s AND (s.owner_id = %s OR s.scope = 'SYSTEM')", (frame_id, uid))
    row = cur.fetchone()
    if not row:
        return None
    if not PilotDB.can_read_source_in_tx(cur, uid, {"id": row["source_id"], "scope": row["scope"],
                                                     "owner_id": row["owner_id"], "published": row.get("published", True)}):
        return None
    return row


def active_job_in_tx(cur, uid: UUID, source_id: UUID) -> UUID | None:
    cur.execute("SELECT id FROM rav_pilot_job WHERE (owner_id = %s OR payload ->> 'target_scope' = 'SYSTEM') "
                "AND kind = 'PREPARE_FRAMES' AND resource_id = %s "
                "AND state = ANY(%s) ORDER BY created_at LIMIT 1", (uid, source_id, list(ACTIVE)))
    row = cur.fetchone()
    return row["id"] if row else None


def readiness_in_tx(cur, uid: UUID, source_ids: list[UUID]) -> dict[UUID, PrepState]:
    out = {sid: PrepState(ready=False, job_id=None) for sid in source_ids}
    if not source_ids:
        return out
    cur.execute("SELECT DISTINCT s.source_id FROM rav_pilot_frame_set s JOIN rav_pilot_source_asset src ON src.id = s.source_id "
                f"WHERE s.source_id = ANY(%s) AND (s.owner_id = %s OR s.scope = 'SYSTEM') AND {_ready_set_sql()}",
                (source_ids, uid, DEFAULT_SAMPLE_SPEC["spec_hash"], ENCODING_VERSION))
    for r in cur.fetchall():
        out[r["source_id"]] = PrepState(ready=True, job_id=None)
    cur.execute("SELECT resource_id, id FROM rav_pilot_job "
                "WHERE (owner_id = %s OR payload ->> 'target_scope' = 'SYSTEM') AND kind = 'PREPARE_FRAMES' AND state = ANY(%s) "
                "AND resource_id = ANY(%s) ORDER BY created_at", (uid, list(ACTIVE), source_ids))
    for r in cur.fetchall():
        state = out[r["resource_id"]]
        if not state.ready and state.job_id is None:
            state.job_id = r["id"]
    return out


# ------------------------------------------------------------------------------
# Policy + publish (hook worker, cùng transaction fenced với nguồn)
# ------------------------------------------------------------------------------
FRAME_BOUND_KEYS = ("frame_batch_size", "frame_put_concurrency", "max_frame_bytes")


def bounds(cfg: dict, kind: str) -> dict:
    """Giới hạn tách ảnh đóng băng vào payload lúc enqueue; thiếu khóa → 503 `policy_unconfigured`."""
    raw = (cfg.get("chunk_bounds_by_kind") or {}).get(kind) or {}
    out = {k: raw.get(k) for k in FRAME_BOUND_KEYS}
    if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in out.values()):
        raise ServiceError("policy_unconfigured", f"policy thiếu chunk_bounds_by_kind.{kind}.{'/'.join(FRAME_BOUND_KEYS)}", 503)
    return {**out, "worker_temp_bytes": cfg["worker_temp_bytes"], "encoding_version": ENCODING_VERSION}


def ready_set_in_tx(cur, source_id: UUID, raw_sha256: str) -> UUID | None:
    cur.execute("SELECT id FROM rav_pilot_frame_set WHERE source_id = %s AND raw_sha256 = %s AND sample_spec_hash = %s "
                "AND encoding_version = %s AND state = 'READY'",
                (source_id, raw_sha256, DEFAULT_SAMPLE_SPEC["spec_hash"], ENCODING_VERSION))
    row = cur.fetchone()
    return row["id"] if row else None


def validate_set(source: dict, frames: dict) -> int:
    """Kiểm bộ ảnh đủ và đúng key/encoding/spec cho `source`; trả tổng byte."""
    bad = ServiceError("result_invalid", "bộ ảnh của worker không hợp lệ", 422)
    set_id, items, n = UUID(frames["frame_set_id"]), frames["items"], frames["n_units"]
    if frames.get("encoding_version") != ENCODING_VERSION or frames.get("sample_spec_hash") != DEFAULT_SAMPLE_SPEC["spec_hash"]:
        raise bad
    if n != (source["metadata"] or {}).get("n_units") or len(items) != 2 * n:
        raise bad
    marker = (f"/private/{source['owner_id']}/frames/{set_id}/" if source["scope"] == "PRIVATE"
              else f"/frames/{source['core_source_id']}/{set_id}/")
    pairs = {(i["variant"], i["frame_idx"]) for i in items}
    if len(pairs) != len(items) or {v for v, _ in pairs} != set(VARIANTS) or len({i for _, i in pairs}) != n:
        raise bad
    for i in items:
        if (not _SHA.fullmatch(i["sha256"]) or i["size_bytes"] <= 0 or marker not in i["object_key"]
                or not i["object_key"].endswith(f"/{i['frame_idx']}/{i['variant'].lower()}/{i['sha256']}.jpg")):
            raise bad
    total = sum(i["size_bytes"] for i in items)
    if total != frames["total_bytes"]:
        raise bad
    return total


def insert_set_in_tx(cur, source: dict, frames: dict, job_id: UUID | None, attempt_id: UUID | None) -> UUID:
    """Chèn frame set READY + toàn bộ frame rows (caller đã `validate_set` và kiểm chưa có bộ READY)."""
    set_id, items = UUID(frames["frame_set_id"]), frames["items"]
    cur.execute("INSERT INTO rav_pilot_frame_set (id, scope, owner_id, source_id, raw_sha256, sample_spec_hash, encoding_version, "
                "n_units, object_count, total_bytes, job_id, attempt_id, state) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'READY')",
                (set_id, source["scope"], source["owner_id"], source["id"], source["raw_sha256"], DEFAULT_SAMPLE_SPEC["spec_hash"],
                 ENCODING_VERSION, frames["n_units"], len(items), frames["total_bytes"], job_id, attempt_id))
    cur.executemany("INSERT INTO rav_pilot_frame (id, frame_set_id, variant, frame_idx, unit_id, object_key, sha256, size_bytes, "
                    "width, height) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(uuid4(), set_id, i["variant"], i["frame_idx"], i["unit_id"], i["object_key"], i["sha256"], i["size_bytes"],
                      i["width"], i["height"]) for i in items])
    return set_id


def publish_set_in_tx(cur, job: dict, lease, source: dict, frames: dict) -> UUID | None:
    """Chèn frame set đủ bộ cho `source` + ghi persisted đúng tổng byte một lần. Nguồn đã có bộ READY (race/dedup) → None:
    receipt của bộ này không được nhận, cleanup xóa object rồi mới trả dung lượng (§21.13)."""
    total = validate_set(source, frames)
    if ready_set_in_tx(cur, source["id"], source["raw_sha256"]):
        return None
    set_id = insert_set_in_tx(cur, source, frames, job["id"], lease.attempt_id)
    pilot_storage.persist_job_bytes(cur, job["id"], total)
    return set_id


# ------------------------------------------------------------------------------
# PREPARE_FRAMES: chuẩn bị ảnh cho nguồn đã có (F2) — payload đóng băng lúc enqueue, hook publish/finish của runtime
# ------------------------------------------------------------------------------
def prepare_payload(src: dict, frame_bounds: dict, *, owner_id: UUID | None = None) -> dict:
    meta = src["metadata"] or {}
    return {"source_id": str(src["id"]), "owner_id": str(owner_id or src["owner_id"]), "target_scope": src["scope"],
            "core_source_id": src["core_source_id"], "raw_key": src["raw_key"], "raw_sha256": src["raw_sha256"],
            "resource_version": src["resource_version"], "n_units": meta.get("n_units"),
            "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"], "frames": frame_bounds}


def publish_in_tx(cur, job: dict, lease, outputs: dict) -> None:
    """Gắn bộ ảnh vào nguồn nếu nguồn vẫn READY, đúng phạm vi/owner và đúng nội dung; không đổi `resource_version`."""
    p = job["payload"]
    if p.get('sample_copy'):
        from .pilot_sample_copy import publish_in_tx as publish_copy
        return publish_copy(cur, job, lease, outputs)
    target_scope = p.get("target_scope") or "PRIVATE"
    cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id = %s FOR UPDATE", (UUID(p["source_id"]),))
    src = cur.fetchone()
    scope_ok = bool(src) and ((src["scope"] == "SYSTEM") if target_scope == "SYSTEM" else (src["scope"] == "PRIVATE" and src["owner_id"] == job["owner_id"]))
    if not src or src["availability"] != "READY" or not scope_ok or src["raw_sha256"] != p["raw_sha256"]:
        raise ServiceError("source_unavailable", "nguồn đã đổi trong lúc chuẩn bị ảnh", 409)
    publish_set_in_tx(cur, job, lease, src, outputs["frames"])


def finish_in_tx(cur, job: dict, state: str, error: dict | None) -> None:
    """Không có bản ghi phụ: trạng thái chuẩn bị đọc thẳng từ frame set/job."""


def accept_in_tx(cur, lease) -> None:
    """ACCEPT receipt của mọi ảnh trong frame set do attempt này publish; thiếu/lệch receipt → 422."""
    cur.execute("SELECT count(*) AS n FROM rav_pilot_frame f JOIN rav_pilot_frame_set s ON s.id = f.frame_set_id "
                "WHERE s.attempt_id = %s", (lease.attempt_id,))
    expected = cur.fetchone()["n"]
    if not expected:
        return
    cur.execute("UPDATE rav_pilot_worker_object o SET state = 'ACCEPTED', updated_at = now() "
                "FROM rav_pilot_frame f JOIN rav_pilot_frame_set s ON s.id = f.frame_set_id "
                "WHERE s.attempt_id = %s AND o.attempt_id = %s AND o.object_key = f.object_key AND o.sha256 = f.sha256 "
                "AND o.size_bytes = f.size_bytes AND o.state IN ('INTENT', 'WRITTEN', 'ACCEPTED')", (lease.attempt_id, lease.attempt_id))
    if cur.rowcount != expected:
        raise ServiceError("result_invalid", "ảnh không có receipt của attempt", 422)
