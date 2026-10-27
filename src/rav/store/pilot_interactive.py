"""Interactive session snapshots and explicit checkpoints (§21.26).

Reads validated metadata/artifact references, never ML/raw. Save writes server-confirmed current
proposal and decision delta in one CAS/idempotent transaction; no CPU job or quota.
"""
from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from ..actor import ActorContext
from ..errors import ServiceError
from .pilot_curation import canonical_hash, catalog, feature_input_hash
from .pilot_db import PilotDB, replay, require_user
from .pilot_features import DEFAULT_SAMPLE_SPEC


class PilotInteractive:
    def __init__(self, db: PilotDB):
        self.db = db

    def open(self, uid: UUID, sid: UUID) -> dict:
        with self.db.backend() as cur:
            profile = self.db.read_profile(cur, uid)
            cur.execute("SELECT * FROM rav_pilot_session WHERE id=%s AND owner_id=%s", (sid, uid))
            session = cur.fetchone()
            if not session:
                raise ServiceError("not_found", "không tìm thấy phiên", 404)
            cur.execute("SELECT config FROM rav_pilot_quota_policy WHERE id=%s", (profile['policy_id'],))
            cfg = cur.fetchone()['config']
            limit = cfg.get('chunk_bounds_by_kind', {}).get('PROPOSE', {}).get('max_units', 4000)
            if session['n_units'] > limit:
                raise ServiceError('pool_too_large', 'pool vượt giới hạn interactive', 422)
            cur.execute("SELECT s.* FROM rav_pilot_session_source ss JOIN rav_pilot_source_asset s ON s.id=ss.source_id "
                        "WHERE ss.session_id=%s ORDER BY ss.ord", (sid,))
            sources = cur.fetchall()
            pool = []
            cur.execute("SELECT * FROM rav_pilot_artifact WHERE source_id=ANY(%s) AND kind='FEATURE' AND state='READY'",
                        ([s['id'] for s in sources],))
            arts = cur.fetchall()
            for s in sources:
                if s['availability'] != 'READY' or (s['scope'] != 'SYSTEM' and s['owner_id'] != uid):
                    raise ServiceError('source_unavailable', 'nguồn không sẵn sàng', 409)
                feats = {a['variant']: {'artifact_id': str(a['id']), 'object_key': a['object_key'], 'sha256': a['sha256'],
                         'size_bytes': a['size_bytes'], 'field_keys': a['manifest'].get('field_keys', {})}
                         for a in arts if a['source_id'] == s['id'] and a['variant'] in ('DINO', 'CLIP')
                         and a['input_hash'] == feature_input_hash(s, a['variant'])}
                pool.append({'source_id': str(s['id']), 'core_source_id': s['core_source_id'], 'metadata': s['metadata'],
                             'resource_version': s['resource_version'], 'features': feats})
            cur.execute("SELECT f.unit_id,f.variant,f.object_key,f.sha256 FROM rav_pilot_frame f "
                        "JOIN rav_pilot_frame_set fs ON fs.id=f.frame_set_id WHERE fs.source_id=ANY(%s) AND fs.state='READY'",
                        ([s['id'] for s in sources],))
            frames = cur.fetchall()
            rows = self.db._decisions(cur, sid)
            kept, dropped = replay(rows)
            cur.execute('SELECT n,frozen_input,picks,evidence FROM rav_pilot_proposal WHERE session_id=%s ORDER BY n DESC LIMIT 32', (sid,))
            records = [{'n': r['n'], 'round': r['n'], 'input': r['frozen_input'], 'picks': r['picks'],
                        'relaxed': bool((r['evidence'] or {}).get('relaxed'))} for r in reversed(cur.fetchall())]
        return {'session_id': str(sid), 'owner_id': str(uid), 'state_version': session['state_version'],
                'goal': session['goal'], 'target_k': session['target_k'], 'pool': pool, 'frames': frames,
                'kept_ids': sorted(kept), 'dropped_ids': sorted(dropped), 'records': records,
                'spec': {k: DEFAULT_SAMPLE_SPEC[k] for k in ('decoder', 'units')}, 'spec_hash': DEFAULT_SAMPLE_SPEC['spec_hash'],
                'url_ttl_s': min(300, cfg['user_get_ttl_ms']//1000)}

    def validate(self, uid: UUID, sid: UUID, basis: dict) -> None:
        with self.db.backend() as cur:
            cur.execute("SELECT p.active,s.state_version, (SELECT jsonb_agg(jsonb_build_object('id',a.id,'version',a.resource_version,"
                        "'availability',a.availability)) FROM rav_pilot_session_source ss JOIN rav_pilot_source_asset a ON a.id=ss.source_id "
                        "WHERE ss.session_id=s.id) AS sources FROM rav_pilot_profile p JOIN rav_pilot_session s ON s.owner_id=p.user_id "
                        "WHERE p.user_id=%s AND s.id=%s", (uid, sid))
            r = cur.fetchone()
        if not r or not r['active']:
            raise ServiceError('account_disabled' if r else 'not_found', 'phiên/tài khoản không còn khả dụng', 401 if r else 404)
        expected = {s['source_id']: s['resource_version'] for s in basis['pool']}
        if r['state_version'] != basis['state_version'] or {s['id']: s['version'] for s in r['sources'] or []} != expected \
                or any(s['availability'] != 'READY' for s in r['sources'] or []):
            raise ServiceError('state_version_conflict', 'phiên đã đổi; mở lại phiên trước khi tiếp tục', 409)

    def save(self, actor: ActorContext, sid: UUID, body: dict, proof: dict | None, key: str) -> dict:
        uid = require_user(actor)
        digest = canonical_hash({'session_id': str(sid), **body})
        with self.db.backend() as cur:
            cur.execute('SELECT public.rav_pilot_checkpoint(%s,%s,%s,%s,%s,%s,%s) AS result',
                        (uid, sid, Jsonb(body), Jsonb(proof) if proof is not None else None, key, digest, actor.actor_string))
            result = cur.fetchone()['result']
            if code := result.get('error'):
                raise ServiceError(code, {'state_version_conflict': 'phiên đã lưu ở tab khác; tải lại để đối chiếu',
                                         'batch_expired': 'batch hết cache; đề xuất lại',
                                         'invalid_state': 'giữ/bỏ không hợp lệ',
                                         'account_disabled': 'tài khoản không khả dụng'}.get(code, code),
                                   401 if code == 'account_disabled' else 404 if code == 'not_found' else 422 if code == 'invalid_state' else 409)
        return result


def required_features(recipe: str, basis: dict) -> None:
    entry = next((r for r in catalog() if r['name'] == recipe), None)
    if entry is None or entry.get('background'):
        raise ServiceError('recipe_not_available', 'strategy requires background execution', 422)
    missing = [{'source_id': s['source_id'], 'feature': f} for s in basis['pool'] for f in entry.get('needs', []) if f not in s['features']]
    if missing:
        raise ServiceError('feature_not_ready', 'chuẩn bị đặc trưng trước khi đề xuất', 409, {'missing': missing})
