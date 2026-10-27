"""§21.39 operator-only read-only samples using existing immutable prepared objects.

Input: own private video ID + idempotency key. Output: hidden SYSTEM source ID, no job/S3 I/O.
Narrow DB RPC atomically creates metadata aliases and pins backing objects against deletion.
"""
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from ..errors import ServiceError
from . import pilot_frames
from .pilot_db import canonical_hash
from .pilot_features import input_hash
from .pilot_operations import PilotOperations


class SampleReferenceResult(BaseModel):
    source_id: UUID
    existing: bool


def reference_sample(db, actor, source_id: UUID, key: str) -> SampleReferenceResult:
    uid = PilotOperations(db).operator(actor)
    key, digest = canonical_hash(key), canonical_hash(str(source_id))
    with db.backend() as cur:
        db.lock_profile(cur, uid)
        old = db.receipt(cur, uid, 'REFERENCE_SAMPLE', key, digest)
        if old:
            return SampleReferenceResult(**old)
        src = db.lock_sources(cur, uid, [source_id])[source_id]
        if src['scope'] != 'PRIVATE' or src['owner_id'] != uid:
            raise ServiceError('not_found', 'chỉ chọn video trong kho riêng của admin', 404)
        if src['availability'] != 'READY' or src['kind'] != 'VIDEO':
            raise ServiceError('source_not_ready', 'cần video READY', 409)
        frame_set = pilot_frames.ready_set_in_tx(cur, source_id, src['raw_sha256'])
        if not frame_set:
            raise ServiceError('feature_not_ready', 'cần chuẩn bị FRAMES trước', 409)
        features = {}
        for feature in ('DINO', 'CLIP'):
            cur.execute("SELECT id FROM rav_pilot_artifact WHERE source_id=%s AND owner_id=%s AND scope='PRIVATE' "
                        "AND kind='FEATURE' AND variant=%s AND input_hash=%s AND state='READY'",
                        (source_id, uid, feature, input_hash(src, feature)))
            row = cur.fetchone()
            if not row:
                raise ServiceError('feature_not_ready', f'cần chuẩn bị {feature} đúng phiên bản trước', 409)
            features[feature] = row['id']
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('rav-sample:'||%s,0))", (src['core_source_id'],))
        cur.execute("SELECT id,raw_sha256 FROM rav_pilot_source_asset WHERE scope='SYSTEM' AND core_source_id=%s "
                    "AND availability<>'DELETED'", (src['core_source_id'],))
        existing = cur.fetchone()
        if existing and existing['raw_sha256'] != src['raw_sha256']:
            raise ServiceError('result_invalid', 'core ID trùng nhưng SHA nguồn mẫu khác', 422)
        target = uuid5(NAMESPACE_URL, f"rav-pilot-system-source:{src['core_source_id']}")
        if existing:
            target = existing['id']
        else:
            cur.execute('SELECT rav_pilot_reference_sample(%s,%s,%s,%s,%s,%s) AS id',
                        (uid, source_id, target, frame_set, features['DINO'], features['CLIP']))
            target = cur.fetchone()['id']
        result = SampleReferenceResult(source_id=target, existing=bool(existing))
        db.write_receipt(cur, uid, 'REFERENCE_SAMPLE', key, digest, result)
        return result
