"""Phiên mẫu SYSTEM chỉ đọc và "tạo phiên riêng từ mẫu" (CONTRACTS §21.10, §21.12; identity spec §8.1, §9).

- `list`/`get`: mọi tài khoản active đọc mẫu; lịch sử trả projection đã lọc (không payload thô, không path).
- `fork`: chỉ mẫu VERIFIED; tạo phiên PRIVATE **trống** bằng cùng lõi `create_session` (cùng nguồn/cấu hình khởi tạo),
  receipt `FORK_TEMPLATE` idempotent; không chép đề xuất/quyết định/snapshot, không enqueue.
- Operator: `project_events` dựng sự kiện mẫu từ hàng SQLite local; `apply_plan` ghi nguồn SYSTEM + mẫu + receipt trong một transaction
  (S3 do script ghi và xác minh trước). Chạy lại cùng `inventory_hash` → no-op.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..actor import ActorContext
from ..errors import ServiceError
from .pilot_db import CreateSessionInput, PilotDB, SessionDTO, canonical_hash, require_user


class TemplateCounts(BaseModel):
    proposals: int
    decisions: int
    snapshots: int


class TemplateDTO(BaseModel):
    id: UUID
    template_key: str
    name: str
    availability: str
    reason_code: str | None
    goal: str
    target_k: int
    source_ids: list[UUID]
    counts: TemplateCounts


class TemplateEventDTO(BaseModel):
    ord: int
    kind: str
    payload: dict[str, Any]


class TemplateDetailDTO(TemplateDTO):
    history: list[TemplateEventDTO]


class ForkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)


class ForkReceipt(BaseModel):
    session: SessionDTO
    template_id: UUID
    counts: TemplateCounts
    replayed: bool


_LIST = ("SELECT t.id, t.template_key, t.name, t.frozen_spec, t.source_mapping, s.availability, s.reason_code, "
         "(SELECT count(*) FROM rav_pilot_demo_template_event e WHERE e.template_id = t.id AND e.kind = 'PROPOSAL') AS proposals, "
         "(SELECT count(*) FROM rav_pilot_demo_template_event e WHERE e.template_id = t.id AND e.kind = 'DECISION') AS decisions, "
         "(SELECT count(*) FROM rav_pilot_demo_template_event e WHERE e.template_id = t.id AND e.kind = 'SNAPSHOT') AS snapshots "
         "FROM rav_pilot_demo_template t JOIN rav_pilot_demo_template_status s ON s.template_id = t.id")


def _dto(row: dict) -> TemplateDTO:
    spec = row["frozen_spec"]
    return TemplateDTO(id=row["id"], template_key=row["template_key"], name=row["name"], availability=row["availability"],
                       reason_code=row["reason_code"], goal=spec["goal"], target_k=spec["target_k"],
                       source_ids=[UUID(m["source_id"]) for m in row["source_mapping"]],
                       counts=TemplateCounts(proposals=row["proposals"], decisions=row["decisions"], snapshots=row["snapshots"]))


class PilotTemplates:
    def __init__(self, db: PilotDB):
        self.db = db

    def _active(self, cur, uid: UUID) -> None:
        cur.execute("SELECT active FROM rav_pilot_profile WHERE user_id = %s", (uid,))
        row = cur.fetchone()
        if not row:
            raise ServiceError("unauthorized", "tài khoản chưa được cấp quyền dùng pilot", 401)
        if not row["active"]:
            raise ServiceError("account_disabled", "tài khoản đã bị vô hiệu hóa", 401)

    def list(self, actor: ActorContext) -> list[TemplateDTO]:
        uid = require_user(actor)
        with self.db.backend() as cur:
            self._active(cur, uid)
            cur.execute(_LIST + " ORDER BY t.created_at, t.template_key")
            return [_dto(r) for r in cur.fetchall()]

    def get(self, actor: ActorContext, template_id: UUID) -> TemplateDetailDTO:
        uid = require_user(actor)
        with self.db.backend() as cur:
            self._active(cur, uid)
            cur.execute(_LIST + " WHERE t.id = %s", (template_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy mẫu {template_id}", 404)
            cur.execute("SELECT ord, kind, legacy_payload FROM rav_pilot_demo_template_event WHERE template_id = %s ORDER BY ord",
                        (template_id,))
            history = [TemplateEventDTO(ord=e["ord"], kind=e["kind"], payload=e["legacy_payload"]) for e in cur.fetchall()]
        return TemplateDetailDTO(**_dto(row).model_dump(), history=history)

    def fork(self, actor: ActorContext, template_id: UUID, inp: ForkInput, idempotency_key: str) -> ForkReceipt:
        uid = require_user(actor)
        input_hash = canonical_hash({"template_id": str(template_id), "input": inp.model_dump()})
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute(_LIST + " WHERE t.id = %s", (template_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", f"không tìm thấy mẫu {template_id}", 404)
            template = _dto(row)
            sources = self.db.lock_sources(cur, uid, template.source_ids, require_published_system=True)
            cur.execute("SELECT * FROM rav_pilot_demo_template_status WHERE template_id = %s FOR SHARE", (template_id,))
            if (old := self.db.receipt(cur, uid, "FORK_TEMPLATE", idempotency_key, input_hash)) is not None:
                return ForkReceipt(**{**old, "replayed": True})
            if template.availability != "VERIFIED":
                raise ServiceError("template_unavailable", f"mẫu đang {template.availability}", 409)
            # Chỉ lấy cấu hình khởi tạo của mẫu; không lấy review state, đề xuất hay kết quả (#83).
            session = self.db.create_session_in_tx(cur, uid, CreateSessionInput(
                name=inp.name or template.name, goal=template.goal, target_k=template.target_k,
                pool_source_ids=template.source_ids, reference=None), sources, template_id)
            receipt = ForkReceipt(session=session, template_id=template_id,
                                  counts=TemplateCounts(proposals=0, decisions=0, snapshots=0), replayed=False)
            self.db.write_receipt(cur, uid, "FORK_TEMPLATE", idempotency_key, input_hash, receipt)
            return receipt


# ------------------------------------------------------------------------------
# Operator: projection sự kiện và áp kế hoạch import
# ------------------------------------------------------------------------------
def project_events(proposals: list[dict], decisions: list[dict], snapshots: list[dict]) -> list[dict]:
    """Hàng SQLite local → sự kiện mẫu đã lọc, sắp theo thời gian (đề xuất trước quyết định cùng thời điểm)."""
    events = []
    for p in proposals:
        snap = p["snapshot"]
        inp = snap.get("input", snap)
        events.append((p["created_at"], 0, f"proposal:{p['n']}", "PROPOSAL", {
            "n": p["n"], "recipe": (inp.get("config") or {}).get("name") or inp.get("recipe"),
            "batch_size": inp.get("batch_size"), "picks": [x["unit_id"] for x in p["picks"]], "created_at": p["created_at"]}))
    for d in decisions:
        events.append((d["decided_at"], 1, f"decision:{d['seq']}", "DECISION", {
            "seq": d["seq"], "unit_id": d["unit_id"], "action": d["action"], "actor": d["actor"],
            "proposal_n": d["proposal_n"], "decided_at": d["decided_at"], "reason": d["reason"]}))
    for s in snapshots:
        events.append((s["created_at"], 2, f"snapshot:{s['n']}", "SNAPSHOT", {
            "n": s["n"], "n_members": s["n_members"], "members_hash": s["members_hash"], "decision_seq": s["decision_seq"],
            "created_by": s["created_by"], "created_at": s["created_at"]}))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    return [{"source_event_id": eid, "ord": i, "kind": kind, "payload": payload, "payload_hash": canonical_hash(payload)}
            for i, (_, _, eid, kind, payload) in enumerate(events)]


def apply_plan(cur, plan: dict, inventory_hash: str, operator_id: UUID | None = None) -> dict:
    """Ghi nguồn SYSTEM + mẫu + sự kiện + trạng thái + receipt (cursor role operator, một transaction). Trả tóm tắt."""
    from psycopg.types.json import Jsonb

    key = f"import:{plan['seed_version']}"
    cur.execute("SELECT * FROM rav_pilot_import_receipt WHERE idempotency_key = %s FOR UPDATE", (key,))
    if old := cur.fetchone():
        if old["inventory_hash"] != inventory_hash or old["state"] != "COMMITTED":
            raise ServiceError("idempotency_conflict", "seed_version đã import với inventory khác; dùng seed_version mới", 409)
        return {"replayed": True, **old["counts"]}
    for s in plan["sources"]:
        cur.execute("INSERT INTO rav_pilot_source_asset (id, core_source_id, kind, scope, owner_id, protected, name, raw_key, "
                    "raw_sha256, size_bytes, provenance, metadata, availability) "
                    "VALUES (%s, %s, 'VIDEO', 'SYSTEM', NULL, true, %s, %s, %s, %s, %s, %s, 'READY') ON CONFLICT (id) DO NOTHING",
                    (s["id"], s["core_source_id"], s["name"], s["raw_key"], s["sha256"], s["size_bytes"],
                     Jsonb({"seed_version": plan["seed_version"]}), Jsonb(s["metadata"])))
        cur.execute("SELECT raw_sha256, raw_key FROM rav_pilot_source_asset WHERE id = %s", (s["id"],))
        row = cur.fetchone()
        if (row["raw_sha256"], row["raw_key"]) != (s["sha256"], s["raw_key"]):
            raise ServiceError("idempotency_conflict", f"nguồn {s['core_source_id']} đã có với nội dung khác", 409)
    for f in plan.get("features", []):   # đặc trưng SYSTEM do operator import (§21.14)
        cur.execute("INSERT INTO rav_pilot_artifact (id, owner_id, scope, source_id, kind, variant, input_hash, object_key, sha256, "
                    "size_bytes, state, manifest) VALUES (%s, NULL, 'SYSTEM', %s, 'FEATURE', %s, %s, %s, %s, %s, 'READY', %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (f["id"], f["source_id"], f["feature"], f["input_hash"], f["object_key"], f["sha256"], f["size_bytes"],
                     Jsonb(f["manifest"])))
        cur.execute("SELECT sha256, object_key FROM rav_pilot_artifact WHERE id = %s", (f["id"],))
        row = cur.fetchone()
        if (row["sha256"], row["object_key"]) != (f["sha256"], f["object_key"]):
            raise ServiceError("idempotency_conflict", f"đặc trưng {f['core_source_id']}:{f['feature']} đã có với nội dung khác", 409)
    n_events = 0
    for t in plan["templates"]:
        cur.execute("INSERT INTO rav_pilot_demo_template (id, template_key, name, legacy_session_id, manifest_hash, frozen_spec, "
                    "source_mapping, provenance) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (t["id"], t["template_key"], t["name"], t["legacy_session_id"], t["manifest_hash"], Jsonb(t["frozen_spec"]),
                     Jsonb(t["source_mapping"]), Jsonb({"seed_version": plan["seed_version"]})))
        for e in t["events"]:
            cur.execute("INSERT INTO rav_pilot_demo_template_event (template_id, source_event_id, ord, kind, legacy_payload, "
                        "payload_hash) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (t["id"], e["source_event_id"], e["ord"], e["kind"], Jsonb(e["payload"]), e["payload_hash"]))
        n_events += len(t["events"])
        cur.execute("SELECT count(*) AS n FROM rav_pilot_source_asset WHERE id = ANY(%s) AND availability = 'READY' "
                    "AND scope = 'SYSTEM'", ([m["source_id"] for m in t["source_mapping"]],))
        ok = cur.fetchone()["n"] == len(t["source_mapping"])
        cur.execute("INSERT INTO rav_pilot_demo_template_status (template_id, availability, reason_code) VALUES (%s, %s, %s) "
                    "ON CONFLICT (template_id) DO NOTHING",
                    (t["id"], "VERIFIED" if ok else "BLOCKED", None if ok else "source_unavailable"))
    counts = {"sources": len(plan["sources"]), "templates": len(plan["templates"]), "events": n_events}
    if "features" in plan:
        counts["features"] = len(plan["features"])
    cur.execute("INSERT INTO rav_pilot_import_receipt (id, idempotency_key, inventory_hash, mapping, counts, hashes, state, operator_id) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, 'COMMITTED', %s)",
                (key, inventory_hash, Jsonb({t["template_key"]: str(t["id"]) for t in plan["templates"]}), Jsonb(counts),
                 Jsonb({s["core_source_id"]: s["sha256"] for s in plan["sources"]}), operator_id))
    return {"replayed": False, **counts}
