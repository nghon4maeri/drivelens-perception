"""Read-only operations and sanitized telemetry (CONTRACTS §21.19).

Inputs: verified actors, resource UUIDs, bounded browser spans. Output: operational metadata/timelines.
No credentials, private media capabilities or business mutations are exposed.
"""
from __future__ import annotations

import base64
import binascii
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..actor import ActorContext
from ..errors import ServiceError
from .pilot_db import PilotDB, require_user

BROWSER_STAGES = ('browser.create', 'browser.s3.transfer', 'browser.complete', 'browser.ready_wait',
                  'browser.refresh_library', 'browser.upload_total')
FIELDS = {'wall_ms', 'process_cpu_ms', 'bytes', 'queue_ms', 'status', 'outcome', 'error_type', 'error_code', 'cache_hit', 'duplicate'}


class _LogCursor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    window_end: AwareDatetime
    before_at: AwareDatetime
    before_id: UUID
    hours: int
    source: Literal['browser', 'api', 'worker', 'gpu'] | None
    errors_only: bool
    upload_id: UUID | None = None
    job_id: UUID | None = None
    request_id: UUID | None = None


class AdminCatalogPatchIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    published: bool | None = None


def _safe_data(data: dict) -> dict:
    out = {}
    for key in FIELDS:
        value = data.get(key)
        if isinstance(value, bool) or isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            out[key] = value
        elif key in ('outcome', 'error_type', 'error_code') and isinstance(value, str) and len(value) <= 80:
            out[key] = value
    return out


def operations_read(user_id: UUID) -> bool:
    values = os.environ.get('RAV_PILOT_OPERATOR_IDS', '')
    try:
        return user_id in {UUID(v.strip()) for v in values.split(',') if v.strip()}
    except ValueError:
        return False


class BrowserSpan(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: UUID
    at: AwareDatetime
    stage: Literal['browser.create', 'browser.s3.transfer', 'browser.complete', 'browser.ready_wait',
                   'browser.refresh_library', 'browser.upload_total']
    wall_ms: float = Field(ge=0, le=86_400_000)
    bytes: int = Field(ge=0)
    outcome: Literal['ok', 'error', 'incomplete', 'READY', 'FAILED', 'ABORTED', 'CREATED', 'UPLOADED', 'VALIDATING']


class BrowserTimings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    events: list[BrowserSpan] = Field(min_length=1, max_length=30)


def append_events(cur, rows: list[dict]) -> int:
    """Serialize daily cap with a transaction advisory lock; telemetry cannot grow without bound."""
    cur.execute('SELECT pg_advisory_xact_lock(1380013619)')
    cur.execute("SELECT count(*) AS n FROM rav_pilot_timing_event WHERE received_at >= date_trunc('day', now())")
    available = max(0, 20_000 - cur.fetchone()['n'])
    accepted = 0
    for row in rows[:available]:
        cur.execute('INSERT INTO rav_pilot_timing_event (id,observed_at,source,upload_id,job_id,request_id,attempt_id,stage,data) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING',
                    (row['id'], row['at'], row['source'], row.get('upload_id'), row.get('job_id'), row.get('request_id'),
                     row.get('attempt_id'), row['stage'], Jsonb(row['data'])))
        accepted += cur.rowcount
    return accepted


def safe_event(event: dict, source: str, event_id: UUID) -> dict | None:
    """Allowlist log fields; malformed logging records are discarded, not stored verbatim."""
    stage = event.get('stage', '')
    if not isinstance(stage, str) or len(stage) > 100 or not stage.startswith(('api.', 'db.', 'worker.')):
        return None
    if not (event.get('upload_id') or event.get('job_id') or
            (stage in ('api.response', 'api.error') and isinstance(event.get('status'), int) and event['status'] >= 400)):
        return None
    try:
        at = datetime.fromisoformat(event['at'])
        if at.tzinfo is None:
            return None
        row = {'id': event_id, 'at': at, 'source': source, 'stage': stage, 'data': {}}
        for key in ('upload_id', 'job_id', 'request_id', 'attempt_id'):
            row[key] = UUID(str(event[key])) if event.get(key) else None
        row['data'] = _safe_data(event)
        return row
    except (ValueError, KeyError, TypeError):
        return None


class PilotOperations:
    def __init__(self, db: PilotDB):
        self.db = db

    def operator(self, actor: ActorContext) -> UUID:
        uid = require_user(actor)
        if not operations_read(uid):
            raise ServiceError('forbidden', 'tài khoản không có quyền xem vận hành', 403)
        profile = self.db.get_profile(uid)
        if not profile or not profile['active']:
            raise ServiceError('account_disabled', 'tài khoản đã bị vô hiệu hóa', 401)
        return uid

    def browser(self, actor: ActorContext, upload_id: UUID, inp: BrowserTimings):
        uid = require_user(actor)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute('SELECT owner_id,declared_bytes FROM rav_pilot_upload WHERE id=%s FOR UPDATE', (upload_id,))
            upload = cur.fetchone()
            if not upload or upload['owner_id'] != uid:
                raise ServiceError('not_found', 'không tìm thấy upload', 404)
            if any(e.bytes > upload['declared_bytes'] for e in inp.events):
                raise ServiceError('validation_error', 'bytes vượt kích thước upload', 422)
            cur.execute("SELECT count(*) AS n FROM rav_pilot_timing_event WHERE upload_id=%s AND source='browser'", (upload_id,))
            count = cur.fetchone()['n']
            cur.execute('SELECT id FROM rav_pilot_timing_event WHERE id=ANY(%s)', ([e.id for e in inp.events],))
            existing = {r['id'] for r in cur.fetchall()}
            if count + len({e.id for e in inp.events}-existing) > 2000:
                raise ServiceError('telemetry_limit', 'đã đạt giới hạn timing upload', 429)
            rows = [{'id': e.id, 'at': e.at, 'stage': e.stage, 'source': 'browser', 'upload_id': upload_id,
                     'data': {'wall_ms': e.wall_ms, 'bytes': e.bytes, 'outcome': e.outcome}} for e in inp.events]
            return {'accepted': append_events(cur, rows)}

    def logs(self, actor: ActorContext, *, hours: int = 1, source: str | None = None,
             errors_only: bool = False, upload_id: UUID | None = None, job_id: UUID | None = None,
             request_id: UUID | None = None, limit: int = 50, cursor: str | None = None):
        """Bounded keyset read over the existing retention index; no count or new collection path."""
        self.operator(actor)
        if not 1 <= hours <= 24 or not 1 <= limit <= 100 or source not in (None, 'browser', 'api', 'worker', 'gpu'):
            raise ServiceError('validation_error', 'bộ lọc log không hợp lệ', 422)
        now = datetime.now(UTC)
        end, page = now, None
        if cursor is not None:
            try:
                if len(cursor) > 1024:
                    raise ValueError('cursor size')
                page = _LogCursor.model_validate_json(base64.b64decode(cursor, altchars=b'-_', validate=True))
                if (page.hours, page.source, page.errors_only, page.upload_id, page.job_id, page.request_id) != (
                        hours, source, errors_only, upload_id, job_id, request_id) or not (
                        now - timedelta(days=7) <= page.window_end <= now
                        and page.window_end - timedelta(hours=hours) <= page.before_at <= page.window_end):
                    raise ValueError('cursor window or filters')
                end = page.window_end
            except (ValueError, binascii.Error):
                raise ServiceError('validation_error', 'cursor log không hợp lệ hoặc bộ lọc đã đổi', 422) from None
        start = end - timedelta(hours=hours)
        clauses = ['received_at >= %s', 'received_at <= %s']
        args: list[object] = [start, end]
        if source:
            clauses.append('source = %s')
            args.append(source)
        if upload_id is not None:
            clauses.append('upload_id = %s')
            args.append(upload_id)
        if job_id is not None:
            clauses.append('job_id = %s')
            args.append(job_id)
        if request_id is not None:
            clauses.append('request_id = %s')
            args.append(request_id)
        if errors_only:
            clauses.append("(CASE WHEN jsonb_typeof(data->'status')='number' THEN (data->>'status')::numeric >= 400 "
                           "ELSE false END OR COALESCE(data->>'error_code','')<>'' "
                           "OR COALESCE(data->>'error_type','')<>'' OR lower(data->>'outcome') IN ('error','failed'))")
        if page:
            clauses.append('(received_at,id) < (%s,%s)')
            args.extend([page.before_at, page.before_id])
        with self.db.backend() as cur:
            cur.execute('SELECT id,received_at,observed_at,source,stage,upload_id,job_id,request_id,attempt_id,data '
                        'FROM rav_pilot_timing_event WHERE ' + ' AND '.join(clauses) +
                        ' ORDER BY received_at DESC,id DESC LIMIT %s', (*args, limit + 1))
            rows = cur.fetchall()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            token = _LogCursor(window_end=end, before_at=last['received_at'], before_id=last['id'],
                               hours=hours, source=source, errors_only=errors_only,
                               upload_id=upload_id, job_id=job_id, request_id=request_id)
            next_cursor = base64.urlsafe_b64encode(token.model_dump_json().encode()).decode()
        items = [{**r, 'data': _safe_data(r['data'])} for r in rows[:limit]]
        return {'items': items, 'next_cursor': next_cursor, 'window_start': start, 'window_end': end,
                'retention_days': 7, 'coverage': 'partial'}

    def overview(self, actor: ActorContext):
        self.operator(actor)
        out = {'trace_enabled': os.environ.get('RAV_TRACE_ENABLED') == 'true', 'retention_days': 7, 'daily_cap': 20_000}
        with self.db.backend() as cur:
            cur.execute('SELECT now() AS at')
            out['server_time'] = cur.fetchone()['at']
            cur.execute(
                "SELECT "
                "count(*) FILTER (WHERE state = 'QUEUED') AS queued, "
                "count(*) FILTER (WHERE state = 'WAITING_RESOURCE') AS waiting_resource, "
                "count(*) FILTER (WHERE state = 'RUNNING') AS running, "
                "count(*) FILTER (WHERE state = 'CANCEL_REQUESTED') AS cancel_requested, "
                "count(*) FILTER (WHERE state = 'FAILED' AND updated_at >= now() - interval '24 hours') AS failed_24h, "
                "count(*) FILTER (WHERE state = 'SUCCEEDED' AND updated_at >= now() - interval '24 hours') AS succeeded_24h "
                "FROM rav_pilot_job"
            )
            out['job_counts'] = cur.fetchone()
            queries = {
                'heartbeats': "SELECT component,observed_at,details,now()-observed_at > interval '30 seconds' AS stale FROM rav_pilot_heartbeat ORDER BY component",
                'slots': 'SELECT slot_key,kind,state,attempt_id,lease_until FROM rav_pilot_resource_slot ORDER BY kind,slot_key',
                'gpu_budget': 'SELECT observed_at,remaining_ms,reset_at,external_session_active,source FROM rav_pilot_gpu_budget_snapshot ORDER BY observed_at DESC LIMIT 1',
                'uploads': (
                    "SELECT u.id,p.display_name,u.state,u.declared_bytes,u.validation_job_id,u.source_id,"
                    "COALESCE(u.target_scope,'PRIVATE') AS target_scope,"
                    "u.created_at,u.updated_at,u.error->>'code' AS error_code "
                    "FROM rav_pilot_upload u JOIN rav_pilot_profile p ON p.user_id=u.owner_id "
                    "ORDER BY u.created_at DESC LIMIT 50"
                ),
                'jobs': (
                    "SELECT j.id,p.display_name,j.kind,j.state,j.phase,j.done,j.total,j.resource_id,"
                    "j.created_at,j.updated_at,j.error->>'code' AS error_code,"
                    "coalesce(j.payload->>'sample_copy'='true',false) AS sample_copy,"
                    "COALESCE(att.attempt_n, 0) AS attempt_n,"
                    "CASE "
                    "  WHEN att.first_started_at IS NOT NULL THEN GREATEST(0, round(extract(epoch FROM (att.first_started_at - j.created_at)) * 1000))::bigint "
                    "  WHEN j.state IN ('QUEUED', 'WAITING_RESOURCE') THEN GREATEST(0, round(extract(epoch FROM (now() - j.created_at)) * 1000))::bigint "
                    "  ELSE NULL "
                    "END AS queue_wait_ms,"
                    "CASE "
                    "  WHEN att.last_started_at IS NOT NULL THEN GREATEST(0, round(extract(epoch FROM (COALESCE(att.last_finished_at, now()) - att.last_started_at)) * 1000))::bigint "
                    "  ELSE NULL "
                    "END AS run_ms "
                    "FROM rav_pilot_job j "
                    "JOIN rav_pilot_profile p ON p.user_id=j.owner_id "
                    "LEFT JOIN LATERAL ("
                    "  SELECT max(a.attempt_n) AS attempt_n,"
                    "         min(a.started_at) AS first_started_at,"
                    "         (ARRAY_AGG(a.started_at ORDER BY a.attempt_n DESC))[1] AS last_started_at,"
                    "         (ARRAY_AGG(a.finished_at ORDER BY a.attempt_n DESC))[1] AS last_finished_at "
                    "  FROM rav_pilot_job_attempt a WHERE a.job_id = j.id"
                    ") att ON true "
                    "ORDER BY j.created_at DESC LIMIT 50"
                ),
                'quota': "SELECT p.display_name,q.resource,sum(q.base_ms-q.held_ms-q.consumed_ms) AS available_ms,sum(q.held_ms) AS held_ms FROM rav_pilot_quota_window q JOIN rav_pilot_profile p ON p.user_id=q.owner_id WHERE q.window_end > now() GROUP BY p.display_name,q.resource",
                'bonus': 'SELECT p.display_name,b.resource,sum(b.granted_ms-b.held_ms-b.consumed_ms) AS available_ms,sum(b.held_ms) AS held_ms FROM rav_pilot_quota_bonus b JOIN rav_pilot_profile p ON p.user_id=b.owner_id GROUP BY p.display_name,b.resource',
                'errors': "SELECT request_id,stage,data,observed_at FROM rav_pilot_timing_event WHERE source='api' AND stage='api.error' AND (data->>'status')::int >=400 ORDER BY received_at DESC LIMIT 20",
            }
            for key, query in queries.items():
                cur.execute(query)
                out[key] = cur.fetchall()
        return out

    # ---- Admin quản lý danh mục tài nguyên mẫu SYSTEM (§21.36) ----
    @staticmethod
    def _mapped_source_ids(mapping: Any) -> list[UUID]:
        out: list[UUID] = []
        if isinstance(mapping, list):
            raw_vals = [m.get('source_id') if isinstance(m, dict) else m for m in mapping]
        elif isinstance(mapping, dict):
            raw_vals = list(mapping.values())
        else:
            raw_vals = []
        for v in raw_vals:
            if not v:
                continue
            try:
                out.append(UUID(str(v)))
            except ValueError:
                continue
        return out

    @staticmethod
    def _source_admin_meta_in_tx(cur, row: dict, prep: dict, templates: list[dict]) -> dict:
        reasons: list[str] = []
        if row['availability'] != 'READY':
            reasons.append('source_not_ready')
        frames_state = (prep or {}).get('FRAMES')
        if row['kind'] == 'VIDEO' and not (frames_state and frames_state.ready):
            reasons.append('frames_not_ready')
        elif row['kind'] == 'IMAGE_SET':
            cur.execute('SELECT 1 FROM rav_pilot_dataset d JOIN rav_pilot_dataset_version dv ON dv.dataset_id = d.id '
                        'WHERE d.source_id = %s LIMIT 1', (row['id'],))
            if not cur.fetchone():
                reasons.append('dataset_not_ready')
        sid = row['id']
        deps = [
            {'id': t['id'], 'template_key': t['template_key'], 'name': t['name'],
             'availability': t['availability'], 'reason_code': t['reason_code']}
            for t in templates
            if sid in PilotOperations._mapped_source_ids(t.get('source_mapping'))
        ]
        dto = PilotDB._source_dto(row, prep).model_dump(mode='json')
        dto.update({
            'publishable': len(reasons) == 0,
            'unpublishable_reasons': reasons,
            'dependent_template_count': len(deps),
            'dependent_templates': deps,
        })
        return dto

    def list_admin_sources(self, actor: ActorContext, cursor: str | None = None, limit: int = 50):
        from .pilot_db import Page, _page_args

        uid = self.operator(actor)
        offset, lim = _page_args(cursor, limit)
        with self.db.backend() as cur:
            cur.execute("SELECT count(*) AS n FROM rav_pilot_source_asset WHERE scope = 'SYSTEM' AND availability <> 'DELETED'")
            total = cur.fetchone()['n']
            cur.execute(f"SELECT {PilotDB._SOURCE_COLS} FROM rav_pilot_source_asset "
                        "WHERE scope = 'SYSTEM' AND availability <> 'DELETED' "
                        "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", (lim, offset))
            rows = cur.fetchall()
            cur.execute("SELECT t.id, t.template_key, t.name, t.source_mapping, s.availability, s.reason_code "
                        "FROM rav_pilot_demo_template t JOIN rav_pilot_demo_template_status s ON s.template_id = t.id "
                        "ORDER BY t.created_at, t.template_key")
            templates = cur.fetchall()
        prep = self.db._readiness(uid, [r['id'] for r in rows])
        with self.db.backend() as cur:
            items = [self._source_admin_meta_in_tx(cur, r, prep.get(r['id']) or {}, templates) for r in rows]
        return Page(items=items, total=total, next_cursor=str(offset + lim) if offset + lim < total else None)

    def update_admin_source(self, actor: ActorContext, source_id: UUID, inp: AdminCatalogPatchIn) -> dict:
        from . import pilot_frames
        from .pilot_features import readiness_in_tx

        uid = self.operator(actor)
        if inp.name is None and inp.description is None and inp.published is None:
            raise ServiceError('validation_error', 'cần ít nhất một trường cập nhật (name, description, published)', 422)
        if inp.name is not None and not inp.name.strip():
            raise ServiceError('validation_error', 'tên nguồn không được để trống', 422)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_source_asset WHERE id = %s AND scope = 'SYSTEM' AND availability <> 'DELETED' FOR UPDATE",
                        (source_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError('not_found', f'không tìm thấy nguồn mẫu {source_id}', 404)
            prep = readiness_in_tx(cur, uid, [source_id]).get(source_id) or {}
            prep['FRAMES'] = pilot_frames.readiness_in_tx(cur, uid, [source_id]).get(source_id)
            cur.execute("SELECT t.id, t.template_key, t.name, t.source_mapping, s.availability, s.reason_code "
                        "FROM rav_pilot_demo_template t JOIN rav_pilot_demo_template_status s ON s.template_id = t.id FOR UPDATE OF s")
            templates = cur.fetchall()
            meta = self._source_admin_meta_in_tx(cur, row, prep, templates)
            if inp.published is True and not meta['publishable']:
                raise ServiceError('source_not_publishable',
                                   f"nguồn mẫu chưa đủ điều kiện xuất bản ({', '.join(meta['unpublishable_reasons'])})", 409)
            new_name = inp.name.strip() if inp.name is not None else row['name']
            new_desc = inp.description.strip() if inp.description is not None else (row.get('description') or '')
            new_pub = inp.published if inp.published is not None else bool(row.get('published', True))
            cur.execute(f"UPDATE rav_pilot_source_asset SET name = %s, description = %s, published = %s WHERE id = %s "
                        f"RETURNING {PilotDB._SOURCE_COLS}",
                        (new_name, new_desc, new_pub, source_id))
            updated = cur.fetchone()
            if inp.published is not None:
                for t in templates:
                    mapped_ids = self._mapped_source_ids(t.get('source_mapping'))
                    if source_id not in set(mapped_ids):
                        continue
                    cur.execute("SELECT id, availability, published FROM rav_pilot_source_asset WHERE id = ANY(%s) AND scope = 'SYSTEM'",
                                (mapped_ids,))
                    srows = {r['id']: r for r in cur.fetchall()}
                    if any(s not in srows or srows[s]['availability'] != 'READY' for s in mapped_ids):
                        new_avail, new_reason = 'BLOCKED', 'source_unavailable'
                    elif any(not srows[s].get('published', True) for s in mapped_ids):
                        new_avail, new_reason = 'BLOCKED', 'source_unpublished'
                    else:
                        new_avail, new_reason = 'VERIFIED', None
                    if new_avail == 'BLOCKED' and (t['availability'], t['reason_code']) != ('BLOCKED', new_reason):
                        cur.execute("UPDATE rav_pilot_demo_template_status SET availability = 'BLOCKED', reason_code = %s, "
                                    "version = version + 1, updated_at = now() WHERE template_id = %s",
                                    (new_reason, t['id']))
                        t['availability'], t['reason_code'] = 'BLOCKED', new_reason
                    elif new_avail == 'VERIFIED' and t['availability'] == 'BLOCKED' and t['reason_code'] in (
                            None, 'source_unpublished', 'source_unavailable'):
                        cur.execute("UPDATE rav_pilot_demo_template_status SET availability = 'VERIFIED', reason_code = NULL, "
                                    "version = version + 1, updated_at = now() WHERE template_id = %s",
                                    (t['id'],))
                        t['availability'], t['reason_code'] = 'VERIFIED', None
            return self._source_admin_meta_in_tx(cur, updated, prep, templates)

    @staticmethod
    def _model_admin_dto(row: dict) -> dict:
        from .pilot_analysis import _model_dto

        reasons: list[str] = []
        if row['validation'] != 'INFERENCE_VERIFIED':
            reasons.append('model_not_verified')
        dto = _model_dto(row).model_dump(mode='json')
        dto.update({
            'publishable': len(reasons) == 0,
            'unpublishable_reasons': reasons,
        })
        return dto

    def list_admin_models(self, actor: ActorContext, cursor: str | None = None, limit: int = 50):
        from .pilot_db import Page, _page_args

        self.operator(actor)
        offset, lim = _page_args(cursor, limit)
        with self.db.backend() as cur:
            cur.execute("SELECT count(*) AS n FROM rav_pilot_model_asset WHERE scope = 'SYSTEM'")
            total = cur.fetchone()['n']
            cur.execute("SELECT * FROM rav_pilot_model_asset WHERE scope = 'SYSTEM' "
                        "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", (lim, offset))
            rows = cur.fetchall()
        items = [self._model_admin_dto(r) for r in rows]
        return Page(items=items, total=total, next_cursor=str(offset + lim) if offset + lim < total else None)

    def update_admin_model(self, actor: ActorContext, model_id: UUID, inp: AdminCatalogPatchIn) -> dict:
        uid = self.operator(actor)
        if inp.name is None and inp.description is None and inp.published is None:
            raise ServiceError('validation_error', 'cần ít nhất một trường cập nhật (name, description, published)', 422)
        if inp.name is not None and not inp.name.strip():
            raise ServiceError('validation_error', 'tên model không được để trống', 422)
        with self.db.backend() as cur:
            self.db.lock_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_model_asset WHERE id = %s AND scope = 'SYSTEM' FOR UPDATE", (model_id,))
            row = cur.fetchone()
            if not row:
                raise ServiceError('not_found', f'không tìm thấy model mẫu {model_id}', 404)
            if inp.published is True and row['validation'] != 'INFERENCE_VERIFIED':
                raise ServiceError('model_not_publishable', 'model mẫu chưa được xác minh suy luận (INFERENCE_VERIFIED)', 409)
            new_name = inp.name.strip() if inp.name is not None else row['name']
            new_desc = inp.description.strip() if inp.description is not None else (row.get('description') or '')
            new_pub = inp.published if inp.published is not None else bool(row.get('published', True))
            cur.execute("UPDATE rav_pilot_model_asset SET name = %s, description = %s, published = %s WHERE id = %s RETURNING *",
                        (new_name, new_desc, new_pub, model_id))
            updated = cur.fetchone()
        return self._model_admin_dto(updated)

    def trace(self, actor: ActorContext, kind: str, resource_id: UUID):
        self.operator(actor)
        with self.db.backend() as cur:
            upload_id, job_ids = None, []
            resource = None
            if kind == 'upload':
                cur.execute('SELECT id,state,declared_bytes,validation_job_id,created_at,updated_at,error->>\'code\' AS error_code FROM rav_pilot_upload WHERE id=%s', (resource_id,))
                resource = cur.fetchone()
                if resource:
                    upload_id = resource_id
                    job_ids = [resource['validation_job_id']] if resource['validation_job_id'] else []
            elif kind == 'job':
                cur.execute('SELECT id,kind,state,phase,done,total,created_at,updated_at,error->>\'code\' AS error_code,payload->>\'upload_id\' AS upload_id FROM rav_pilot_job WHERE id=%s', (resource_id,))
                resource = cur.fetchone()
                if resource:
                    job_ids = [resource_id]
                    upload_id = UUID(resource['upload_id']) if resource['upload_id'] else None
            if kind != 'request' and resource is None:
                raise ServiceError('not_found', 'không tìm thấy upload/job', 404)
            cur.execute('SELECT id,job_id,attempt_n,state,started_at,finished_at,usage_state,cleanup_done,session_stop_verified,launch_state,usage->>\'cpu_ms\' AS cpu_ms,usage->>\'gpu_ms\' AS gpu_ms FROM rav_pilot_job_attempt WHERE job_id=ANY(%s) ORDER BY started_at', (job_ids,))
            attempts = cur.fetchall()
            cur.execute('SELECT source,stage,observed_at,request_id,job_id,attempt_id,data FROM rav_pilot_timing_event '
                        'WHERE upload_id=%s OR job_id=ANY(%s) OR request_id=%s OR request_id IN '
                        '(SELECT request_id FROM rav_pilot_timing_event WHERE upload_id=%s OR job_id=ANY(%s)) '
                        'ORDER BY observed_at,id LIMIT 1001',
                        (upload_id, job_ids, resource_id if kind == 'request' else None, upload_id, job_ids))
            events = cur.fetchall()
        return {'kind': kind, 'id': resource_id, 'resource': resource, 'attempts': attempts, 'events': events[:1000],
                'truncated': len(events) > 1000, 'coverage': 'partial', 'retention_days': 7,
                'note': 'Log 7 ngày, có thể thiếu/cũ hoặc bị giới hạn; browser tự báo. Span lồng nhau không cộng dồn.'}
