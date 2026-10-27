"""Read-only sample reference acceptance with isolated PostgreSQL/S3; no production data."""
import pytest
from starlette.testclient import TestClient

from src.rav.api.auth import AuthConfig
from src.rav.api.pilot import create_pilot_app
from src.rav.errors import ServiceError
from src.rav.store.pilot_curation import ProposeIn
from src.rav.store.pilot_db import CreateSessionInput
from src.rav.store.pilot_media import MediaRequestIn, PilotMedia
from src.rav.store.pilot_operations import AdminCatalogPatchIn, PilotOperations
from src.rav.store.pilot_runtime import PilotRuntime
from src.rav.store.pilot_sample_copy import request_copy
from src.rav.store.pilot_sample_reference import reference_sample
from src.rav.store.pilot_uploads import PilotUploads
from src.rav.worker.supervisor import PilotSupervisor
from tests.test_pilot_api_v2 import ISS, JWK, auth
from tests.test_pilot_curation import cu  # noqa: F401
from tests.test_pilot_media import actor
from tests.test_pilot_sample_copy import setup_copy


@pytest.mark.parametrize('accounting', [True, False])
def test_reference_shares_objects_without_io_jobs_or_new_storage(cu, monkeypatch, accounting):  # noqa: F811
    setup_copy(cu, monkeypatch)
    if not accounting:
        cfg = cu.pg.sql('SELECT config FROM rav_pilot_quota_policy WHERE id=%s', (cu.policy,))[0]['config']
        cfg.update(storage_accounting_enabled=False, storage_user_bytes=0, storage_global_bytes=0)
        policy = cu.pg.policy(config=cfg)
        cu.pg.sql('UPDATE rav_pilot_profile SET policy_id=%s WHERE user_id=%s', (policy, cu.a))
    original = cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (cu.sid,))[0]
    ledger = cu.pg.sql('SELECT * FROM rav_pilot_storage_account ORDER BY account_key')
    objects = cu.s3.client.list_objects_v2(Bucket=cu.s3.bucket)['KeyCount']
    for name in ('copy_verified', 'put_file', 'put_bytes', 'download'):
        monkeypatch.setattr(cu.s3, name, lambda *a, **kw: pytest.fail('reference must not do S3 I/O'))
    result = reference_sample(cu.api, actor(cu.a), cu.sid, 'ref-1')
    target = result.source_id
    assert reference_sample(cu.api, actor(cu.a), cu.sid, 'ref-1') == result
    assert reference_sample(cu.api, actor(cu.a), cu.sid, 'ref-2').existing
    sample = cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (target,))[0]
    assert sample['raw_key'] == original['raw_key']
    assert sample['scope'] == 'SYSTEM' and sample['protected'] and sample['owner_id'] is None and not sample['published']
    assert cu.pg.sql('SELECT * FROM rav_pilot_source_asset WHERE id=%s', (cu.sid,))[0] == original
    def frames(sid):
        return cu.pg.sql('SELECT f.unit_id,f.variant,f.object_key,f.sha256 FROM rav_pilot_frame f '
                         'JOIN rav_pilot_frame_set s ON s.id=f.frame_set_id WHERE s.source_id=%s '
                         'ORDER BY f.unit_id,f.variant', (sid,))
    assert frames(target) == frames(cu.sid)
    def arts(sid):
        return cu.pg.sql('SELECT variant,object_key,sha256,input_hash FROM rav_pilot_artifact '
                         'WHERE source_id=%s ORDER BY variant', (sid,))
    assert arts(target) == arts(cu.sid)
    assert cu.pg.sql('SELECT * FROM rav_pilot_storage_account ORDER BY account_key') == ledger
    assert cu.pg.sql('SELECT count(*) AS n FROM rav_pilot_job')[0]['n'] == 0
    assert cu.pg.sql('SELECT count(*) AS n FROM rav_pilot_worker_object')[0]['n'] == 0
    assert cu.s3.client.list_objects_v2(Bucket=cu.s3.bucket)['KeyCount'] == objects
    pin = cu.pg.sql("SELECT * FROM rav_pilot_source_pin WHERE source_id=%s AND consumer_id=%s", (cu.sid, target))[0]
    assert pin['consumer_kind'] == 'REFERENCE' and pin['released_at'] is None


def test_shared_sample_media_proposal_and_hidden_existing_session(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    result = reference_sample(cu.api, actor(cu.a), cu.sid, 'share')
    target = result.source_id
    ops = PilotOperations(cu.api)
    with pytest.raises(ServiceError):
        cu.api.get_source(actor(cu.b), target)
    ops.update_admin_source(actor(cu.a), target, AdminCatalogPatchIn(published=True))
    session = cu.api.create_session(actor(cu.b), CreateSessionInput(name='shared', goal='g', target_k=4,
        pool_source_ids=[target]), 'shared-session')
    media = PilotMedia(cu.api, lambda: cu.s3)
    unit = f'{cu.core}:0.000-0.000'
    image = media.request(actor(cu.b), MediaRequestIn(source_id=target, unit_id=unit, variant='thumbnail'))
    assert image.state == 'READY' and image.url and media.get(actor(cu.b), image.id).state == 'READY'
    with pytest.raises(ServiceError):
        media.request(actor(cu.b), MediaRequestIn(source_id=cu.sid, unit_id=unit, variant='thumbnail'))
    job = cu.cur.request_proposal(actor(cu.b), session.id, ProposeIn(recipe='diversity',batch_size=2,
        expected_state_version=1), 'shared-proposal')
    assert cu.d.run_once()
    completed = cu.d.runtime.get_job(actor(cu.b), job.id)
    assert completed.state == 'SUCCEEDED', completed
    ops.update_admin_source(actor(cu.a), target, AdminCatalogPatchIn(published=False))
    assert cu.api.get_source(actor(cu.b), target)
    assert media.get(actor(cu.b), image.id).state == 'READY'
    with pytest.raises(ServiceError):
        cu.api.create_session(actor(cu.b), CreateSessionInput(name='hidden', goal='g', target_k=4,
            pool_source_ids=[target]), 'hidden-session')
    with pytest.raises(ServiceError) as e:
        PilotRuntime(cu.api).request_source_delete(actor(cu.b), target, 1, 'delete-sample')
    assert e.value.status == 403
    with pytest.raises(ServiceError) as e:
        PilotRuntime(cu.api).request_source_delete(actor(cu.a), cu.sid, 1, 'delete-backing')
    assert e.value.code == 'resource_in_use'


def test_backing_guards_and_reference_are_immutable(cu, monkeypatch):  # noqa: F811
    import psycopg
    setup_copy(cu, monkeypatch)
    target = reference_sample(cu.api, actor(cu.a), cu.sid, 'guard').source_id
    ref = cu.pg.sql('SELECT * FROM rav_pilot_sample_reference WHERE sample_source_id=%s', (target,))[0]
    statements = [
        ("UPDATE rav_pilot_source_asset SET availability='DELETE_PENDING' WHERE id=%s", cu.sid),
        ("UPDATE rav_pilot_artifact SET state='DELETED' WHERE id=%s", ref['dino_id']),
        ("UPDATE rav_pilot_frame_set SET state='DELETED' WHERE id=%s", ref['frame_set_id']),
        ("UPDATE rav_pilot_frame SET width=width+1 WHERE frame_set_id=%s", ref['frame_set_id']),
        ('DELETE FROM rav_pilot_sample_reference WHERE sample_source_id=%s', target),
    ]
    for sql, value in statements:
        with pytest.raises(psycopg.errors.CheckViolation):
            cu.pg.sql(sql, (value,))
    assert cu.s3.head(cu.pg.sql('SELECT raw_key FROM rav_pilot_source_asset WHERE id=%s', (cu.sid,))[0]['raw_key'])
    assert not cu.pg.sql("SELECT has_function_privilege('authenticated',"
                        "'rav_pilot_reference_sample(uuid,uuid,uuid,uuid,uuid,uuid)','EXECUTE') AS allowed")[0]['allowed']
    assert not cu.pg.sql("SELECT has_table_privilege('authenticated','rav_pilot_sample_reference','SELECT') AS allowed")[0]['allowed']


def test_reference_http_auth_readiness_idempotency_and_retired_copy(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    client = TestClient(create_pilot_app(cu.pg.api_url, AuthConfig(expected_issuer=ISS), raw_jwks={'keys':[JWK]}))
    path = f'/api/v2/admin/sources/{cu.sid}/reference-as-sample'
    assert client.post(path).status_code == 401
    assert client.post(path, headers=auth(cu.b)|{'Idempotency-Key':'x'}).status_code == 403
    assert client.post(path, headers=auth(cu.a)).status_code == 422
    one = client.post(path, headers=auth(cu.a)|{'Idempotency-Key':'x'})
    assert one.status_code == 200, one.text
    assert client.post(path, headers=auth(cu.a)|{'Idempotency-Key':'x'}).json() == one.json()
    assert 'raw_key' not in one.text and 'job' not in one.json()
    assert client.post('/api/v2/admin/sources/00000000-0000-0000-0000-000000000001/reference-as-sample',
                       headers=auth(cu.a)|{'Idempotency-Key':'x'}).status_code == 409
    assert client.post(f'/api/v2/admin/sources/{cu.sid}/copy-to-samples',
                       headers=auth(cu.a)|{'Idempotency-Key':'old'}).status_code == 410


def test_reference_requires_own_complete_video_and_legacy_retry_stays_retired(cu, monkeypatch):  # noqa: F811
    monkeypatch.setenv('RAV_PILOT_OPERATOR_IDS', f'{cu.a},{cu.b}')
    with pytest.raises(ServiceError) as e:
        reference_sample(cu.api, actor(cu.b), cu.sid, 'other')
    assert e.value.status == 404
    with pytest.raises(ServiceError) as e:
        reference_sample(cu.api, actor(cu.a), cu.sid, 'no-frames')
    assert e.value.code == 'feature_not_ready'
    setup_copy(cu, monkeypatch)
    old = request_copy(cu.api, actor(cu.a), cu.sid, 'legacy')
    runtime = PilotRuntime(cu.api)
    runtime.cancel(actor(cu.a), old['job'].id, old['job'].state_version)
    with pytest.raises(ServiceError) as e:
        cu.cur.retry(actor(cu.a), old['job'].id, 'retry')
    assert e.value.code == 'sample_copy_retired'
    cu.pg.sql("UPDATE rav_pilot_artifact SET state='DELETED' WHERE source_id=%s AND variant='CLIP'", (cu.sid,))
    with pytest.raises(ServiceError) as e:
        reference_sample(cu.api, actor(cu.a), cu.sid, 'no-clip')
    assert e.value.code == 'feature_not_ready'


def test_legacy_attempt_cleanup_keeps_shared_backing_objects(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    old = request_copy(cu.api, actor(cu.a), cu.sid, 'old-failed-copy')
    real_copy = cu.s3.copy_verified
    def fail_after_copy(*args, **kwargs):
        real_copy(*args, **kwargs)
        raise ServiceError('dependency_unavailable', 'QA partial copy', 503)
    monkeypatch.setattr(cu.s3, 'copy_verified', fail_after_copy)
    assert cu.d.run_once()
    assert cu.d.runtime.get_job(actor(cu.a), old['job'].id).state == 'FAILED'
    target = reference_sample(cu.api, actor(cu.a), cu.sid, 'after-partial-copy').source_id
    supervisor = PilotSupervisor(cu.d.runtime, cu.d.worker_ctx, PilotUploads(cu.d.runtime.db, lambda: cu.s3))
    assert supervisor.run_reconciliation()
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_worker_object WHERE job_id=%s AND state<>'DELETED'",
                     (old['job'].id,))[0]['n'] == 0
    raw = cu.pg.sql('SELECT raw_key FROM rav_pilot_source_asset WHERE id=%s', (target,))[0]['raw_key']
    assert cu.s3.head(raw)
    rows = cu.pg.sql('SELECT f.object_key FROM rav_pilot_frame f JOIN rav_pilot_frame_set s ON s.id=f.frame_set_id '
                     'WHERE s.source_id=%s', (target,))
    rows += cu.pg.sql('SELECT object_key FROM rav_pilot_artifact WHERE source_id=%s', (target,))
    assert rows and all(cu.s3.head(r['object_key']) for r in rows)


def test_reference_rejects_existing_core_with_other_full_sha(cu, monkeypatch):  # noqa: F811
    setup_copy(cu, monkeypatch)
    cu.pg.source(None, cu.core)
    with pytest.raises(ServiceError) as e:
        reference_sample(cu.api, actor(cu.a), cu.sid, 'sha-mismatch')
    assert e.value.code == 'result_invalid'


def test_reference_migration_preserves_interactive_checkpoint_receipts(cu):  # noqa: F811
    from uuid import uuid4

    from src.rav.store.pilot_sample_reference import SampleReferenceResult
    output = SampleReferenceResult(source_id=cu.sid, existing=True)
    with cu.api.backend() as cur:
        cu.api.write_receipt(cur, cu.a, 'INTERACTIVE_SAVE', str(uuid4()), 'a'*64, output)
    assert cu.pg.sql("SELECT count(*) AS n FROM rav_pilot_command_receipt WHERE action='INTERACTIVE_SAVE'")[0]['n'] == 1
