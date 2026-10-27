"""Opt-in VPS real TEXT owner HTTP + YOLO/upload/media; isolated PG/Moto, seeded image embeddings.

RAV_RUN_INTERACTIVE_CAPACITY=1 enables. CLIP text/YOLO real public models; no semantic quality/cloud SLA claim.
"""
import copy
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.rav.store.pilot_analysis import DetectIn, PilotAnalysis
from src.rav.store.pilot_curation import catalog
from src.rav.store.pilot_db import CreateSessionInput, PilotDB
from tests.conftest import make_video
from tests.test_pilot_analysis import YOLO, _model
from tests.test_pilot_curation import seed_features
from tests.test_pilot_frames_e2e import Api, media, upload
from tests.test_pilot_frames_e2e import e2e as frames_fixture  # noqa: F401
from tests.test_pilot_media import actor, seed_frames, seed_source, unit
from tests.test_pilot_worker_spawn import _free_port, _wait

pytestmark = pytest.mark.skipif(os.environ.get('RAV_RUN_INTERACTIVE_CAPACITY') != '1' or not YOLO.exists(), reason='opt-in VPS model capacity')


@pytest.fixture
def stack(request, monkeypatch):
    port = _free_port()
    for k, v in {'RAV_FRAMES_E2E_CPU_LANES': '1', 'RAV_FRAMES_E2E_CPU_FREE': '1', 'RAV_INTERACTIVE_ENABLED': '1',
                 'RAV_INTERACTIVE_PORT': str(port), 'RAV_INTERACTIVE_URL': f'http://127.0.0.1:{port}',
                 'RAV_INTERACTIVE_TOKEN': 'x'*32, 'HF_HUB_OFFLINE': '1'}.items():
        monkeypatch.setenv(k, v)
    result = request.getfixturevalue('frames_fixture')
    def ready():
        try:
            return requests.get(f'http://127.0.0.1:{port}/health', timeout=1).status_code == 200
        except requests.RequestException:
            return False
    _wait(ready, 120, 'TEXT interactive model warm/listening')
    return result


def test_real_http_text_under_bulk_yolo_and_media(stack):
    pg, s3, a, b = stack['pg'], stack['s3'], stack['a'], stack['b']
    db = PilotDB(pg.api_url)
    video = make_video(stack['tmp']/'source.mp4', seconds=21, fps=20, size=(640, 360))
    sessions, sources = [], []
    for owner in (a, b):
        sid, core, frames = seed_source(pg, s3, video, owner)
        seed_frames(pg, s3, sid, owner, video, frames)
        seed_features(pg, s3, sid, core, video, owner, ('DINO', 'CLIP'))
        sessions.append(db.create_session(actor(owner), CreateSessionInput(name='HTTP capacity', goal='test', target_k=100,
                                                                          pool_source_ids=[sid]), str(owner)))
        sources.append((sid, core, frames))
    aa, ab = Api(stack['base'], a), Api(stack['base'], b)
    for api, session in zip((aa, ab), sessions):
        api.ok('POST', f'/sessions/{session.id}/interactive/open')
    from uuid import uuid4
    base = ab.ok('POST', f'/sessions/{sessions[1].id}/interactive/propose', {'request_id': str(uuid4()), 'recipe': 'random',
                 'seed': 1, 'batch_size': 16, 'expected_state_version': 1})
    saved = ab.ok('POST', f'/sessions/{sessions[1].id}/checkpoint', {'batch_id': base['batch_id'], 'kept_ids': [],
                  'dropped_ids': [], 'expected_state_version': 1}, key='save')
    mid = _model(SimpleNamespace(pg=pg, s3=s3))
    config = copy.deepcopy(next(r['config'] for r in catalog() if r['name'] == 'text_diversity'))
    durations = []
    bulk_video = make_video(stack['tmp']/'bulk.mp4', seconds=90, fps=20, size=(640, 360), shift=19)
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(upload, ab, bulk_video.read_bytes(), 'bulk.mp4')
            job, status = PilotAnalysis(db).request_detections(actor(b), sessions[1].id, saved['proposal']['n'], DetectIn(model_id=mid), 'yolo')
            assert status == 202
            _wait(lambda: pg.sql("SELECT count(*) AS n FROM rav_pilot_job WHERE kind='VALIDATE_UPLOAD' AND state='RUNNING'")[0]['n'] == 1, 30, 'BULK running')
            for i in range(20):
                for objective in config['objectives']:
                    if objective['type'] == 'text_match':
                        objective.setdefault('params', {}).update(texts=[f'cars driving on road {i}'], min_similarity=-1)
                body = {'request_id': str(uuid4()), 'recipe': 'text_diversity', 'config': config, 'seed': i,
                        'batch_size': 16, 'expected_state_version': 1}
                start = time.perf_counter()
                result = aa.ok('POST', f'/sessions/{sessions[0].id}/interactive/propose', body)
                durations.append((time.perf_counter()-start)*1000)
                assert len(result['proposal']['picks']) == 16 and len(result['images']) == 32
            sid, core, frames = sources[0]
            clip, clip_ms = media(aa, str(sid), unit(core, frames[0]), 'context')
            assert clip['state'] == 'READY'
            assert future.result(timeout=180)[0]['state'] == 'READY'
            _wait(lambda: (j := ab.ok('GET', f'/jobs/{job.job.id}'))['state'] in ('SUCCEEDED', 'FAILED') and j, 180, 'YOLO done')
            assert ab.ok('GET', f'/jobs/{job.job.id}')['state'] == 'SUCCEEDED'
        assert not pg.sql("SELECT id FROM rav_pilot_job WHERE kind='PROPOSE'")
        assert not pg.sql("SELECT id FROM rav_pilot_quota_hold WHERE resource='CPU'")
        assert not pg.sql("SELECT id FROM rav_pilot_usage_receipt WHERE resource='CPU'")
        p95 = sorted(durations)[18]
        assert p95 < 2000, durations
        target = Path('output/interactive/capacity.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'scope': 'isolated PG/Moto, actual HTTP TEXT CLIP/YOLO; synthetic video/seeded image embeddings',
                          'warm_http_ms': durations, 'p95_ms': p95, 'clip_ms': clip_ms, 'proposal_jobs': 0, 'cpu_holds_receipts': 0}, indent=2))
    finally:
        db.close()
