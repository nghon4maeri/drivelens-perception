"""Opt-in full-file upload/preprocess with real videos and isolated API/PG/Moto/worker (§21.27).

RAV_LARGE_TEST_VIDEOS is a JSON list of paths. No real S3/private cloud credentials; no truncating the supplied videos.
"""
import copy
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
import requests

from src.rav.actor import ActorContext
from src.rav.store.pilot_db import CreateSessionInput, PilotDB
from src.rav.worker.handlers import _decoder, _probe
from src.rav.worker.interactive import Interactive, ProposalIn, SessionIn
from tests.test_pilot_frames_e2e import Api, grid
from tests.test_pilot_frames_e2e import e2e as frames_fixture  # noqa: F401
from tests.test_pilot_worker_spawn import _children, _wait

VIDEOS = json.loads(os.environ.get('RAV_LARGE_TEST_VIDEOS', '[]'))
pytestmark = pytest.mark.skipif(not VIDEOS, reason='opt-in full large videos')


@pytest.fixture
def large_stack(request, monkeypatch):
    monkeypatch.setenv('RAV_FRAMES_E2E_CPU_LANES', '1')
    monkeypatch.setenv('RAV_FRAMES_E2E_CPU_FREE', '1')
    monkeypatch.setenv('RAV_INTERACTIVE_ENABLED', '0')
    result = request.getfixturevalue('frames_fixture')
    config = copy.deepcopy(result['pg'].sql('SELECT config FROM rav_pilot_quota_policy WHERE version=%s', (result['policy_version'],))[0]['config'])
    config.update(multipart_upload_enabled=True, max_video_bytes=5 << 30, max_duration_ms=3600000,
                  max_decoded_pixels=2*10**12, storage_user_bytes=32 << 30, storage_global_bytes=128 << 30, upload_ttl_ms=86400000)
    config['chunk_bounds_by_kind']['PROPOSE']['max_units'] = 20000
    for kind in ('VALIDATE_UPLOAD', 'PREPARE_FRAMES'):
        config['chunk_bounds_by_kind'][kind] = {'frame_batch_size': 64, 'frame_put_concurrency': 8, 'max_frame_bytes': 8 << 30}
        config['run_deadline_ms_by_kind'][kind] = 3600000
    policy = result['pg'].policy(config=config)
    result['pg'].sql('UPDATE rav_pilot_profile SET policy_id=%s WHERE user_id=ANY(%s)', (policy, [result['a'], result['b']]))
    return result


@pytest.mark.parametrize('name', VIDEOS)
def test_full_video_multipart_frames_and_proposal(large_stack, name):
    s = large_stack
    api = Api(s['base'], s['a'])
    path = Path(name)
    assert path.is_file()
    video = _probe(path)
    frames = _decoder().frames(video)
    size = path.stat().st_size
    started = time.perf_counter()
    created = api.ok('POST', '/uploads', {'filename': path.name, 'declared_bytes': size}, key=str(uuid4()))
    assert created['transfer']['method'] == 'MULTIPART'
    info = api.ok('POST', f"/uploads/{created['id']}/multipart")
    stop = threading.Event()
    rss = {'BULK': 0, 'pytest_api_moto': 0}
    def monitor():
        while not stop.wait(.5):
            for label, pid in [('BULK', (_children(s['wd']).get('BULK') or {}).get('pid')), ('pytest_api_moto', os.getpid())]:
                try:
                    if pid:
                        rss[label] = max(rss[label], psutil.Process(pid).memory_info().rss)
                except psutil.Error:
                    pass
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    def put(part):
        with path.open('rb') as f:
            f.seek((part['part_number']-1)*info['part_size'])
            data = f.read(info['part_size'])
        response = requests.put(part['url'], data=data, timeout=120)
        assert response.status_code == 200, response.status_code
    try:
        for offset in range(0, info['part_count'], 4):
            numbers = list(range(offset+1, min(info['part_count']+1, offset+5)))
            parts = api.ok('POST', f"/uploads/{created['id']}/multipart/parts", {'part_numbers': numbers})['parts']
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(put, parts))
            if offset == 0:
                resumed = api.ok('POST', f"/uploads/{created['id']}/multipart")
                assert len(resumed['parts']) == len(numbers)  # reconnect/restart transport sees durable S3 parts
        transfer_ms = (time.perf_counter()-started)*1000
        api.ok('POST', f"/uploads/{created['id']}/complete")
        done = _wait(lambda: (u := api.ok('GET', f"/uploads/{created['id']}"))['state'] in ('READY', 'FAILED') and u, 3600, 'full video READY')
        assert done['state'] == 'READY', done
        ready_ms = (time.perf_counter()-started)*1000
        src = api.ok('GET', f"/sources/{done['source_id']}")
        assert src['frames']['ready'] and src['core_source_id'] == video.id
        sets = s['pg'].sql("SELECT * FROM rav_pilot_frame_set WHERE source_id=%s AND state='READY'", (src['id'],))
        assert len(sets) == 1 and sets[0]['n_units'] == len(frames) and sets[0]['object_count'] == len(frames)*2
        anchors = [f'{video.id}:{f.t:.3f}-{f.t:.3f}' for f in (frames[0], frames[len(frames)//2], frames[-1])]
        images, _, grid_ms = grid(api, src['id'], anchors)
        assert all(i['state'] == 'READY' and i['job_id'] is None for i in images)
        db = PilotDB(s['pg'].api_url)
        session = db.create_session(ActorContext(user_id=s['a']), CreateSessionInput(name='large QA', goal='test', target_k=100, pool_source_ids=[src['id']]), str(uuid4()))
        db.close()
        worker_db = PilotDB(s['pg'].worker_url)
        service = Interactive(worker_db, s['s3'], s['tmp']/'interactive')
        t = time.perf_counter()
        service.open(SessionIn(owner_id=s['a'], session_id=session.id))
        proposal = service.run(ProposalIn(owner_id=s['a'], session_id=session.id, request_id=uuid4(), recipe='random', batch_size=16, seed=7, expected_state_version=1))
        assert len(proposal['proposal']['picks']) == 16 and len(proposal['images']) == 32
        worker_db.close()
        out = Path('output/large-upload')
        out.mkdir(exist_ok=True, parents=True)
        report = {'scope': 'full actual video, isolated HTTP API/Postgres/Moto/real worker processes; not cloud S3',
                  'name': path.name, 'bytes': size, 'duration_s': video.duration_s, 'fps': video.fps,
                  'n_units': len(frames), 'stored_images': len(frames)*2, 'frame_bytes': sets[0]['total_bytes'],
                  'transfer_ms': transfer_ms, 'total_ready_ms': ready_ms, 'three_anchor_grid_ms': grid_ms,
                  'cold_random_proposal_ms': (time.perf_counter()-t)*1000, 'peak_rss_bytes': rss}
        (out/(path.stem+'.json')).write_text(json.dumps(report, indent=2))
    finally:
        stop.set()
        thread.join(5)
