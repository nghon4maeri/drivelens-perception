"""Contabo synchronous proposal owner (§21.26): bounded RAM, no warm DB/S3/raw reads.

FastAPI service uses a backend-only bearer credential; verified owner comes from Render.
TEXT supervisor process runs serve(); standalone: python -m src.rav.worker.interactive.
"""
from __future__ import annotations

import copy
import hmac
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..core.registry import REGISTRY, discover
from ..core.types import FieldTable, RecipeConfig, ReviewState, VideoRef
from ..errors import ServiceError
from ..pipeline.prepare import make_context
from ..pipeline.propose import config_hash, propose
from ..proposal_response import interactive_response
from ..service.explain import explain_record
from ..service.proposal import proposal_record, to_proposals
from ..store.pilot_curation import canonical_hash, catalog, check_config, resolve_strategy_params
from ..store.pilot_db import PilotDB
from ..store.pilot_interactive import PilotInteractive, required_features
from ..store.pilot_storage import PilotStorage
from ..timing import timing
from .curation_handlers import finite
from .session_context import ARTIFACT_FIELDS, ArtifactFieldStore, cached


class SessionIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    owner_id: UUID
    session_id: UUID


class ProposalIn(SessionIn):
    checkpoint: dict | None = None   # trusted Render-only; public API schema forbids this field
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


class ProofIn(SessionIn):
    batch_id: UUID | None = None


class CommittedIn(SessionIn):
    checkpoint: dict


@dataclass
class Session:
    basis: dict
    ctx: object
    frames: dict
    size: int
    checked: float
    touched: float
    replies: OrderedDict = field(default_factory=OrderedDict)
    next_n: int = 0


class Interactive:
    def __init__(self, db: PilotDB, storage: PilotStorage, workdir: Path, *, max_bytes: int = 512 << 20,
                 ttl_s: float = 900, validate_s: float = 15):
        self.repo, self.storage, self.workdir = PilotInteractive(db), storage, workdir
        self.max_bytes, self.ttl_s, self.validate_s = max_bytes, ttl_s, validate_s
        self.sessions = OrderedDict()
        self.lock = threading.RLock()
        self.active = set()

    def _enter(self, owner: UUID):
        with self.lock:
            if owner in self.active or len(self.active) >= 2:
                raise ServiceError('interactive_busy', 'Contabo đang bận; thử lại sau', 429, {'retry_after': 1})
            self.active.add(owner)

    def _leave(self, owner: UUID):
        with self.lock:
            self.active.discard(owner)

    def _load(self, owner: UUID, sid: UUID) -> Session:
        p = self.repo.open(owner, sid)
        discover()
        store = ArtifactFieldStore()
        videos = [VideoRef(s['core_source_id'], '', s['metadata']['fps'], s['metadata']['duration_ms']/1000,
                           {'n_frames': s['metadata']['n_frames']}) for s in p['pool']]
        ctx = make_context(videos, {'source': None, **p['spec']}, store)
        by_source = {s['core_source_id']: [u.id for u in ctx.units if u.source_id == s['core_source_id']] for s in p['pool']}
        if ctx._execution.spec_hash != p['spec_hash'] or len(ctx.units) != sum(s['metadata']['n_units'] for s in p['pool']):
            raise ServiceError('result_invalid', 'metadata/grid không khớp sampling version', 409)
        size = len(ctx.units)*512 + len(p['frames'])*512
        for s in p['pool']:
            for art in s['features'].values():
                path = cached(self.storage, self.workdir/'feature-cache', art['object_key'], art['sha256'], art['size_bytes'], '.npz')
                with np.load(path, allow_pickle=False) as z:
                    ids = list(z['ids'])
                    if ids != by_source[s['core_source_id']]:
                        raise ServiceError('feature_not_ready', 'artifact lệch unit', 409)
                    for name, key_doc in art['field_keys'].items():
                        key = REGISTRY.feature_for(name)().key
                        if asdict(key) != key_doc:
                            raise ServiceError('feature_not_ready', 'artifact khác phiên bản feature', 409)
                        values = np.array(z[name], copy=True)
                        size += values.nbytes
                        store.add(FieldTable(key, ids, values, ARTIFACT_FIELDS.get(name, 'vector')), (s['core_source_id'], p['spec_hash']))
        ctx.cache_only_fields |= set(ARTIFACT_FIELDS)
        ctx.state = ReviewState(set(p['kept_ids']), set(p['dropped_ids']))
        ctx.proposals = to_proposals(str(sid), p['records'], ctx.units)
        return Session(p, ctx, {(f['unit_id'], f['variant'].lower()): f for f in p['frames']}, size, time.monotonic(), time.monotonic(),
                       next_n=max((r['n'] for r in p['records']), default=0))

    def _session(self, owner: UUID, sid: UUID) -> Session:
        k, now = (owner, sid), time.monotonic()
        with self.lock:
            for key, entry in list(self.sessions.items()):
                if key[0] not in self.active and now-entry.touched > self.ttl_s:
                    del self.sessions[key]
            entry = self.sessions.get(k)
        if entry is not None and now-entry.checked >= self.validate_s:
            try:
                self.repo.validate(owner, sid, entry.basis)
            except ServiceError:
                with self.lock:
                    self.sessions.pop(k, None)
                raise
            entry.checked = now
        if entry is None:
            entry = self._load(owner, sid)
            if entry.size > self.max_bytes:
                raise ServiceError('interactive_capacity', 'phiên vượt trần RAM cache', 413)
            with self.lock:
                while sum(s.size for s in self.sessions.values())+entry.size > self.max_bytes or len(self.sessions) >= 8:
                    victim = next((key for key in self.sessions if key[0] not in self.active), None)
                    if victim is None:
                        raise ServiceError('interactive_busy', 'cache đang bận; thử lại sau', 429, {'retry_after': 1})
                    del self.sessions[victim]
                self.sessions[k] = entry
        with self.lock:
            entry.touched = now
            self.sessions.move_to_end(k)
        return entry

    def open(self, inp: SessionIn, *, refresh: bool = False):
        self._enter(inp.owner_id)
        try:
            if refresh:
                with self.lock:
                    self.sessions.pop((inp.owner_id, inp.session_id), None)
            s = self._session(inp.owner_id, inp.session_id)
            return {'state_version': s.basis['state_version'], 'ready': True, 'cache_bytes': s.size}
        finally:
            self._leave(inp.owner_id)

    def run(self, inp: ProposalIn):
        self._enter(inp.owner_id)
        try:
            if inp.checkpoint:
                self._committed(inp.owner_id, inp.session_id, inp.checkpoint)
            s = self._session(inp.owner_id, inp.session_id)
            digest, rid = canonical_hash(inp.model_dump(mode='json')), str(inp.request_id)
            if rid in s.replies:
                old_hash, result, _ = s.replies[rid]
                if old_hash != digest:
                    raise ServiceError('idempotency_conflict', 'request_id khác input', 409)
                return copy.deepcopy(interactive_response(result))
            if inp.expected_state_version != s.basis['state_version']:
                raise ServiceError('state_version_conflict', 'phiên khác checkpoint; tải lại phiên', 409)
            check_config(inp.recipe, inp.config)
            required_features(inp.recipe, s.basis)
            kept, dropped = set(inp.kept_ids), set(inp.dropped_ids)
            ids = {u.id for u in s.ctx.units}
            if kept & dropped or not (kept | dropped) <= ids or len(kept) > s.basis['target_k']:
                raise ServiceError('invalid_state', 'giữ/bỏ không hợp lệ hoặc vượt target K', 422)
            cfg = RecipeConfig.model_validate(inp.config) if inp.config else REGISTRY.recipe(inp.recipe)
            if cfg.features or cfg.candidate_stage or getattr(REGISTRY.get('reference', cfg.reference.type), 'needs_reference_sets', False):
                raise ServiceError('invalid_config', 'recipe không thuộc interactive pilot', 422)
            strategy = REGISTRY.strategy(inp.recipe)
            params = strategy.Params.model_validate(resolve_strategy_params(next(r for r in catalog() if r["name"] == inp.recipe), inp.strategy_params, inp.config))
            if inp.strategy_params is not None:
                cfg = strategy.resolve_config(cfg, params)
            config_hash(cfg)
            s.ctx.state, s.ctx.evidence = ReviewState(kept, dropped), {}
            s.ctx._used.clear()
            scores = {} if inp.return_scores else None
            try:
                prop = propose(cfg, inp.batch_size, inp.seed, s.ctx, score_output=scores)
            except (ValueError, OSError) as exc:
                raise ServiceError('invalid_query', str(exc)[:200], 422) from None
            s.next_n += 1
            prop.n = prop.round = s.next_n
            record = finite(proposal_record(cfg, inp.batch_size, inp.seed, s.ctx, prop))
            record['input'].update(strategy_params=params.model_dump(mode='json'), strategy_version=strategy.version)
            record['scores'] = finite(scores)
            try:
                record['explain'] = finite(explain_record(record, s.basis['goal'], s.ctx))
            except (ValueError, ServiceError):
                record['explain'] = None
            for pick in record['picks']:
                pick['source_id'] = next(a['source_id'] for a in s.basis['pool'] if a['core_source_id'] == pick['unit_id'].rsplit(':', 1)[0])
            # No second scoring pass; picked scores/evidence are already in the response.
            expires = datetime.now(UTC)+timedelta(seconds=s.basis['url_ttl_s'])
            images = [{'source_id': pick['source_id'], 'unit_id': pick['unit_id'], 'variant': variant,
                       'url': self.storage.presign_get(f['object_key'], s.basis['url_ttl_s']), 'expires_at': expires.isoformat()}
                      for pick in record['picks'] for variant in ('thumbnail', 'frame')
                      if (f := s.frames.get((pick['unit_id'], variant))) is not None]
            result = {'proposal': record, 'batch_id': rid, 'images': images, 'state_version': s.basis['state_version']}
            json.dumps(result, allow_nan=False)
            basis = {k: copy.deepcopy(s.basis[k]) for k in ('session_id', 'owner_id', 'state_version', 'pool', 'spec_hash')}
            s.replies[rid] = (digest, result, basis)
            while len(s.replies) > 8:
                s.replies.popitem(last=False)
            return copy.deepcopy(interactive_response(result))
        finally:
            self._leave(inp.owner_id)

    def proof(self, inp: ProofIn):
        self._enter(inp.owner_id)
        try:
            s = self._session(inp.owner_id, inp.session_id)
            rec = None
            if inp.batch_id:
                reply = s.replies.get(str(inp.batch_id))
                if reply is None:
                    raise ServiceError('batch_expired', 'batch đã hết cache; đề xuất lại trước khi lưu', 409)
                rec = reply[1]['proposal']
            basis = {k: s.basis[k] for k in ('session_id', 'owner_id', 'state_version', 'pool', 'spec_hash')}
            if inp.batch_id:
                basis = reply[2]
            return {'basis': basis, 'proposal': rec, 'unit_ids': [u.id for u in s.ctx.units]}
        finally:
            self._leave(inp.owner_id)

    def _committed(self, owner, sid, checkpoint):
        state = checkpoint['state']
        if state['session_id'] != str(sid):
            raise ServiceError('not_found', 'checkpoint khác phiên', 404)
        with self.lock:
            s = self.sessions.get((owner, sid))
            if s and state['state_version'] >= s.basis['state_version']:
                s.basis['state_version'] = state['state_version']
                s.basis['kept_ids'], s.basis['dropped_ids'] = state['kept_ids'], state['dropped_ids']
                s.checked = time.monotonic()

    def committed(self, inp: CommittedIn):
        self._enter(inp.owner_id)
        try:
            self._committed(inp.owner_id, inp.session_id, inp.checkpoint)
            return {'ok': True}
        finally:
            self._leave(inp.owner_id)


def create_app(service: Interactive, token: str) -> FastAPI:
    if len(token) < 32:
        raise ValueError('interactive token cần tối thiểu32 ký tự')
    app = FastAPI(title='RAV interactive')
    from fastapi.middleware.cors import CORSMiddleware

    from ..api.vps_upload import router as upload_router
    origins = [o.strip() for o in os.environ.get('RAV_FRONTEND_ORIGIN', '').split(',') if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'PUT', 'OPTIONS'],
                           allow_headers=['Authorization', 'Content-Type', 'Upload-Offset'])
    app.include_router(upload_router, prefix='/api/v2')

    def auth(value):
        if not hmac.compare_digest(value or '', 'Bearer '+token):
            raise ServiceError('unauthorized', 'service credential không hợp lệ', 401)

    @app.exception_handler(ServiceError)
    async def error(request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message, **exc.details}}, status_code=exc.status,
                            headers={'Retry-After': str(exc.details.get('retry_after', 1))} if exc.status == 429 else {})

    @app.get('/health')
    def health():
        return {'ready': True, 'service': 'rav-interactive'}

    @app.post('/open')
    def opened(inp: SessionIn, authorization: str | None = Header(None), refresh: bool = False):
        auth(authorization)
        with timing('interactive.open'):
            return service.open(inp, refresh=refresh)

    @app.post('/propose')
    def proposed(inp: ProposalIn, authorization: str | None = Header(None)):
        auth(authorization)
        with timing('interactive.propose'):
            return service.run(inp)

    @app.post('/proof')
    def proof(inp: ProofIn, authorization: str | None = Header(None)):
        auth(authorization)
        return service.proof(inp)

    @app.post('/invalidate')
    def invalidate(inp: SessionIn, authorization: str | None = Header(None)):
        auth(authorization)
        with service.lock:
            if inp.owner_id in service.active:
                raise ServiceError('interactive_busy', 'request đang chạy', 429, {'retry_after': 1})
            service.sessions.pop((inp.owner_id, inp.session_id), None)
        return {'ok': True}

    @app.post('/committed')
    def committed(inp: CommittedIn, authorization: str | None = Header(None)):
        auth(authorization)
        return service.committed(inp)

    return app


def serve(db, storage, workdir, stop):
    import uvicorn
    if os.environ.get('RAV_INTERACTIVE_WARM_TEXT', '1') == '1':
        from ..components.features.clip_b32 import MODEL, REVISION, _load_text
        _load_text(MODEL, REVISION)
    service = Interactive(db, storage, workdir, max_bytes=int(os.environ.get('RAV_INTERACTIVE_CACHE_BYTES', str(512<<20))))
    cert, key = os.environ.get('RAV_INTERACTIVE_TLS_CERT'), os.environ.get('RAV_INTERACTIVE_TLS_KEY')
    server = uvicorn.Server(uvicorn.Config(create_app(service, os.environ['RAV_INTERACTIVE_TOKEN']), host='0.0.0.0' if cert and key else '127.0.0.1',
                           port=int(os.environ.get('RAV_INTERACTIVE_PORT', '8443')), log_level='warning',
                           ssl_certfile=cert, ssl_keyfile=key))
    parent = os.getppid()
    def stopping():
        from .process import rss_bytes
        cap = int(os.environ.get('RAV_INTERACTIVE_RSS_BYTES', str(1536<<20)))
        while not stop.wait(.5):
            if os.getppid() != parent or (rss_bytes() or 0) > cap:
                break
        server.should_exit = True
    threading.Thread(target=stopping, daemon=True).start()
    server.run()


if __name__ == '__main__':
    from .lanes import configure_threads
    configure_threads(1)
    serve(PilotDB(os.environ['RAV_PILOT_WORKER_DATABASE_URL']), PilotStorage.from_env(),
          Path(os.environ.get('RAV_PILOT_WORKDIR', 'output/pilot-worker')), threading.Event())
