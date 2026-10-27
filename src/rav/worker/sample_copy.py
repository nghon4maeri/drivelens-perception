"""Copy a prepared private video into independent SYSTEM objects, without decode or embedding inference.

Input: frozen PREPARE_FRAMES sample_copy payload and fenced lease. Output: metadata for atomic publish.
Uses existing object intents/GLOBAL holds/cleanup; only server-side S3 transfers, bounded concurrency.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from ..errors import ServiceError
from ..store import pilot_frames, pilot_objects
from ..store.pilot_db import DEFAULT_SAMPLE_SPEC
from ..store.pilot_features import FIELDS
from ..store.pilot_runtime import PilotRuntime, Progress


def copy_sample(storage, db, lease, stop):
    p, runtime = lease.payload, PilotRuntime(db)
    src = p['source']

    def cancelled(*args):
        if stop.is_set():
            raise ServiceError('cancelled', 'đã dừng sao chép mẫu', 409)

    cancelled()
    from ..core.registry import REGISTRY, discover
    discover()
    for art in p['features']:
        if set(art['manifest'].get('field_keys', {})) != set(FIELDS[art['variant']]):
            raise ServiceError('feature_not_ready', 'manifest thiếu FieldKey của embedding', 409)
        for name, key in art['manifest'].get('field_keys', {}).items():
            if asdict(REGISTRY.feature_for(name)().key) != key:
                raise ServiceError('feature_not_ready', 'embedding khác phiên bản component hiện tại', 409)
    with db.backend() as cur:
        cur.execute("SELECT f.variant,f.frame_idx,f.unit_id,f.object_key,f.sha256,f.size_bytes,f.width,f.height "
                    "FROM rav_pilot_frame f JOIN rav_pilot_frame_set s ON s.id=f.frame_set_id "
                    "WHERE s.id=%s AND s.source_id=%s AND s.owner_id=%s AND s.scope='PRIVATE' AND s.state='READY' "
                    "AND s.raw_sha256=%s AND s.sample_spec_hash=%s AND s.encoding_version=%s "
                    "ORDER BY f.frame_idx,f.variant", (UUID(p['frame_set_id']), UUID(p['source_id']), UUID(p['owner_id']),
                        src['raw_sha256'], DEFAULT_SAMPLE_SPEC['spec_hash'], pilot_frames.ENCODING_VERSION))
        original_frames = cur.fetchall()
    n = src['metadata']['n_units']
    if len(original_frames) != 2 * n:
        raise ServiceError('feature_not_ready', 'bộ ảnh gốc không đủ hoặc sai phiên bản', 409)
    frame_set_id, seed = uuid4(), f'copy-{lease.attempt_id}'
    prefix = f'{storage.prefix}/system/{seed}/'
    raw_key = storage.system_raw_key(seed, src['core_source_id'], src['raw_sha256'], PurePosixPath(src['raw_key']).suffix[1:] or 'video')
    objects = [{'from': src['raw_key'], 'to': raw_key, 'sha': src['raw_sha256'], 'bytes': src['size_bytes']}]
    frames, features = [], []
    for row in original_frames:
        if row['size_bytes'] > p['frames']['max_frame_bytes']:
            raise ServiceError('result_invalid', 'ảnh vượt giới hạn policy hiện tại', 422)
        key = storage.system_frame_key(seed, src['core_source_id'], frame_set_id, row['frame_idx'], row['variant'], row['sha256'])
        objects.append({'from': row['object_key'], 'to': key, 'sha': row['sha256'], 'bytes': row['size_bytes']})
        frames.append({**row, 'object_key': key})
    for art in p['features']:
        key = storage.system_feature_key(seed, src['core_source_id'], art['variant'], art['sha256'])
        objects.append({'from': art['object_key'], 'to': key, 'sha': art['sha256'], 'bytes': art['size_bytes']})
        features.append({'id': str(uuid4()), 'object_key': key, 'variant': art['variant'],
                         'sha256': art['sha256'], 'size_bytes': art['size_bytes']})

    def copy_one(obj):
        cancelled()
        storage.copy_verified(obj['from'], obj['to'], obj['sha'], obj['bytes'], callback=cancelled)
        return obj['to']

    size = p['frames']['frame_batch_size']
    concurrency = min(4, p['frames']['frame_put_concurrency'])
    runtime.update_progress(lease, Progress(sequence=1, phase='COPYING_SAMPLE', done=0, total=len(objects)))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, len(objects), size):
            cancelled()
            batch = objects[start:start + size]
            pilot_objects.intent_batch(db, lease, [(x['to'], x['sha'], x['bytes']) for x in batch],
                                       f'sample-copy:{lease.attempt_id}:{start}', reuse_reserved=True)
            keys = list(pool.map(copy_one, batch))
            pilot_objects.written_batch(db, lease, keys)
            runtime.update_progress(lease, Progress(sequence=2 + start // size, phase='COPYING_SAMPLE',
                done=min(start + size, len(objects)), total=len(objects)))
    cancelled()
    return {'sample_copy': {'prefix': prefix, 'raw_key': raw_key, 'features': features, 'frames': {
        'frame_set_id': str(frame_set_id), 'sample_spec_hash': DEFAULT_SAMPLE_SPEC['spec_hash'],
        'encoding_version': pilot_frames.ENCODING_VERSION, 'n_units': n, 'items': frames,
        'total_bytes': sum(f['size_bytes'] for f in frames)}}, 'cleanup_done': True}
