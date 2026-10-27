"""Interactive API/cache/checkpoint/CPU-free regressions on isolated PostgreSQL/Moto (§21.26)."""
import copy
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from src.rav.api.auth import AuthConfig
from src.rav.api.interactive_proxy import InteractiveProxy
from src.rav.api.pilot import create_pilot_app
from src.rav.errors import ServiceError
from src.rav.store.pilot_interactive import PilotInteractive
from src.rav.store.pilot_runtime import PilotRuntime, Usage, WorkerContext
from src.rav.worker.interactive import CommittedIn, Interactive, ProofIn, ProposalIn, SessionIn, create_app
from tests.conftest import POLICY_CONFIG
from tests.test_pilot_api_v2 import ISS, JWK, auth
from tests.test_pilot_curation import _ask, _local
from tests.test_pilot_curation import cu as curation_fixture  # noqa: F401
from tests.test_pilot_media import actor
from tests.test_pilot_queue import spec


@pytest.fixture
def stack(request):
    cu = request.getfixturevalue('curation_fixture')
    service = Interactive(cu.d.runtime.db, cu.s3, cu.tmp/'interactive', validate_s=60)
    return cu, service


def inp(cu, **kw):
    return ProposalIn(owner_id=cu.a, session_id=cu.session.id, request_id=uuid4(), recipe='diversity', batch_size=4,
                      seed=7, expected_state_version=1, **kw)


def test_refresh_open_rebases_batch_number_after_background_job(stack):
    cu, service = stack
    service.open(SessionIn(owner_id=cu.a, session_id=cu.session.id))
    _ask(cu, recipe='random')
    assert cu.d.run_once()
    service.open(SessionIn(owner_id=cu.a, session_id=cu.session.id), refresh=True)
    assert service.run(inp(cu))['proposal']['n'] == 2


def test_warm_parity_no_db_raw_download_and_replay(stack, monkeypatch):
    cu, service = stack
    expected = _local(cu, 'diversity', 4, 7)
    service.open(SessionIn(owner_id=cu.a, session_id=cu.session.id))
    req = inp(cu, return_scores=True)
    monkeypatch.setattr(service.repo.db, 'backend', lambda: pytest.fail('warm proposal must not read DB'))
    monkeypatch.setattr(service.storage, 'download', lambda *_: pytest.fail('warm proposal must not download'))
    r = service.run(req)
    assert [p['unit_id'] for p in r['proposal']['picks']] == [p.unit.id for p in expected.picks]
    assert r['proposal']['scores']['unit_id'] == [p['unit_id'] for p in r['proposal']['picks']]
    proof = service.proof(ProofIn(owner_id=cu.a, session_id=cu.session.id, batch_id=r['batch_id']))
    assert len(proof['proposal']['scores']['unit_id']) > len(r['proposal']['picks'])
    assert service.run(req) == r
    with pytest.raises(ServiceError, match='request_id khác input'):
        service.run(req.model_copy(update={'seed': 9}))
    assert cu.pg.sql('SELECT count(*) AS n FROM rav_pilot_job')[0]['n'] == 0
    assert cu.pg.sql('SELECT count(*) AS n FROM rav_pilot_decision')[0]['n'] == 0


@pytest.mark.parametrize('legacy_worker', [False, True])
def test_explicit_save_cas_replay_and_no_proposal_trust(stack, monkeypatch, legacy_worker):
    cu, service = stack
    monkeypatch.setenv('RAV_INTERACTIVE_URL', 'http://127.0.0.1:8443')
    def proxy(_, path, body):
        if path == '/propose':
            result = service.run(ProposalIn(**body))
            if legacy_worker:
                result['proposal'] = service.proof(ProofIn(owner_id=cu.a, session_id=cu.session.id,
                                                          batch_id=result['batch_id']))['proposal']
            return result
        if path == '/proof':
            return service.proof(ProofIn(**body))
        if path == '/committed':
            return service.committed(CommittedIn(**body))
        return service.open(SessionIn(**body))
    monkeypatch.setattr(InteractiveProxy, 'call', proxy)
    with TestClient(create_pilot_app(cu.pg.api_url, AuthConfig(expected_issuer=ISS), raw_jwks={'keys': [JWK]})) as client:
        headers = auth(cu.a)
        base = f'/api/v2/sessions/{cu.session.id}'
        req = inp(cu, return_scores=True).model_dump(mode='json')
        req.pop('owner_id')
        req.pop('session_id')
        req.pop('checkpoint')
        response = client.post(base+'/interactive/propose', json=req, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['proposal']['scores']['unit_id'] == [p['unit_id'] for p in result['proposal']['picks']]
        uid = result['proposal']['picks'][0]['unit_id']
        body = {'expected_state_version': 1, 'batch_id': result['batch_id'], 'kept_ids': [uid], 'dropped_ids': []}
        saved = client.post(base+'/checkpoint', json=body, headers={**headers, 'Idempotency-Key': 'save1'})
        assert saved.status_code == 200, saved.text
        assert saved.json()['state']['kept_ids'] == [uid]
        assert saved.json()['proposal']['picks'] == result['proposal']['picks']
        assert saved.json()['proposal']['scores'] is None
        assert 'interactive_basis' not in saved.json()['proposal']['evidence']
        monkeypatch.setattr(InteractiveProxy, 'call', lambda *_: (_ for _ in ()).throw(ServiceError('batch_expired', 'cache lost', 409)))
        assert client.post(base+'/checkpoint', json=body, headers={**headers, 'Idempotency-Key': 'save1'}).json() == saved.json()
        assert client.post(base+'/checkpoint', json={**body, 'proposal': {'picks': []}}, headers=headers).status_code == 422
    assert cu.pg.sql('SELECT count(*) AS n FROM rav_pilot_proposal')[0]['n'] == 1
    assert cu.pg.sql('SELECT count(*) AS n FROM rav_pilot_job')[0]['n'] == 0
    proof = service.proof(ProofIn(owner_id=cu.a, session_id=cu.session.id, batch_id=result['batch_id']))
    with pytest.raises(ServiceError, match='tab khác'):
        PilotInteractive(cu.api).save(actor(cu.a), cu.session.id, body, proof, 'save2')


def test_auth_owner_rate_limit_and_cache_bounds(stack):
    cu, service = stack
    with TestClient(create_app(service, 'x'*32)) as client:
        req = inp(cu).model_dump(mode='json')
        assert client.post('/propose', json=req).status_code == 401
        service.active.add(cu.a)
        r = client.post('/propose', json=req, headers={'Authorization': 'Bearer '+'x'*32})
        assert r.status_code == 429 and r.headers['retry-after'] == '1'
        service.active.clear()
        req['owner_id'] = str(cu.b)
        assert client.post('/propose', json=req, headers={'Authorization': 'Bearer '+'x'*32}).status_code == 404
    service.max_bytes = 1
    with pytest.raises(ServiceError, match='trần RAM'):
        service.open(SessionIn(owner_id=cu.a, session_id=cu.session.id))
    assert not service.sessions


@pytest.mark.parametrize('compute_error', [False, True])
def test_advance_checkpoint_has_no_pool_scores_on_success_or_error(stack, monkeypatch, compute_error):
    cu, service = stack
    monkeypatch.setenv('RAV_INTERACTIVE_URL', 'http://127.0.0.1:8443')

    def proxy(_, path, body):
        if path == '/propose':
            return service.run(ProposalIn(**body))
        if path == '/proof':
            return service.proof(ProofIn(**body))
        return service.committed(CommittedIn(**body))

    monkeypatch.setattr(InteractiveProxy, 'call', proxy)
    with TestClient(create_pilot_app(cu.pg.api_url, AuthConfig(expected_issuer=ISS), raw_jwks={'keys': [JWK]})) as client:
        base = f'/api/v2/sessions/{cu.session.id}'
        body = inp(cu, return_scores=True).model_dump(mode='json', exclude={'owner_id', 'session_id', 'checkpoint'})
        first = client.post(base+'/interactive/propose', json=body, headers=auth(cu.a)).json()
        body.update(request_id=str(uuid4()), previous_batch_id=first['batch_id'],
                    kept_ids=[first['proposal']['picks'][0]['unit_id']],
                    recipe='invalid-recipe' if compute_error else 'diversity')
        r = client.post(base+'/interactive/propose', json=body, headers=auth(cu.a))
        assert r.status_code == (422 if compute_error else 200), r.text
        result = r.json()['error'] if compute_error else r.json()
        assert result['checkpoint']['proposal']['scores'] is None
        if not compute_error:
            assert result['proposal']['scores']['unit_id'] == [p['unit_id'] for p in result['proposal']['picks']]
        persisted = cu.pg.sql('SELECT evidence FROM rav_pilot_proposal')[0]['evidence']
        assert len(persisted['scores']['unit_id']) > 4


def test_revalidation_rejects_changed_session_and_restores_after_cache_loss(stack):
    cu, service = stack
    req = inp(cu)
    first = service.run(req)
    proof = service.proof(ProofIn(owner_id=cu.a, session_id=cu.session.id, batch_id=first['batch_id']))
    result = PilotInteractive(cu.api).save(actor(cu.a), cu.session.id,
                                          {'expected_state_version': 1, 'kept_ids': [], 'dropped_ids': []}, proof, 'save')
    assert result['state']['state_version'] == 2
    service.validate_s = 0
    with pytest.raises(ServiceError, match='phiên đã đổi'):
        service.run(req)
    r = service.run(req.model_copy(update={'request_id': uuid4(), 'expected_state_version': 2}))
    assert r['state_version'] == 2


def test_cpu_free_job_no_hold_receipt_or_deficit_and_flag_immutable(pg):
    cfg = copy.deepcopy(POLICY_CONFIG)
    cfg.update(cpu_accounting_enabled=False, cpu_base_ms=0)
    uid = pg.profile(policy_id=pg.policy(config=cfg))
    pg.slot('cpu-1')
    from src.rav.store.pilot_db import PilotDB
    api, worker = PilotRuntime(PilotDB(pg.api_url)), PilotRuntime(PilotDB(pg.worker_url))
    job = api.enqueue(actor(uid), spec())
    assert not pg.sql('SELECT * FROM rav_pilot_quota_hold')
    lease = worker.claim(WorkerContext(worker_id=uuid4(), host_boot_id=uuid4()), 'CPU')
    worker.ensure_running_budget(lease, Usage(cpu_ms=99999999, gpu_ms=0, state='FINAL'))
    result = worker.finish(lease, Usage(cpu_ms=99999999, gpu_ms=0, state='FINAL'), {'code': 'test'})
    assert result.state == 'FAILED'
    assert not pg.sql('SELECT * FROM rav_pilot_usage_receipt')
    assert pg.sql('SELECT state FROM rav_pilot_resource_slot')[0]['state'] == 'FREE'
    assert not api.read_quota(actor(uid)).cpu.enabled
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.sql('UPDATE rav_pilot_job SET cpu_accounting_enabled=true WHERE id=%s', (job.id,))


def test_gpu_quota_still_enforced_when_cpu_free(pg):
    cfg = copy.deepcopy(POLICY_CONFIG)
    cfg.update(cpu_accounting_enabled=False, gpu_base_ms=1)
    uid = pg.profile(policy_id=pg.policy(config=cfg))
    from src.rav.store.pilot_db import PilotDB
    rt = PilotRuntime(PilotDB(pg.api_url))
    with pytest.raises(ServiceError, match='quota GPU'):
        rt.enqueue(actor(uid), spec(kind='PREPARE_DINO'))
