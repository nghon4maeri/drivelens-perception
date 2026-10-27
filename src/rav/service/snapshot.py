"""Snapshot bất biến của collection (tập kept) — CONTRACTS §14.2, #64.

Chỉ lưu tham chiếu tới raw (video id + `sha256` đủ, chỉ số frame, cấu hình decoder trong `context.sample_spec`) kèm provenance quyết định;
**không đọc / ghi pixel**. Ảnh chỉ được đọc khi export theo snapshot (S8).
`is_current` = cả `members_hash` lẫn `context_hash` khớp collection + context hiện tại; khả dụng của nguồn là câu hỏi riêng (S8).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from ..core.execution import spec_hash
from ..core.registry import REGISTRY, discover
from ..core.types import ReviewState
from ..store.db import SCHEMA_VERSION, SnapshotConflictError
from . import App, ServiceError
from .review_state import ACTOR, _feedback
from .session import get_session, make_context

MEMBERS_PREFIX = "rav-collection/1\n"


def members_hash(unit_ids) -> str:
    return hashlib.sha256((MEMBERS_PREFIX + "\n".join(sorted(unit_ids))).encode()).hexdigest()


def context_of(spec: dict) -> dict:
    """Context đóng băng (format 1). `app` không vào `context_hash` (nâng schema không làm snapshot cũ "không hiện hành")."""
    discover()
    decoder, units = REGISTRY.build("decoder", spec["decoder"]), REGISTRY.build("unit", spec["units"])
    return {"format": 1,
            "session": {"goal": spec["goal"], "target_k": spec["target_k"], "pool": spec["pool"],
                        "reference": spec.get("reference", {"dataset_version_ids": []})},
            "sample_spec": {"decoder": spec["decoder"], "units": spec["units"], "spec_hash": spec_hash(decoder, units)},
            "app": {"schema_version": SCHEMA_VERSION}}


def context_hash(context: dict) -> str:
    doc = {k: v for k, v in context.items() if k != "app"}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=256)
def _file_sha256(uri: str, size: int, mtime_ns: int) -> str:
    """sha256 đủ của file video; cache theo (đường dẫn, kích thước, mtime) để không băm lại mỗi lần chốt."""
    digest = hashlib.sha256()
    with open(uri, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kept(app: App, row: dict) -> tuple[set[str], dict[str, tuple[int, object]], int]:
    """kept hiện tại, quyết định keep cuối của mỗi unit kept (seq, Decision), seq lớn nhất của log."""
    rows = app.db.decision_rows(row["id"])
    state = _feedback(row).apply([d for _, d in rows], ReviewState())
    last_keep = {d.unit_id: (seq, d) for seq, d in rows if d.action == "keep"}
    return state.kept, last_keep, max((seq for seq, _ in rows), default=0)


def create_snapshot(app: App, sid: str, note: str | None = None) -> tuple[dict, bool]:
    row = get_session(app, sid)
    kept, last_keep, seq = _kept(app, row)
    if not kept:
        raise ServiceError("empty_collection", "collection rỗng (chưa keep frame nào) — không có gì để chốt")
    ctx = make_context(app, row)
    recipes = {p["n"]: p["input"] for p in app.db.proposals(sid)}
    members = []
    for u in ctx.units:                                  # thứ tự pool (video_id, t0)
        if u.id not in kept:
            continue
        video = ctx.videos[u.video_id]
        dseq, d = last_keep[u.id]
        inp = recipes.get(d.proposal_n)
        members.append({"unit_id": u.id, "source_kind": "video", "source_id": u.video_id, "provenance": {
            "video": {"id": video.id, "name": video.meta.get("name"), "size_bytes": video.meta.get("size_bytes"),
                      "sha256": _file_sha256(video.uri, video.meta.get("size_bytes", 0), video.meta.get("mtime_ns", 0))},
            "t0": u.t0, "t1": u.t1, "frame_idx": u.anchor.idx,
            "decision": {"seq": dseq, "actor": d.actor, "decided_at": d.decided_at.isoformat(), "proposal_n": d.proposal_n},
            "proposal": {"recipe": inp["recipe"], "config_hash": inp["config_hash"]} if inp else None}})
    context = context_of(row["spec"])
    snap = {"id": uuid.uuid4().hex, "session_id": sid, "created_at": datetime.now(UTC).isoformat(), "created_by": ACTOR,
            "note": note, "decision_seq": seq, "members_hash": members_hash(kept), "context": context,
            "context_hash": context_hash(context)}
    try:
        saved, created = app.db.add_snapshot(snap, members)
    except SnapshotConflictError as e:
        raise ServiceError("collection_changed", f"collection đổi trong lúc chốt, thử lại: {e}", 409) from None
    return with_status(app, saved, row), created


def with_status(app: App, snap: dict, row: dict | None = None) -> dict:
    """Thêm `is_current`, `decisions_after` (so với collection + context hiện tại của phiên)."""
    row = row or get_session(app, snap["session_id"])
    kept, _, seq = _kept(app, row)
    current = snap["members_hash"] == members_hash(kept) and snap["context_hash"] == context_hash(context_of(row["spec"]))
    return {**snap, "is_current": current, "decisions_after": seq - snap["decision_seq"]}


def list_snapshots(app: App, sid: str) -> list[dict]:
    row = get_session(app, sid)
    return [with_status(app, s, row) for s in app.db.snapshots(sid)]


def get_snapshot(app: App, snapshot_id: str) -> tuple[dict, list[dict]]:
    snap = app.db.snapshot(snapshot_id)
    if snap is None:
        raise ServiceError("snapshot_not_found", f"không có snapshot {snapshot_id!r}", 404)
    return with_status(app, snap), app.db.snapshot_members(snapshot_id)
