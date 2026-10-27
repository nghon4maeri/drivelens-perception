"""Route `/api/v2` của pilot (CONTRACTS §21.4, §21.11; identity spec §8; runtime spec §4.1 — phần thuộc DEP2/DEP3).

Mỗi route: xác thực → ActorContext → gọi một hàm adapter (`PilotDB`/`PilotRuntime`); không logic nghiệp vụ ở đây.
POST tạo tài nguyên bắt buộc header `Idempotency-Key`; body `extra="forbid"`, không nhận owner/actor.
Upload trực tiếp, media riêng tư, phiên mẫu: DEP4 (CONTRACTS §21.12). Chuẩn bị đặc trưng: DEP5a (§21.14). Đề xuất trên worker: DEP5b (§21.15). YOLO trên đợt + phân tích: DEP5b2 (§21.17). Export: DEP6, chưa mount.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..actor import ActorContext
from ..errors import ServiceError
from ..proposal_response import interactive_response
from ..store.pilot_analysis import AnalysisIn, DetectIn, PilotAnalysis
from ..store.pilot_curation import PilotCuration, ProposeIn, catalog
from ..store.pilot_db import CreateSessionInput, DecisionInput, PilotDB, SnapshotInput, UpdateReferenceIn
from ..store.pilot_export import ConfirmExportIn, PilotExport
from ..store.pilot_features import PilotFeatures, PreparationIn
from ..store.pilot_interactive import PilotInteractive
from ..store.pilot_media import MediaBatchIn, MediaRequestIn, PilotMedia
from ..store.pilot_models import ModelCompleteIn, ModelUploadIn, PilotModels, models_write
from ..store.pilot_operations import AdminCatalogPatchIn, BrowserTimings, PilotOperations, operations_read
from ..store.pilot_runtime import PilotRuntime
from ..store.pilot_storage import PilotStorage
from ..store.pilot_templates import ForkInput, PilotTemplates
from ..store.pilot_uploads import PilotUploads, UploadIn
from ..timing import correlation, timing
from .auth import AuthConfig, extract_actor_context, verify_jwt_token
from .interactive_proxy import InteractiveProxy


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CancelJobIn(_In):
    expected_state_version: int = Field(ge=1)


class DeleteSourceIn(_In):
    expected_resource_version: int = Field(ge=1)


class InteractiveIn(_In):
    previous_batch_id: UUID | None = None
    save_current: bool = False
    request_id: UUID
    recipe: str
    config: dict | None = None
    strategy_params: dict | None = None
    batch_size: int = Field(default=16, ge=1, le=200)
    seed: int = Field(default=0, ge=0, lt=2**31)
    return_scores: bool = False
    kept_ids: list[str] = Field(default_factory=list, max_length=20000)
    dropped_ids: list[str] = Field(default_factory=list, max_length=20000)
    expected_state_version: int = Field(ge=1)


class MultipartPartsIn(_In):
    part_numbers: list[int] = Field(min_length=1, max_length=16)


class CheckpointIn(_In):
    batch_id: UUID | None = None
    kept_ids: list[str] = Field(default_factory=list, max_length=20000)
    dropped_ids: list[str] = Field(default_factory=list, max_length=20000)
    expected_state_version: int = Field(ge=1)


def _key(idempotency_key: str | None) -> str:
    if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise ServiceError("validation_error", "cần header Idempotency-Key (1–200 ký tự)", 422)
    return idempotency_key.strip()


def router(get_db: Callable[[], PilotDB], auth_cfg: AuthConfig, raw_jwks: dict | None = None,
           get_storage: Callable[[], PilotStorage] = PilotStorage.from_env) -> APIRouter:
    """`get_db` trả `PilotDB` đã kiểm schema (503 nếu chưa sẵn sàng); `get_storage` trả S3 pilot (503 nếu thiếu cấu hình)."""
    r = APIRouter(prefix="/api/v2", tags=["pilot-v2"])
    direct = InteractiveProxy()

    def token_actor(authorization: str | None = Header(None)):
        parts = (authorization or '').split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            raise ServiceError('unauthorized', 'cần Bearer token', 401)
        claims = verify_jwt_token(parts[1], auth_cfg, raw_jwks=raw_jwks)
        try:
            return ActorContext(user_id=UUID(claims['sub']))
        except (ValueError, KeyError):
            raise ServiceError('unauthorized', 'token không hợp lệ', 401) from None

    @r.get('/interactive/config')
    def interactive_config(a: ActorContext = Depends(token_actor)):
        return {'enabled': direct.enabled()}

    @r.post('/sessions/{session_id}/interactive/open')
    def interactive_open(session_id: UUID, refresh: bool = False, a: ActorContext = Depends(token_actor)):
        return direct.call('/open?refresh=true' if refresh else '/open', {'owner_id': str(a.user_id), 'session_id': str(session_id)})

    @r.post('/sessions/{session_id}/interactive/propose')
    def interactive_propose(session_id: UUID, inp: InteractiveIn, a: ActorContext = Depends(token_actor)):
        data = inp.model_dump(mode='json')
        previous = data.pop('previous_batch_id')
        save_current = data.pop('save_current')
        saved = None
        if previous or save_current:
            save_body = {k: data[k] for k in ('kept_ids', 'dropped_ids', 'expected_state_version')}
            save_body['batch_id'] = previous
            saved = save_checkpoint(a, session_id, save_body, 'advance:'+str(inp.request_id))
            data['expected_state_version'] = saved['state']['state_version']
            data['checkpoint'] = saved
        try:
            result = direct.call('/propose', {'owner_id': str(a.user_id), 'session_id': str(session_id), **data})
            result['checkpoint'] = saved
            return interactive_response(result)
        except ServiceError as exc:
            if saved:
                exc.details['checkpoint'] = interactive_response({'checkpoint': saved})['checkpoint']
            raise

    def save_checkpoint(a, session_id, body, key):
        try:
            proof = direct.call('/proof', {'owner_id': str(a.user_id), 'session_id': str(session_id), 'batch_id': body['batch_id']})
        except ServiceError as exc:
            if exc.code not in ('batch_expired', 'state_version_conflict'):
                raise
            proof = None   # RPC still replays a committed checkpoint after cache loss
        return PilotInteractive(get_db()).save(a, session_id, body, proof, key)

    @r.post('/sessions/{session_id}/checkpoint')
    def checkpoint(session_id: UUID, inp: CheckpointIn, a: ActorContext = Depends(token_actor), idempotency_key: str | None = Header(None)):
        key = _key(idempotency_key)
        body = inp.model_dump(mode='json')
        result = save_checkpoint(a, session_id, body, key)
        try:
            direct.call('/committed', {'owner_id': str(a.user_id), 'session_id': str(session_id), 'checkpoint': result})
        except ServiceError:
            pass   # durable Save already committed; cache revalidation catches stale version
        return interactive_response({'checkpoint': result})['checkpoint']

    def actor(request: Request, authorization: str | None = Header(None)) -> ActorContext:
        db = get_db()
        a = extract_actor_context(authorization, auth_cfg, profile_store=db, raw_jwks=raw_jwks)
        request.state.actor = a
        return a

    def runtime() -> PilotRuntime:
        return PilotRuntime(get_db())

    @r.get("/me")
    def me(a: ActorContext = Depends(actor)):
        p = get_db().get_profile(a.user_id)
        return {"user_id": p["user_id"], "display_name": p["display_name"], "active": p["active"],
                "authz_version": p["authz_version"], "operations_read": operations_read(a.user_id),
                "models_write": models_write(a.user_id)}

    @r.get('/operations')
    def operations(a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).overview(a)

    @r.get('/operations/logs')
    def operation_logs(hours: int = Query(1, ge=1, le=24),
                       source: Literal['api', 'worker', 'gpu', 'browser'] | None = None,
                       errors_only: bool = False,
                       upload_id: UUID | None = None,
                       job_id: UUID | None = None,
                       request_id: UUID | None = None,
                       limit: int = Query(50, ge=1, le=100),
                       cursor: str | None = Query(None, max_length=1024), a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).logs(a, hours=hours, source=source, errors_only=errors_only,
                                              upload_id=upload_id, job_id=job_id, request_id=request_id,
                                              limit=limit, cursor=cursor)

    @r.get('/operations/traces/{kind}/{resource_id}')
    def operation_trace(kind: Literal['upload', 'job', 'request'], resource_id: UUID, a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).trace(a, kind, resource_id)

    @r.post('/uploads/{upload_id}/timings')
    def upload_timings(upload_id: UUID, inp: BrowserTimings, a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).browser(a, upload_id, inp)

    @r.get("/sources")
    def list_sources(cursor: str | None = None, limit: int = 50, kind: Literal["VIDEO", "IMAGE_SET"] | None = None,
                     include_unpublished: bool = False, a: ActorContext = Depends(actor)):
        return get_db().list_sources(a, cursor, limit, kind, include_unpublished=include_unpublished)

    @r.get("/sources/{source_id}")
    def get_source(source_id: UUID, a: ActorContext = Depends(actor)):
        return get_db().get_source(a, source_id)

    @r.post("/sources/{source_id}/delete-requests", status_code=202)
    def delete_source(source_id: UUID, inp: DeleteSourceIn, a: ActorContext = Depends(actor),
                      idempotency_key: str | None = Header(None)):
        return runtime().request_source_delete(a, source_id, inp.expected_resource_version, _key(idempotency_key))

    @r.post("/sources/{source_id}/preparations")
    def prepare_source(source_id: UUID, inp: PreparationIn, a: ActorContext = Depends(actor),
                       idempotency_key: str | None = Header(None)):
        dto, status = PilotFeatures(get_db()).prepare(a, source_id, inp, _key(idempotency_key))
        return JSONResponse(dto.model_dump(mode="json"), status_code=status)

    @r.get("/delete-requests/{delete_id}")
    def get_delete(delete_id: UUID, a: ActorContext = Depends(actor)):
        return runtime().get_source_delete(a, delete_id)

    @r.get("/sessions")
    def list_sessions(cursor: str | None = None, limit: int = 50, a: ActorContext = Depends(actor)):
        return get_db().list_sessions(a, cursor, limit)

    @r.post("/sessions", status_code=201)
    def create_session(inp: CreateSessionInput, a: ActorContext = Depends(actor), idempotency_key: str | None = Header(None)):
        return get_db().create_session(a, inp, _key(idempotency_key))

    @r.get("/sessions/{session_id}")
    def get_session(session_id: UUID, a: ActorContext = Depends(actor)):
        return get_db().get_session(a, session_id)

    @r.get("/sessions/{session_id}/state")
    def get_state(session_id: UUID, a: ActorContext = Depends(actor)):
        return get_db().get_review_state(a, session_id)

    @r.get("/sessions/{session_id}/reference")
    def get_session_reference(session_id: UUID, a: ActorContext = Depends(actor)):
        return get_db().get_session_reference(a, session_id)

    @r.patch("/sessions/{session_id}/reference")
    def update_session_reference(session_id: UUID, inp: UpdateReferenceIn, a: ActorContext = Depends(actor)):
        return get_db().update_session_reference(a, session_id, inp)

    @r.post("/sessions/{session_id}/decisions")
    def append_decisions(session_id: UUID, inp: DecisionInput, a: ActorContext = Depends(actor)):
        return get_db().append_decisions(a, session_id, inp)

    @r.post("/sessions/{session_id}/snapshots", status_code=201)
    def create_snapshot(session_id: UUID, inp: SnapshotInput, a: ActorContext = Depends(actor),
                        idempotency_key: str | None = Header(None)):
        return get_db().create_snapshot(a, session_id, inp, _key(idempotency_key))

    @r.get("/sessions/{session_id}/snapshots")
    def list_snapshots(session_id: UUID, cursor: str | None = None, limit: int = 50, a: ActorContext = Depends(actor)):
        return get_db().list_snapshots(a, session_id, cursor, limit)

    @r.get("/snapshots/{snapshot_id}")
    def get_snapshot(snapshot_id: UUID, a: ActorContext = Depends(actor)):
        return get_db().get_snapshot(a, snapshot_id)

    # ---- đề xuất trên worker (DEP5b) ----
    @r.get("/recipes")
    def list_recipes(a: ActorContext = Depends(actor)):
        return catalog()

    @r.post("/sessions/{session_id}/proposals", status_code=202)
    def request_proposal(session_id: UUID, inp: ProposeIn, a: ActorContext = Depends(actor),
                         idempotency_key: str | None = Header(None)):
        cat_map = {r["name"]: r for r in catalog()}
        recipe_meta = cat_map.get(inp.recipe)
        is_bg = bool(recipe_meta and recipe_meta.get("background"))
        if direct.enabled() and not is_bg:
            raise ServiceError('interactive_required', 'tải lại trang để dùng đề xuất trực tiếp', 409)
        return PilotCuration(get_db()).request_proposal(a, session_id, inp, _key(idempotency_key))

    @r.get("/sessions/{session_id}/proposals")
    def list_proposals(session_id: UUID, a: ActorContext = Depends(actor)):
        return PilotCuration(get_db()).list_proposals(a, session_id)

    @r.get("/sessions/{session_id}/proposals/{n}")
    def get_proposal(session_id: UUID, n: int, a: ActorContext = Depends(actor)):
        return PilotCuration(get_db()).get_proposal(a, session_id, n)

    @r.get("/models")
    def list_models(include_unpublished: bool = False, a: ActorContext = Depends(actor)):
        return PilotAnalysis(get_db()).list_models(a, include_unpublished=include_unpublished)

    @r.get("/models/{model_id}")
    def get_model(model_id: UUID, a: ActorContext = Depends(actor)):
        return get_db().get_model(a, model_id)

    # ---- admin quản lý danh mục tài nguyên mẫu SYSTEM (§21.33, §21.36) ----
    @r.get("/admin/sources")
    def list_admin_sources(cursor: str | None = None, limit: int = 50, a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).list_admin_sources(a, cursor=cursor, limit=limit)

    @r.patch("/admin/sources/{source_id}")
    def update_admin_source(source_id: UUID, inp: AdminCatalogPatchIn, a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).update_admin_source(a, source_id, inp)

    @r.post("/admin/sources/{source_id}/copy-to-samples")
    def copy_to_samples(source_id: UUID, response: Response, a: ActorContext = Depends(actor), idempotency_key: str | None = Header(None)):
        PilotOperations(get_db()).operator(a)
        _key(idempotency_key)
        raise ServiceError('sample_copy_retired', 'Dùng Thêm vào kho mẫu để tham chiếu chỉ đọc, không sao chép file.', 410)

    @r.post("/admin/sources/{source_id}/reference-as-sample")
    def reference_as_sample(source_id: UUID, a: ActorContext = Depends(actor), idempotency_key: str | None = Header(None)):
        from ..store.pilot_sample_reference import reference_sample
        return reference_sample(get_db(), a, source_id, _key(idempotency_key))

    @r.get("/admin/models")
    def list_admin_models(cursor: str | None = None, limit: int = 50, a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).list_admin_models(a, cursor=cursor, limit=limit)

    @r.patch("/admin/models/{model_id}")
    def update_admin_model(model_id: UUID, inp: AdminCatalogPatchIn, a: ActorContext = Depends(actor)):
        return PilotOperations(get_db()).update_admin_model(a, model_id, inp)

    @r.post("/admin/models/uploads", status_code=201)
    def create_model_upload(inp: ModelUploadIn, a: ActorContext = Depends(actor)):
        return PilotModels(get_db(), get_storage).create_upload(a, inp)

    @r.post("/admin/models/uploads/{upload_id}/complete", status_code=202)
    def complete_model_upload(upload_id: UUID, inp: ModelCompleteIn, a: ActorContext = Depends(actor)):
        return PilotModels(get_db(), get_storage).complete(a, upload_id, inp)

    # ---- Catalog Dataset & Versions (N3) ----
    @r.get("/datasets")
    def list_datasets(a: ActorContext = Depends(actor)):
        return get_db().list_datasets(a)

    @r.get("/datasets/{dataset_id}")
    def get_dataset(dataset_id: UUID, a: ActorContext = Depends(actor)):
        return get_db().get_dataset(a, dataset_id)

    @r.post("/sessions/{session_id}/proposals/{n}/detections")
    def request_detections(session_id: UUID, n: int, inp: DetectIn, a: ActorContext = Depends(actor),
                           idempotency_key: str | None = Header(None)):
        dto, status = PilotAnalysis(get_db()).request_detections(a, session_id, n, inp, _key(idempotency_key))
        return JSONResponse(dto.model_dump(mode="json"), status_code=status)

    @r.get("/sessions/{session_id}/proposals/{n}/detections")
    def get_detections(session_id: UUID, n: int, model_id: UUID, a: ActorContext = Depends(actor)):
        return PilotAnalysis(get_db()).get_detections(a, session_id, n, model_id)

    @r.post("/sessions/{session_id}/analysis")
    def request_analysis(session_id: UUID, inp: AnalysisIn, a: ActorContext = Depends(actor), idempotency_key: str | None = Header(None)):
        dto, status = PilotAnalysis(get_db()).request_analysis(a, session_id, inp, _key(idempotency_key))
        return JSONResponse(dto.model_dump(mode="json"), status_code=status)

    @r.get("/sessions/{session_id}/analysis")
    def get_analysis(session_id: UUID, input_hash: str | None = None, a: ActorContext = Depends(actor)):
        return PilotAnalysis(get_db()).get_analysis(a, session_id, input_hash)

    @r.get("/jobs/{job_id}/result")
    def job_result(job_id: UUID, a: ActorContext = Depends(actor)):
        return PilotCuration(get_db()).job_result(a, job_id)

    @r.post("/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: UUID, a: ActorContext = Depends(actor), idempotency_key: str | None = Header(None)):
        return PilotCuration(get_db()).retry(a, job_id, _key(idempotency_key))

    @r.get("/jobs")
    def list_jobs(state: str | None = None, cursor: str | None = None, limit: int = 50,
                  kind: str | None = None, resource_id: UUID | None = None, a: ActorContext = Depends(actor)):
        return runtime().list_jobs(a, state, cursor, limit, kind, resource_id)

    @r.get("/jobs/{job_id}")
    def get_job(job_id: UUID, a: ActorContext = Depends(actor)):
        return runtime().get_job(a, job_id)

    @r.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: UUID, inp: CancelJobIn, a: ActorContext = Depends(actor)):
        return runtime().cancel(a, job_id, inp.expected_state_version)

    @r.get("/quota")
    def quota(a: ActorContext = Depends(actor)):
        return runtime().read_quota(a)

    # ---- upload trực tiếp (DEP4) ----
    def uploads() -> PilotUploads:
        return PilotUploads(get_db(), get_storage)

    @r.get("/uploads")
    def list_uploads(limit: int = Query(20, ge=1, le=50), a: ActorContext = Depends(actor)):
        return uploads().list(a, limit=limit)

    @r.post("/uploads", status_code=201)
    def create_upload(inp: UploadIn, a: ActorContext = Depends(actor), idempotency_key: str | None = Header(None)):
        with timing("api.upload.create", bytes=inp.declared_bytes):
            result = uploads().create(a, inp, _key(idempotency_key))
        from ..timing import emit
        emit("api.upload.created", upload_id=result.id)
        return result

    @r.get("/uploads/{upload_id}")
    def get_upload(upload_id: UUID, a: ActorContext = Depends(actor)):
        return uploads().get(a, upload_id)

    @r.post('/uploads/{upload_id}/multipart')
    def start_multipart(upload_id: UUID, a: ActorContext = Depends(actor)):
        from ..store.pilot_multipart import PilotMultipart
        return PilotMultipart(get_db(), get_storage).info(a, upload_id, start=True)

    @r.get('/uploads/{upload_id}/multipart')
    def read_multipart(upload_id: UUID, a: ActorContext = Depends(actor)):
        from ..store.pilot_multipart import PilotMultipart
        return PilotMultipart(get_db(), get_storage).info(a, upload_id)

    @r.post('/uploads/{upload_id}/multipart/parts')
    def sign_multipart(upload_id: UUID, inp: MultipartPartsIn, a: ActorContext = Depends(actor)):
        from ..store.pilot_multipart import PilotMultipart
        return PilotMultipart(get_db(), get_storage).sign(a, upload_id, inp.part_numbers)

    @r.post("/uploads/{upload_id}/complete", status_code=202)
    def complete_upload(upload_id: UUID, a: ActorContext = Depends(actor)):
        with correlation(upload_id=upload_id), timing("api.upload.complete"):
            result = uploads().complete(a, upload_id)
            from ..timing import emit
            emit("api.upload.completed", job_id=result.validation_job_id, status=result.state)
            return result

    @r.post("/uploads/{upload_id}/abort")
    def abort_upload(upload_id: UUID, a: ActorContext = Depends(actor)):
        return uploads().abort(a, upload_id)

    # ---- media riêng tư (DEP4) ----
    @r.post("/media/batch")
    def batch_media(inp: MediaBatchIn, a: ActorContext = Depends(actor)):
        return PilotMedia(get_db(), get_storage).request_batch(a, inp.items)

    @r.post("/media/requests")
    def request_media(inp: MediaRequestIn, a: ActorContext = Depends(actor)):
        dto = PilotMedia(get_db(), get_storage).request(a, inp)
        return JSONResponse(dto.model_dump(mode="json"), status_code=202 if dto.state == "PENDING" else 200)

    @r.get("/media/requests/{media_id}")
    def get_media(media_id: UUID, a: ActorContext = Depends(actor)):
        return PilotMedia(get_db(), get_storage).get(a, media_id)

    # ---- phiên mẫu (DEP4) ----
    @r.get("/demo-templates")
    def list_templates(a: ActorContext = Depends(actor)):
        return PilotTemplates(get_db()).list(a)

    @r.get("/demo-templates/{template_id}")
    def get_template(template_id: UUID, a: ActorContext = Depends(actor)):
        return PilotTemplates(get_db()).get(a, template_id)

    @r.post("/demo-templates/{template_id}/fork", status_code=201)
    def fork_template(template_id: UUID, inp: ForkInput, a: ActorContext = Depends(actor),
                      idempotency_key: str | None = Header(None)):
        return PilotTemplates(get_db()).fork(a, template_id, inp, _key(idempotency_key))

    # ---- xuất dữ liệu và che PII (CONTRACTS §21.8) ----
    def exports():
        return PilotExport(get_db(), get_storage())

    @r.get("/sessions/{session_id}/exports")
    def list_session_exports(session_id: UUID, cursor: str | None = None, limit: int = 20, a: ActorContext = Depends(actor)):
        return exports().list_exports(a, session_id, cursor, limit)

    @r.post("/snapshots/{snapshot_id}/exports", status_code=202)
    def create_snapshot_export(snapshot_id: UUID, a: ActorContext = Depends(actor),
                               idempotency_key: str | None = Header(None)):
        return exports().request_export(a, snapshot_id, _key(idempotency_key))

    @r.get("/exports/{export_id}")
    def get_export(export_id: UUID, a: ActorContext = Depends(actor)):
        return exports().get_export(a, export_id)

    @r.get("/exports/{export_id}/review")
    def get_export_review(export_id: UUID, a: ActorContext = Depends(actor)):
        return exports().get_review(a, export_id)

    @r.post("/exports/{export_id}/confirm", status_code=202)
    def confirm_export(export_id: UUID, inp: ConfirmExportIn, a: ActorContext = Depends(actor),
                       idempotency_key: str | None = Header(None)):
        return exports().confirm_export(a, export_id, inp.payload_hash, inp.review_receipt_id, _key(idempotency_key))

    @r.get("/exports/{export_id}/download")
    def download_export(export_id: UUID, a: ActorContext = Depends(actor)):
        return exports().get_download(a, export_id)

    # ---- vps disk upload (CONTRACTS §21.30) ----
    import os

    from . import vps_upload
    if not os.environ.get("RAV_VPS_UPLOAD_URL") or os.environ.get("RAV_VPS_UPLOAD_LOCAL") == "1":
        r.include_router(vps_upload.router)

    return r
