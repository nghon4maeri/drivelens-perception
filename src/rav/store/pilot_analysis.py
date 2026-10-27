"""YOLO trên đợt đề xuất + phân tích phiên ở pilot (CONTRACTS §17, §21.17, #97).

- Model: chỉ SYSTEM do operator import (`scripts/pilot_import_model.py`); YOLO chỉ chạy với model `INFERENCE_VERIFIED`, task detect.
- `request_detections`: evidence cùng proposal/model/runtime đã có → trả luôn; chưa có → enqueue `YOLO_BATCH` (queue CPU).
- `request_analysis`: kết quả cùng input + `decision_seq` + `proposal_count` đã có → trả luôn; chưa có → enqueue `METRICS`. GET không tự tính.
- `publish_in_tx` (hook runtime): ghi evidence / kết quả phân tích (bất biến); không đổi proposal, state hay scoring.
Chỉ DB, import nhẹ (API dùng được).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from ..actor import ActorContext
from ..errors import ServiceError
from .pilot_curation import freeze_session_in_tx
from .pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB, _In, canonical_hash, require_user
from .pilot_runtime import EnqueueSpec, JobDTO, PilotRuntime, _job_dto

DEFAULT_VIEWS = [{"type": "embedding_map", "params": {}}, {"type": "timeline", "params": {}}, {"type": "duplicate_pairs", "params": {}}]
MAX_YOLO_PICKS = 256


class ModelDTO(BaseModel):
    id: UUID
    name: str
    description: str = ""
    published: bool = True
    sha256: str
    size_bytes: int
    task: str
    class_map: dict[str, Any]
    validation: str
    checkpoint_revision: str
    provenance: dict[str, Any]
    created_at: str


class DetectIn(_In):
    model_id: UUID


class DetectionsDTO(BaseModel):
    evidence: dict[str, Any] | None
    job: JobDTO | None


class MetricIn(_In):
    type: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)


class AnalysisIn(_In):
    expected_state_version: int = Field(ge=1)
    metrics: list[MetricIn] | None = Field(default=None, max_length=20)
    on: Literal["kept"] | int = "kept"

    @field_validator("on")
    @classmethod
    def _on(cls, v):
        if isinstance(v, int) and v < 1:
            raise ValueError("on phải là 'kept' hoặc số đợt ≥ 1")
        return v


class AnalysisDTO(BaseModel):
    id: UUID
    session_id: UUID
    job_id: UUID
    input_hash: str
    input: dict[str, Any]
    decision_seq: int
    state_version: int
    proposal_count: int
    results: dict[str, Any]
    created_at: str
    current: bool


class AnalysisRequestDTO(BaseModel):
    analysis: AnalysisDTO | None
    job: JobDTO | None


def analysis_input(inp: AnalysisIn | None) -> dict:
    if inp is None or inp.metrics is None:
        return {"metrics": None, "on": inp.on if inp else "kept", "views": DEFAULT_VIEWS}
    return {"metrics": [m.model_dump() for m in inp.metrics], "on": inp.on, "views": []}


def _model_dto(r: dict) -> ModelDTO:
    return ModelDTO(id=r["id"], name=r["name"], description=r.get("description") or "", published=bool(r.get("published", True)),
                    sha256=r["sha256"], size_bytes=r["size_bytes"], task=r["task"], class_map=r["class_map"] or {},
                    validation=r["validation"], checkpoint_revision=r["checkpoint_revision"], provenance=r["provenance"] or {},
                    created_at=r["created_at"].isoformat())


def _analysis_dto(r: dict, current: tuple[int, int]) -> AnalysisDTO:
    return AnalysisDTO(id=r["id"], session_id=r["session_id"], job_id=r["job_id"], input_hash=r["input_hash"], input=r["input"],
                       decision_seq=r["decision_seq"], state_version=r["state_version"], proposal_count=r["proposal_count"],
                       results=r["results"], created_at=r["created_at"].isoformat(),
                       current=(r["decision_seq"], r["proposal_count"]) == current)


class PilotAnalysis:
    def __init__(self, db: PilotDB, runtime: PilotRuntime | None = None):
        self.db, self.runtime = db, runtime or PilotRuntime(db)

    # ---- model SYSTEM ----
    def list_models(self, actor: ActorContext, include_unpublished: bool = False) -> list[ModelDTO]:
        uid = require_user(actor)
        if include_unpublished:
            from .pilot_operations import operations_read
            if not operations_read(uid):
                raise ServiceError("forbidden", "không có quyền xem model mẫu chưa xuất bản", 403)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            if include_unpublished:
                cur.execute("SELECT * FROM rav_pilot_model_asset ORDER BY created_at, id")
            else:
                cur.execute("SELECT * FROM rav_pilot_model_asset WHERE published = true ORDER BY created_at, id")
            return [_model_dto(r) for r in cur.fetchall()]

    # ---- YOLO trên đợt ----
    def request_detections(self, actor: ActorContext, session_id: UUID, n: int, inp: DetectIn, idempotency_key: str) -> tuple[DetectionsDTO, int]:
        uid = require_user(actor)
        key = f"detect:{idempotency_key}"
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_job WHERE owner_id = %s AND kind = 'YOLO_BATCH' AND idempotency_key = %s", (uid, key))
            if old := cur.fetchone():
                cur.execute("SELECT evidence FROM rav_pilot_proposal_evidence WHERE job_id = %s", (old["id"],))
                ev = cur.fetchone()
                return DetectionsDTO(evidence=ev["evidence"] if ev else None, job=_job_dto(old)), 200 if ev else 202
            cur.execute("SELECT p.* FROM rav_pilot_proposal p JOIN rav_pilot_session s ON s.id = p.session_id "
                        "WHERE p.session_id = %s AND p.n = %s AND s.owner_id = %s", (session_id, n, uid))
            prop = cur.fetchone()
            if not prop:
                raise ServiceError("not_found", f"phiên {session_id}: không có đợt {n}", 404)
            picks = prop["picks"]
            if not 1 <= len(picks) <= MAX_YOLO_PICKS:
                raise ServiceError("invalid_yolo_batch", f"YOLO cần 1–{MAX_YOLO_PICKS} ảnh trong đợt", 422)
            cur.execute("SELECT * FROM rav_pilot_model_asset WHERE id = %s", (inp.model_id,))
            model = cur.fetchone()
            if not model or not self.db.can_read_model_in_tx(cur, uid, model):
                raise ServiceError("not_found", f"không có model {inp.model_id}", 404)
            if model["validation"] != "INFERENCE_VERIFIED" or model["task"] != "detect":
                raise ServiceError("model_not_verified", "model chưa được operator xác minh chạy detection", 422)
            runtime = model["runtime_manifest"] or {}
            fingerprint = canonical_hash({"engine_version": runtime.get("engine_version"), "params": runtime.get("params"),
                                          "model_sha256": model["sha256"], "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"],
                                          "unit_ids": [k["unit_id"] for k in picks]})
            cur.execute("SELECT evidence FROM rav_pilot_proposal_evidence WHERE proposal_id = %s AND model_id = %s AND runtime_fingerprint = %s",
                        (prop["id"], model["id"], fingerprint))
            if ev := cur.fetchone():
                return DetectionsDTO(evidence=ev["evidence"], job=None), 200
            source_ids = sorted({UUID(k["source_id"]) for k in picks})
            sources = self.db.lock_sources(cur, uid, source_ids)
            pool = []
            for sid in source_ids:
                s = sources[sid]
                if s["availability"] != "READY":
                    raise ServiceError("source_unavailable", f"nguồn {sid} đang {s['availability']}", 409)
                pool.append({"source_id": str(sid), "core_source_id": s["core_source_id"], "raw_key": s["raw_key"],
                             "raw_sha256": s["raw_sha256"], "n_units": (s["metadata"] or {}).get("n_units"), "features": {}})
            payload = {"session_id": str(session_id), "proposal_id": str(prop["id"]), "proposal_n": n,
                       "unit_ids": [k["unit_id"] for k in picks], "pool": pool,
                       "spec": {k: DEFAULT_SAMPLE_SPEC[k] for k in ("decoder", "units")}, "spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"],
                       "model": {"id": str(model["id"]), "core_model_id": model["core_model_id"], "name": model["name"],
                                 "raw_key": model["raw_key"], "sha256": model["sha256"], "size_bytes": model["size_bytes"]},
                       "runtime": runtime, "runtime_fingerprint": fingerprint}
            job = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(kind="YOLO_BATCH", resource_id=prop["id"], idempotency_key=key,
                                                                   payload=payload), source_ids)
            return DetectionsDTO(evidence=None, job=job), 202

    def get_detections(self, actor: ActorContext, session_id: UUID, n: int, model_id: UUID) -> dict:
        uid = require_user(actor)
        with self.db.as_user(uid) as cur:
            cur.execute("SELECT e.evidence FROM rav_pilot_proposal_evidence e JOIN rav_pilot_proposal p ON p.id = e.proposal_id "
                        "WHERE p.session_id = %s AND p.n = %s AND e.model_id = %s ORDER BY e.created_at DESC LIMIT 1",
                        (session_id, n, model_id))
            row = cur.fetchone()
        if not row:
            raise ServiceError("not_found", "chưa có kết quả YOLO cho đợt/model này", 404)
        return row["evidence"]

    # ---- phân tích ----
    def request_analysis(self, actor: ActorContext, session_id: UUID, inp: AnalysisIn, idempotency_key: str) -> tuple[AnalysisRequestDTO, int]:
        uid = require_user(actor)
        key = f"analysis:{idempotency_key}"
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_job WHERE owner_id = %s AND kind = 'METRICS' AND idempotency_key = %s", (uid, key))
            if old := cur.fetchone():
                cur.execute("SELECT * FROM rav_pilot_analysis WHERE job_id = %s", (old["id"],))
                row = cur.fetchone()
                dto = None
                if row:
                    cur.execute("SELECT coalesce(max(seq), 0) AS s FROM rav_pilot_decision WHERE session_id=%s", (row["session_id"],))
                    seq = cur.fetchone()["s"]
                    cur.execute("SELECT count(*) AS n FROM rav_pilot_proposal WHERE session_id=%s", (row["session_id"],))
                    dto = _analysis_dto(row, (seq, cur.fetchone()["n"]))
                return AnalysisRequestDTO(analysis=dto, job=_job_dto(old)), 200 if row else 202
            spec = analysis_input(inp)
            ih = canonical_hash(spec)
            base, ordered = freeze_session_in_tx(self.db, self.runtime, cur, uid, session_id, inp.expected_state_version, "METRICS")
            if isinstance(inp.on, int) and inp.on > base["proposal_count"]:
                raise ServiceError("not_found", f"phiên chưa có đợt {inp.on}", 404)
            cur.execute("SELECT * FROM rav_pilot_analysis WHERE session_id = %s AND input_hash = %s AND decision_seq = %s AND proposal_count = %s",
                        (session_id, ih, base["decision_seq"], base["proposal_count"]))
            if row := cur.fetchone():
                return AnalysisRequestDTO(analysis=_analysis_dto(row, (base["decision_seq"], base["proposal_count"])), job=None), 200
            job = self.runtime.enqueue_in_tx(cur, uid, EnqueueSpec(kind="METRICS", resource_id=session_id, idempotency_key=key,
                                                                   payload={**base, "input": spec, "input_hash": ih}), ordered)
            return AnalysisRequestDTO(analysis=None, job=job), 202

    def get_analysis(self, actor: ActorContext, session_id: UUID, input_hash: str | None = None) -> AnalysisDTO:
        uid = require_user(actor)
        ih = input_hash or canonical_hash(analysis_input(None))
        with self.db.as_user(uid) as cur:
            cur.execute("SELECT 1 FROM rav_pilot_session WHERE id = %s", (session_id,))
            if not cur.fetchone():
                raise ServiceError("not_found", f"không tìm thấy phiên {session_id}", 404)
            cur.execute("SELECT * FROM rav_pilot_analysis WHERE session_id = %s AND input_hash = %s ORDER BY created_at DESC LIMIT 1",
                        (session_id, ih))
            row = cur.fetchone()
            if not row:
                raise ServiceError("not_found", "chưa có kết quả phân tích; bấm Tính phân tích", 404)
            cur.execute("SELECT coalesce(max(seq), 0) AS s FROM rav_pilot_decision WHERE session_id = %s", (session_id,))
            seq = cur.fetchone()["s"]
            cur.execute("SELECT count(*) AS n FROM rav_pilot_proposal WHERE session_id = %s", (session_id,))
            return _analysis_dto(row, (seq, cur.fetchone()["n"]))


# ------------------------------------------------------------------------------
# Hook runtime (cùng transaction publish/finish)
# ------------------------------------------------------------------------------
def publish_in_tx(cur, job: dict, lease, outputs: dict) -> None:
    from psycopg.types.json import Jsonb

    p = job["payload"]
    if job["kind"] == "YOLO_BATCH":
        d = outputs.get("detections") or {}
        items = d.get("items")
        if not isinstance(items, list) or [i.get("unit_id") for i in items] != p["unit_ids"] or d.get("model_sha256") != p["model"]["sha256"]:
            raise ServiceError("result_invalid", "kết quả YOLO không khớp đợt/model đã đóng băng", 422)
        cur.execute("SELECT sha256, validation FROM rav_pilot_model_asset WHERE id = %s", (UUID(p["model"]["id"]),))
        model = cur.fetchone()
        if not model or model["sha256"] != p["model"]["sha256"] or model["validation"] != "INFERENCE_VERIFIED":
            raise ServiceError("stale_input", "model đã đổi trong lúc chạy", 409)
        cur.execute("INSERT INTO rav_pilot_proposal_evidence (id, proposal_id, session_id, owner_id, model_id, job_id, runtime_fingerprint, evidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (proposal_id, model_id, runtime_fingerprint) DO NOTHING",
                    (uuid4(), UUID(p["proposal_id"]), UUID(p["session_id"]), job["owner_id"], UUID(p["model"]["id"]), job["id"],
                     p["runtime_fingerprint"], Jsonb({**d, "session_id": p["session_id"], "proposal_n": p["proposal_n"]})))
        return None
    if job["kind"] == "METRICS":
        res = outputs.get("analysis")
        if not isinstance(res, dict) or "results" not in res:
            raise ServiceError("result_invalid", "kết quả phân tích thiếu", 422)
        cur.execute("INSERT INTO rav_pilot_analysis (id, session_id, owner_id, job_id, input_hash, input, decision_seq, state_version, "
                    "proposal_count, results) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (session_id, input_hash, decision_seq, proposal_count) DO NOTHING",
                    (uuid4(), UUID(p["session_id"]), job["owner_id"], job["id"], p["input_hash"], Jsonb(p["input"]),
                     p["decision_seq"], p["state_version"], p["proposal_count"], Jsonb(res)))
        return None
    raise ServiceError("result_invalid", f"hook phân tích không xử lý {job['kind']}", 500)


def finish_in_tx(cur, job: dict, state: str, error: dict | None) -> None:
    """Không có bản ghi phụ cần đóng."""
