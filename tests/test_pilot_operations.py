"""Operations isolation, browser trust boundary and collector evidence on real PostgreSQL (§21.19)."""
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from starlette.testclient import TestClient

from src.rav.api.auth import AuthConfig
from src.rav.api.pilot import create_pilot_app
from src.rav.store.pilot_db import PilotDB
from src.rav.store.pilot_operations import safe_event
from src.rav.store.pilot_uploads import UploadIn
from src.rav.telemetry import TimingCollector
from tests.test_pilot_api_v2 import ISS, JWK, auth
from tests.test_pilot_uploads import actor, up, uploaded  # noqa: F401


@pytest.fixture
def ops(up, monkeypatch):  # noqa: F811
    monkeypatch.setenv('RAV_PILOT_OPERATOR_IDS', str(up['a']))
    monkeypatch.delenv('RAV_TRACE_ENABLED', raising=False)
    up['client'] = TestClient(create_pilot_app(up['pg'].api_url, AuthConfig(expected_issuer=ISS), raw_jwks={'keys': [JWK]}))
    up['upload'] = up['svc'].create(actor(up['a']), UploadIn(filename='private.mp4', declared_bytes=1000), 'ops')
    return up


def span(**extra):
    return {'id': str(uuid4()), 'at': datetime.now(UTC).isoformat(), 'stage': 'browser.s3.transfer',
            'wall_ms': 1200, 'bytes': 1000, 'outcome': 'ok', **extra}


def test_operations_auth_and_cross_owner_metadata_only(ops):
    c = ops['client']
    assert c.get('/api/v2/operations').status_code == 401
    assert c.get('/api/v2/operations', headers=auth(ops['b'])).status_code == 403
    assert c.get('/api/v2/me', headers=auth(ops['b'])).json()['operations_read'] is False
    assert c.get('/api/v2/me', headers=auth(ops['a'])).json()['operations_read'] is True
    data = c.get('/api/v2/operations', headers=auth(ops['a']))
    assert data.status_code == 200, data.text
    assert data.json()['uploads'][0]['id'] == str(ops['upload'].id)
    assert all(key not in data.text for key in ('staging_key', 'private.mp4', 'raw_key', 'DATABASE_URL'))
    disabled = ops['pg'].profile(active=False)
    assert c.get('/api/v2/operations', headers=auth(disabled)).status_code == 401


def test_log_pages_are_bounded_frozen_and_sanitized(ops):
    c, pg = ops['client'], ops['pg']
    at = datetime.now(UTC) - timedelta(minutes=1)
    ids = [uuid4() for _ in range(5)]
    for uid in ids:
        pg.sql('INSERT INTO rav_pilot_timing_event(id,received_at,observed_at,source,stage,data) '
               'VALUES (%s,%s,%s,%s,%s,%s)',
               (uid, at, at, 'worker', 'worker.handler', Jsonb({'wall_ms': 12, 'status': 503, 'sql': 'secret',
                                                             'url': 'secret', 'filename': 'secret', 'message': 'secret'})))
    headers = auth(ops['a'])
    first = c.get('/api/v2/operations/logs?limit=2', headers=headers).json()
    assert len(first['items']) == 2 and first['next_cursor']
    assert all(r['data'] == {'wall_ms': 12, 'status': 503} for r in first['items'])
    # Insertion after the first page must not move the page boundary or enter its frozen window.
    pg.sql("INSERT INTO rav_pilot_timing_event(id,observed_at,source,stage,data) VALUES (%s,now(),'api','api.error','{}')", (uuid4(),))
    rows, page = first['items'], first
    while page['next_cursor']:
        response = c.get('/api/v2/operations/logs', params={'limit': 2, 'cursor': page['next_cursor']}, headers=headers)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['window_end'] == first['window_end'] and page['window_start'] == first['window_start']
        rows.extend(page['items'])
    assert [r['id'] for r in rows] == [str(x) for x in sorted(ids, reverse=True)]
    changed = c.get('/api/v2/operations/logs', params={'cursor': first['next_cursor'], 'source': 'api'}, headers=headers)
    assert changed.status_code == 422


def test_log_filters_and_access(ops):
    c, pg = ops['client'], ops['pg']
    at = datetime.now(UTC)
    for source, age, data in [('api', 0, {'status': 401}), ('api', 0, {'status': 'not-a-number'}),
                              ('worker', 0, {'outcome': 'FAILED'}), ('worker', 2, {'error_code': 'failed'}),
                              ('worker', 0, {'status': 200})]:
        pg.sql('INSERT INTO rav_pilot_timing_event(id,received_at,observed_at,source,stage,data) '
               'VALUES (%s,%s,%s,%s,%s,%s)',
               (uuid4(), at - timedelta(hours=age), at, source, source+'.step', Jsonb(data)))
    url = '/api/v2/operations/logs'
    assert c.get(url).status_code == 401
    assert c.get(url, headers=auth(ops['b'])).status_code == 403
    assert c.get(url, headers=auth(pg.profile(active=False))).status_code == 401
    headers = auth(ops['a'])
    all_errors = c.get(url, params={'errors_only': 'true'}, headers=headers).json()['items']
    assert len(all_errors) == 2
    worker = c.get(url, params={'source': 'worker', 'hours': 6, 'errors_only': 'true'}, headers=headers).json()['items']
    assert len(worker) == 2 and all(r['source'] == 'worker' for r in worker)
    for params in ({'limit': 101}, {'limit': 0}, {'hours': 25}, {'hours': 0}, {'source': 'sql'}, {'cursor': 'bad'},
                   {'cursor': 'x'*1025}):
        assert c.get(url, params=params, headers=headers).status_code == 422


def test_browser_owner_boundary_replay_and_trace(ops):
    c, uid = ops['client'], ops['upload'].id
    url = f'/api/v2/uploads/{uid}/timings'
    payload = {'events': [span()]}
    assert c.post(url, json=payload, headers=auth(ops['b'])).status_code == 404
    assert c.post(url, json=payload, headers=auth(ops['a'])).json() == {'accepted': 1}
    assert c.post(url, json=payload, headers=auth(ops['a'])).json() == {'accepted': 0}
    trace = c.get(f'/api/v2/operations/traces/upload/{uid}', headers=auth(ops['a'])).json()
    assert len(trace['events']) == 1 and trace['events'][0]['source'] == 'browser'
    assert trace['resource']['state'] == 'CREATED' and trace['attempts'] == []
    assert c.get(f'/api/v2/operations/traces/upload/{uid}', headers=auth(ops['b'])).status_code == 403
    assert c.get(f'/api/v2/operations/traces/job/{uuid4()}', headers=auth(ops['a'])).status_code == 404


@pytest.mark.parametrize('patch', [{'owner_id': str(uuid4())}, {'source': 'api'}, {'job_id': str(uuid4())},
                                 {'stage': 'worker.handler'}, {'bytes': 1001}, {'wall_ms': -1},
                                 {'at': '2026-10-11T12:00:00'}, {'wall_ms': 86_400_001}])
def test_browser_cannot_spoof_or_send_invalid_timings(ops, patch):
    r = ops['client'].post(f"/api/v2/uploads/{ops['upload'].id}/timings", json={'events': [span(**patch)]}, headers=auth(ops['a']))
    assert r.status_code == 422, r.text


def test_collector_sanitizes_and_does_not_recurse_and_heartbeat_stale(ops):
    db = PilotDB(ops['pg'].worker_url)
    collector = TimingCollector(db, 'worker')
    event = {'event': 'rav_timing', 'at': datetime.now(UTC).isoformat(), 'stage': 'worker.handler',
             'upload_id': str(ops['upload'].id), 'wall_ms': 42, 'sql': 'secret', 'url': 'secret', 'exception': 'secret'}
    record = logging.LogRecord('rav.timing', logging.INFO, '', 0, json.dumps(event), (), None)
    logging.getLogger('rav.timing').addHandler(collector)
    try:
        collector.emit(record)
        collector.flush_batch()
        assert collector.pending.empty() and collector.failures == 0
    finally:
        collector.close()
    trace = ops['client'].get(f"/api/v2/operations/traces/upload/{ops['upload'].id}", headers=auth(ops['a'])).json()
    assert len(trace['events']) == 1 and trace['events'][0]['data'] == {'wall_ms': 42}
    ops['pg'].sql("UPDATE rav_pilot_heartbeat SET observed_at=now()-interval '1 minute'")
    assert ops['client'].get('/api/v2/operations', headers=auth(ops['a'])).json()['heartbeats'][0]['stale'] is True
    assert safe_event({**event, 'upload_id': None}, 'worker', uuid4()) is None
    assert safe_event({**event, 'wall_ms': float('inf')}, 'worker', uuid4())['data'] == {}


def test_telemetry_direct_access_denied(ops):
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege), PilotDB(ops['pg'].api_url).as_user(ops['b']) as cur:
        cur.execute('SELECT * FROM rav_pilot_timing_event')


def test_real_upload_worker_spans_join_job_and_purge_old_logs(ops):
    collector = TimingCollector(PilotDB(ops['pg'].worker_url), 'worker')
    logging.getLogger('rav.timing').addHandler(collector)
    try:
        created, ready = uploaded(ops, ops['video'].read_bytes(), 'flow')
        while not collector.pending.empty():   # upload có ảnh lưu sẵn sinh > 1 batch event
            collector.flush_batch()
    finally:
        collector.close()
    assert ready.state == 'READY'
    c = ops['client']
    trace = c.get(f'/api/v2/operations/traces/upload/{created.id}', headers=auth(ops['a'])).json()
    assert any(e['stage'] == 'worker.s3.download_hash' for e in trace['events'])
    assert trace['attempts'][0]['usage_state'] == 'FINAL'
    trace_job = c.get(f'/api/v2/operations/traces/job/{ready.validation_job_id}', headers=auth(ops['a'])).json()
    assert trace_job['events'] == trace['events']
    ops['pg'].sql("UPDATE rav_pilot_timing_event SET received_at=now()-interval '8 days'")
    collector.flush_batch()
    assert not ops['pg'].sql('SELECT * FROM rav_pilot_timing_event')


def test_queue_bound_and_flush_failure_are_visible(ops):
    collector = TimingCollector(PilotDB(ops['pg'].worker_url), 'worker')
    event = {'at': datetime.now(UTC).isoformat(), 'stage': 'worker.handler', 'upload_id': str(ops['upload'].id)}
    record = logging.LogRecord('rav.timing', logging.INFO, '', 0, json.dumps(event), (), None)
    for _ in range(2050):
        collector.emit(record)
    assert collector.dropped == 2 and collector.pending.qsize() == 2048
    class Offline:
        def backend(self):
            raise OSError('offline')
    collector.db = Offline()
    collector.flush_batch()
    assert collector.failures == 1 and collector.dropped == 102
    collector.close()
