"""Prepared sample copies: real isolated PostgreSQL + S3 moto, no model inference or production data."""
import base64
import hashlib
from uuid import UUID

import pytest
from starlette.testclient import TestClient

from src.rav.api.auth import AuthConfig
from src.rav.api.pilot import create_pilot_app
from src.rav.errors import ServiceError
from src.rav.store import pilot_objects
from src.rav.store.pilot_db import CreateSessionInput
from src.rav.store.pilot_runtime import PilotRuntime
from src.rav.store.pilot_sample_copy import request_copy
from src.rav.store.pilot_uploads import PilotUploads
from src.rav.worker.handlers import _decoder, _probe, make_handlers
from src.rav.worker.supervisor import PilotSupervisor
from tests.conftest import make_video
from tests.test_pilot_api_v2 import ISS, JWK, auth
from tests.test_pilot_curation import cu, seed_features  # noqa: F401
from tests.test_pilot_media import actor, seed_frames, seed_source


def setup_copy(cu, monkeypatch):  # noqa: F811
    monkeypatch.setenv('RAV_PILOT_OPERATOR_IDS', str(cu.a))
    seed_frames(cu.pg, cu.s3, cu.sid, cu.a, cu.path, _decoder().frames(_probe(cu.path)))
    for kind, handler in make_handlers(cu.s3, cu.d.runtime.db, cu.tmp / 'copy-worker').items():
        cu.d.register_handler(kind, handler)


def test_copy_reuses_all_bytes_preserves_private_session_and_charges_global(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    before = cu.api.get_session(actor(cu.a), cu.session.id).model_dump(mode='json')
    original = cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (cu.sid,))[0]
    response = request_copy(cu.api, actor(cu.a), cu.sid, 'copy-1')
    assert request_copy(cu.api, actor(cu.a), cu.sid, 'copy-1')['job'].id == response['job'].id
    monkeypatch.setattr(cu.s3, 'download', lambda *a: pytest.fail('copy must not download'))
    monkeypatch.setattr('src.rav.worker.handlers._probe', lambda *a: pytest.fail('copy must not probe/decode'))
    assert cu.d.run_once()
    job = cu.d.runtime.get_job(actor(cu.a), response['job'].id)
    assert job.state == 'SUCCEEDED', job
    target = UUID(response['source_id'])
    system = cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (target,))[0]
    assert system['scope'] == 'SYSTEM' and system['owner_id'] is None and system['protected'] and not system['published']
    assert cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (cu.sid,))[0] == original
    assert cu.api.get_session(actor(cu.a), cu.session.id).model_dump(mode='json') == before
    frame_rows = cu.pg.sql('SELECT f.* FROM rav_pilot_frame f JOIN rav_pilot_frame_set s ON s.id=f.frame_set_id WHERE s.source_id=%s', (target,))
    arts = cu.pg.sql('SELECT * FROM rav_pilot_artifact WHERE source_id=%s', (target,))
    assert len(arts) == 2 and len(frame_rows) == original['metadata']['n_units'] * 2
    objects = [(system['raw_key'], system['raw_sha256'], system['size_bytes'])] + [
        (r['object_key'], r['sha256'], r['size_bytes']) for r in [*frame_rows, *arts]]
    for key, sha, size in objects:
        body = cu.s3.client.get_object(Bucket=cu.s3.bucket, Key=key)['Body'].read()
        assert hashlib.sha256(body).hexdigest() == sha and len(body) == size
        assert '/system/copy-' in key
    assert all(a['manifest']['source_id'] == str(target) for a in arts)
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_worker_object WHERE job_id=%s AND state<>'ACCEPTED'", (job.id,))[0]['n'] == 0
    accounts = {r['account_key']: r['persisted_bytes'] for r in cu.pg.sql('SELECT * FROM rav_pilot_storage_account')}
    assert accounts['GLOBAL'] == sum(x[2] for x in objects)
    assert accounts.get('USER:' + str(cu.a), 0) == 0
    # A new key deduplicates instead of cloning an existing SYSTEM source or publishing it implicitly.
    assert request_copy(cu.api, actor(cu.a), cu.sid, 'copy-2') == {'source_id': str(target), 'job': None, 'existing': True}
    with pytest.raises(ServiceError):
        cu.api.get_source(actor(cu.b), target)
    from src.rav.store.pilot_operations import AdminCatalogPatchIn, PilotOperations
    PilotOperations(cu.api).update_admin_source(actor(cu.a), target, AdminCatalogPatchIn(published=True))
    session = cu.api.create_session(actor(cu.b), CreateSessionInput(name='sample', goal='test', target_k=4,
                                                                  pool_source_ids=[target]), 'sample-session')
    assert session.n_units == original['metadata']['n_units']


def test_copy_requires_admin_own_ready_and_complete_features(cu, monkeypatch):  # noqa: F811
    with pytest.raises(ServiceError) as e:
        request_copy(cu.api, actor(cu.a), cu.sid, 'deny')
    assert e.value.status == 403
    monkeypatch.setenv('RAV_PILOT_OPERATOR_IDS', f'{cu.a},{cu.b}')
    with pytest.raises(ServiceError) as e:
        request_copy(cu.api, actor(cu.b), cu.sid, 'other')
    assert e.value.status == 404
    with pytest.raises(ServiceError) as e:
        request_copy(cu.api, actor(cu.a), cu.sid, 'no-frames')
    assert e.value.code == 'feature_not_ready'
    setup_copy(cu, monkeypatch)
    cu.pg.sql("UPDATE rav_pilot_artifact SET state='DELETED' WHERE source_id=%s AND variant='CLIP'", (cu.sid,))
    with pytest.raises(ServiceError) as e:
        request_copy(cu.api, actor(cu.a), cu.sid, 'no-clip')
    assert e.value.code == 'feature_not_ready'


def test_failed_copy_cleans_only_new_objects_and_releases_holds(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    result = request_copy(cu.api, actor(cu.a), cu.sid, 'fail')
    original_copy = cu.s3.copy_verified
    def fail_after_write(*args, **kwargs):
        original_copy(*args, **kwargs)
        raise ServiceError('dependency_unavailable', 'test copy failure', 503)
    monkeypatch.setattr(cu.s3, 'copy_verified', fail_after_write)
    assert cu.d.run_once()
    job = cu.d.runtime.get_job(actor(cu.a), result['job'].id)
    assert job.state == 'FAILED', job
    assert not cu.pg.sql('SELECT 1 FROM rav_pilot_source_asset WHERE id=%s', (UUID(result['source_id']),))
    supervisor = PilotSupervisor(cu.d.runtime, cu.d.worker_ctx, PilotUploads(cu.d.runtime.db, lambda: cu.s3))
    assert supervisor.run_reconciliation()
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_worker_object WHERE job_id=%s AND state<>'DELETED'", (job.id,))[0]['n'] == 0
    assert all(r['reserved_bytes'] == 0 for r in cu.pg.sql('SELECT * FROM rav_pilot_storage_account'))
    raw = cu.pg.sql('SELECT raw_key FROM rav_pilot_source_asset WHERE id=%s', (cu.sid,))[0]['raw_key']
    assert cu.s3.head(raw)


def test_cancelled_copy_has_no_visible_partial_sample(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    result = request_copy(cu.api, actor(cu.a), cu.sid, 'cancel')
    real = pilot_objects.written_batch
    runtime = PilotRuntime(cu.api)
    def cancel_after_batch(db, lease, keys):
        real(db, lease, keys)
        job = runtime.get_job(actor(cu.a), lease.job_id)
        if job.state == 'RUNNING':
            runtime.cancel(actor(cu.a), lease.job_id, job.state_version)
    monkeypatch.setattr(pilot_objects, 'written_batch', cancel_after_batch)
    assert cu.d.run_once()
    job = runtime.get_job(actor(cu.a), result['job'].id)
    assert job.state == 'CANCELLED'
    assert not cu.pg.sql('SELECT 1 FROM rav_pilot_source_asset WHERE id=%s', (UUID(result['source_id']),))
    supervisor = PilotSupervisor(cu.d.runtime, cu.d.worker_ctx, PilotUploads(cu.d.runtime.db, lambda: cu.s3))
    assert supervisor.run_reconciliation()
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_worker_object WHERE job_id=%s AND state<>'DELETED'", (job.id,))[0]['n'] == 0


def test_http_copy_auth_idempotency_and_progress_metadata(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    client = TestClient(create_pilot_app(cu.pg.api_url, AuthConfig(expected_issuer=ISS), raw_jwks={'keys': [JWK]}))
    url = f'/api/v2/admin/sources/{cu.sid}/copy-to-samples'
    assert client.post(url).status_code == 401
    assert client.post(url, headers=auth(cu.b) | {'Idempotency-Key': 'deny'}).status_code == 403
    assert client.post(url, headers=auth(cu.a)).status_code == 422
    one = client.post(url, headers=auth(cu.a) | {'Idempotency-Key': 'api'})
    assert one.status_code == 410, one.text
    assert one.json()['error']['code'] == 'sample_copy_retired'
    assert 'raw_key' not in one.text


def test_private_deletion_does_not_remove_published_sample_objects(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    path = make_video(cu.tmp / 'unbound.mp4', seconds=1, fps=10, size=(64, 48))
    sid, core, frames = seed_source(cu.pg, cu.s3, path, cu.a)
    seed_frames(cu.pg, cu.s3, sid, cu.a, path, frames)
    seed_features(cu.pg, cu.s3, sid, core, path, cu.a, ('DINO', 'CLIP'))
    response = request_copy(cu.api, actor(cu.a), sid, 'unbound')
    assert cu.d.run_once()
    target = UUID(response['source_id'])
    system = cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (target,))[0]
    # Hand-seeded originals need their pre-existing PRIVATE ledger baseline before exercising real deletion.
    total = cu.pg.sql("SELECT (SELECT size_bytes FROM rav_pilot_source_asset WHERE id=%s) + "
                      "(SELECT sum(size_bytes) FROM rav_pilot_artifact WHERE source_id=%s) + "
                      "(SELECT sum(total_bytes) FROM rav_pilot_frame_set WHERE source_id=%s) AS n", (sid, sid, sid))[0]['n']
    cu.pg.sql("UPDATE rav_pilot_storage_account SET persisted_bytes=persisted_bytes+%s WHERE account_key='GLOBAL'", (total,))
    cu.pg.sql("INSERT INTO rav_pilot_storage_account(account_key,limit_bytes,persisted_bytes) VALUES (%s,%s,%s) "
              "ON CONFLICT(account_key) DO UPDATE SET persisted_bytes=rav_pilot_storage_account.persisted_bytes+%s",
              ('USER:' + str(cu.a), 1 << 30, total, total))
    runtime = PilotRuntime(cu.api)
    receipt = runtime.request_source_delete(actor(cu.a), sid, 1, 'delete-original')
    assert cu.d.run_once()
    deleted = runtime.get_job(actor(cu.a), receipt.job_id)
    assert deleted.state == 'SUCCEEDED', deleted
    assert cu.pg.sql('SELECT availability FROM rav_pilot_source_asset WHERE id=%s', (sid,))[0]['availability'] == 'DELETED'
    assert cu.s3.head(system['raw_key'])
    for row in cu.pg.sql('SELECT object_key FROM rav_pilot_artifact WHERE source_id=%s', (target,)):
        assert cu.s3.head(row['object_key'])
    rows = cu.pg.sql('SELECT f.object_key FROM rav_pilot_frame f JOIN rav_pilot_frame_set s ON s.id=f.frame_set_id WHERE s.source_id=%s', (target,))
    assert rows and all(cu.s3.head(r['object_key']) for r in rows)


def test_copy_detects_source_checksum_drift(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    source = cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (cu.sid,))[0]
    data = b'x' * source['size_bytes']
    cu.s3.put_bytes(source['raw_key'], data, hashlib.sha256(data).hexdigest(), 'video/mp4')
    # Moto omits optional HEAD checksum responses; simulate the checksum AWS supplies for this object.
    head = cu.s3.client.head_object
    def with_checksum(**kwargs):
        return {**head(**kwargs), 'ChecksumSHA256': base64.b64encode(hashlib.sha256(data).digest()).decode()}
    monkeypatch.setattr(cu.s3.client, 'head_object', with_checksum)
    with pytest.raises(ServiceError) as e:
        cu.s3.copy_verified(source['raw_key'], cu.s3.prefix + '/system/test-copy', source['raw_sha256'], len(data))
    assert e.value.code == 'result_invalid'


def test_queued_duplicate_copy_never_deletes_winning_sample(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    monkeypatch.setenv('RAV_PILOT_OPERATOR_IDS', f'{cu.a},{cu.b}')
    sid, core, frames = seed_source(cu.pg, cu.s3, cu.path, cu.b)
    seed_frames(cu.pg, cu.s3, sid, cu.b, cu.path, frames)
    seed_features(cu.pg, cu.s3, sid, core, cu.path, cu.b, ('DINO', 'CLIP'))
    first = request_copy(cu.api, actor(cu.a), cu.sid, 'winner')
    second = request_copy(cu.api, actor(cu.b), sid, 'loser')
    assert cu.d.run_once() and cu.d.run_once()
    states = [cu.d.runtime.get_job(actor(uid), result['job'].id).state for uid, result in [(cu.a, first), (cu.b, second)]]
    assert sorted(states) == ['FAILED', 'SUCCEEDED']
    before = cu.pg.sql("SELECT * FROM rav_pilot_source_asset WHERE scope='SYSTEM'")
    assert len(before) == 1
    supervisor = PilotSupervisor(cu.d.runtime, cu.d.worker_ctx, PilotUploads(cu.d.runtime.db, lambda: cu.s3))
    assert supervisor.run_reconciliation()
    assert cu.s3.head(before[0]['raw_key'])
    assert cu.pg.sql("SELECT * FROM rav_pilot_source_asset WHERE scope='SYSTEM'") == before


def test_copy_preserves_receipts_with_accounting_disabled(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    cfg = cu.pg.sql('SELECT config FROM rav_pilot_quota_policy WHERE id=%s', (cu.policy,))[0]['config']
    cfg.update(storage_accounting_enabled=False, storage_user_bytes=0, storage_global_bytes=0)
    policy = cu.pg.policy(config=cfg)
    cu.pg.sql('UPDATE rav_pilot_profile SET policy_id=%s WHERE user_id=%s', (policy, cu.a))
    result = request_copy(cu.api, actor(cu.a), cu.sid, 'no-accounting')
    assert cu.d.run_once()
    job = cu.d.runtime.get_job(actor(cu.a), result['job'].id)
    assert job.state == 'SUCCEEDED', job
    assert all(r['persisted_bytes'] == 0 and r['reserved_bytes'] == 0 for r in cu.pg.sql('SELECT * FROM rav_pilot_storage_account'))
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_worker_object WHERE job_id=%s AND state='ACCEPTED'", (job.id,))[0]['n'] > 0


def test_copy_rejects_existing_core_with_different_raw_sha(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    cu.pg.source(None, cu.core)
    with pytest.raises(ServiceError) as e:
        request_copy(cu.api, actor(cu.a), cu.sid, 'different-sha')
    assert e.value.code == 'result_invalid'
