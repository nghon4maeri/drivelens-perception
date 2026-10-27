"""Admin materialization of a private prepared video into an independent SYSTEM sample (§21.38).

API freezes identities/enqueues PREPARE_FRAMES copy mode; worker publication is atomic and fenced.
No ML, S3 I/O or owner mutation in this module. Original source remains pinned until job cleanup.
"""
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from ..errors import ServiceError
from . import pilot_frames, pilot_objects, pilot_storage
from .pilot_db import DEFAULT_SAMPLE_SPEC, canonical_hash
from .pilot_features import input_hash
from .pilot_operations import PilotOperations
from .pilot_runtime import EnqueueSpec, PilotRuntime, _job_dto


def request_copy(db, actor, source_id, key):
    uid = PilotOperations(db).operator(actor)
    runtime = PilotRuntime(db)
    stored_key = 'sample-copy:' + canonical_hash(key)
    with db.backend() as cur:
        profile = db.lock_profile(cur, uid)
        src = db.lock_sources(cur, uid, [source_id])[source_id]
        if src['scope'] != 'PRIVATE' or src['owner_id'] != uid:
            raise ServiceError('not_found', 'chỉ chọn video trong kho riêng của admin', 404)
        target_id = uuid5(NAMESPACE_URL, f"rav-pilot-system-source:{src['core_source_id']}")
        cur.execute("SELECT * FROM rav_pilot_job WHERE owner_id=%s AND kind='PREPARE_FRAMES' AND idempotency_key=%s",
                    (uid, stored_key))
        old = cur.fetchone()
        if old:
            if old['payload'].get('source_id') != str(source_id) or not old['payload'].get('sample_copy'):
                raise ServiceError('idempotency_conflict', 'Idempotency-Key đã dùng cho nguồn khác', 409)
            return {'source_id': str(target_id), 'job': _job_dto(old), 'existing': False}
        if src['availability'] != 'READY' or src['kind'] != 'VIDEO':
            raise ServiceError('source_not_ready', 'cần video READY', 409)
        frame_set = pilot_frames.ready_set_in_tx(cur, source_id, src['raw_sha256'])
        if not frame_set:
            raise ServiceError('feature_not_ready', 'cần chuẩn bị FRAMES trước', 409)
        features = []
        for feature in ('DINO', 'CLIP'):
            cur.execute("SELECT id,variant,input_hash,object_key,sha256,size_bytes,manifest FROM rav_pilot_artifact "
                        "WHERE source_id=%s AND owner_id=%s AND scope='PRIVATE' AND kind='FEATURE' "
                        "AND variant=%s AND input_hash=%s AND state='READY'", (source_id, uid, feature, input_hash(src, feature)))
            row = cur.fetchone()
            if not row:
                raise ServiceError('feature_not_ready', f'cần chuẩn bị {feature} đúng phiên bản trước', 409)
            features.append({**row, 'id': str(row['id'])})
        cur.execute("SELECT id,raw_sha256 FROM rav_pilot_source_asset WHERE core_source_id=%s AND scope='SYSTEM' AND availability<>'DELETED'",
                    (src['core_source_id'],))
        if existing := cur.fetchone():
            if existing['raw_sha256'] != src['raw_sha256']:
                raise ServiceError('result_invalid', 'core ID trùng nhưng SHA nguồn mẫu khác', 422)
            return {'source_id': str(existing['id']), 'job': None, 'existing': True}
        _, cfg = runtime._policy(cur, profile)
        if src['size_bytes'] > cfg['max_video_bytes']:
            raise ServiceError('upload_invalid', 'video vượt giới hạn policy hiện tại', 422)
        bounds = pilot_frames.bounds(cfg, 'PREPARE_FRAMES')
        frozen = {k: src[k] for k in ('core_source_id', 'raw_key', 'raw_sha256', 'size_bytes', 'metadata',
                                      'name', 'description', 'resource_version')}
        job = runtime.enqueue_in_tx(cur, uid, EnqueueSpec(kind='PREPARE_FRAMES', resource_id=source_id,
            idempotency_key=stored_key, payload={'sample_copy': True, 'target_scope': 'SYSTEM',
                'owner_id': str(uid), 'source_id': str(source_id), 'target_id': str(target_id), 'source': frozen,
                'frame_set_id': str(frame_set), 'features': features, 'frames': bounds}), [source_id])
        return {'source_id': str(target_id), 'job': job, 'existing': False}


def publish_in_tx(cur, job, lease, outputs):
    """Publish only byte-preserving copies of the frozen source; every object needs its attempt receipt."""
    p, result = job['payload'], outputs['sample_copy']
    src = p['source']
    cur.execute('SELECT * FROM rav_pilot_source_asset WHERE id=%s FOR UPDATE', (UUID(p['source_id']),))
    current = cur.fetchone()
    if (not current or current['availability'] != 'READY' or current['owner_id'] != job['owner_id']
            or current['scope'] != 'PRIVATE' or current['resource_version'] != src['resource_version']
            or current['raw_sha256'] != src['raw_sha256']):
        raise ServiceError('source_unavailable', 'nguồn gốc đã đổi trong lúc sao chép', 409)
    cur.execute("SELECT 1 FROM rav_pilot_source_asset WHERE core_source_id=%s AND scope='SYSTEM' AND availability<>'DELETED'",
                (src['core_source_id'],))
    if cur.fetchone():
        raise ServiceError('sample_already_exists', 'đã có bản mẫu; tải lại catalog, object của attempt này sẽ được dọn', 409)
    prefix = result['prefix']
    expected_prefix = f"/system/copy-{lease.attempt_id}/"
    if expected_prefix not in prefix or not result['raw_key'].startswith(prefix):
        raise ServiceError('result_invalid', 'key bản mẫu không thuộc attempt', 422)
    target = {**src, 'id': UUID(p['target_id']), 'scope': 'SYSTEM', 'owner_id': None}
    frames = result['frames']
    total = src['size_bytes'] + pilot_frames.validate_set(target, frames)
    cur.execute('SELECT variant,frame_idx,unit_id,sha256,size_bytes,width,height FROM rav_pilot_frame WHERE frame_set_id=%s',
                (UUID(p['frame_set_id']),))
    originals = {(r['variant'], r['frame_idx']): r for r in cur.fetchall()}
    if len(originals) != len(frames['items']) or any(
        any(item[k] != original[k] for k in original)
        for item in frames['items'] for original in [originals.get((item['variant'], item['frame_idx']), {'sha256': None})]
    ):
        raise ServiceError('result_invalid', 'ảnh copy không giữ exact identity của bộ gốc', 422)
    if any(not i['object_key'].startswith(prefix) for i in frames['items']):
        raise ServiceError('result_invalid', 'ảnh ngoài namespace attempt', 422)
    if len(result['features']) != len(p['features']):
        raise ServiceError('result_invalid', 'thiếu đặc trưng', 422)
    for feature, frozen in zip(result['features'], p['features']):
        if (feature['variant'] != frozen['variant'] or feature['sha256'] != frozen['sha256']
                or feature['size_bytes'] != frozen['size_bytes'] or not feature['object_key'].startswith(prefix)):
            raise ServiceError('result_invalid', 'đặc trưng copy lệch nguồn', 422)
        total += feature['size_bytes']
    pilot_objects.accept_in_tx(cur, lease, result['raw_key'], src['raw_sha256'], src['size_bytes'])
    cur.execute("INSERT INTO rav_pilot_source_asset (id,core_source_id,kind,scope,owner_id,protected,name,description,raw_key,"
                "raw_sha256,size_bytes,metadata,provenance,availability,published) "
                "VALUES (%s,%s,'VIDEO','SYSTEM',NULL,true,%s,%s,%s,%s,%s,%s,%s,'READY',false) "
                "ON CONFLICT DO NOTHING RETURNING id",
                (target['id'], src['core_source_id'], src['name'], src['description'], result['raw_key'], src['raw_sha256'],
                 src['size_bytes'], Jsonb(src['metadata']), Jsonb({'copied_from': p['source_id'], 'job_id': str(job['id'])})))
    if not cur.fetchone():
        raise ServiceError('sample_already_exists', 'nguồn mẫu vừa được tạo bởi job khác; tải lại catalog', 409)
    pilot_frames.insert_set_in_tx(cur, target, frames, job['id'], lease.attempt_id)
    for feature, frozen in zip(result['features'], p['features']):
        manifest = {**frozen['manifest'], 'source_id': p['target_id'], 'core_source_id': src['core_source_id'],
                    'feature': frozen['variant'], 'input_hash': frozen['input_hash'], 'npz_sha256': frozen['sha256'],
                    'spec_hash': DEFAULT_SAMPLE_SPEC['spec_hash'], 'n_units': src['metadata']['n_units']}
        cur.execute("INSERT INTO rav_pilot_artifact (id,scope,owner_id,source_id,job_id,attempt_id,kind,variant,input_hash,"
                    "object_key,sha256,size_bytes,state,policy_version,manifest) "
                    "VALUES (%s,'SYSTEM',NULL,%s,%s,%s,'FEATURE',%s,%s,%s,%s,%s,'READY',%s,%s)",
                    (UUID(feature['id']), target['id'], job['id'], lease.attempt_id, frozen['variant'], frozen['input_hash'],
                     feature['object_key'], frozen['sha256'], frozen['size_bytes'], job['policy_version'], Jsonb(manifest)))
    pilot_storage.persist_job_bytes(cur, job['id'], total)
