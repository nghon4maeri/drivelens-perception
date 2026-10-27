"""Multipart upload/owner/size/retry/recovery regressions on PostgreSQL/Moto (§21.27)."""
from uuid import uuid4

import pytest
import requests

from src.rav.errors import ServiceError
from src.rav.store import pilot_storage
from src.rav.store.pilot_multipart import PilotMultipart, layout, upload_lock
from src.rav.store.pilot_uploads import UploadIn
from tests.conftest import POLICY_CONFIG
from tests.test_pilot_uploads import actor
from tests.test_pilot_uploads import up as upload_fixture  # noqa: F401


@pytest.fixture
def large(request, monkeypatch):
    up = request.getfixturevalue('upload_fixture')
    monkeypatch.setattr(pilot_storage, 'MULTIPART_THRESHOLD', 1 << 20)
    cfg = dict(POLICY_CONFIG, multipart_upload_enabled=True, storage_user_bytes=32 << 30, storage_global_bytes=128 << 30,
               max_video_bytes=5 << 30, max_duration_ms=3600000, max_decoded_pixels=2*10**12)
    up['a'] = up['pg'].profile(policy_id=up['pg'].policy(config=cfg))
    up['multipart'] = PilotMultipart(up['svc'].db, lambda: up['s3'])
    return up


def prepare(up, size=17 << 20):
    created = up['svc'].create(actor(up['a']), UploadIn(filename='large.mp4', declared_bytes=size), str(uuid4()))
    assert created.transfer.method == 'MULTIPART'
    return created


def transfer(up, created, path):
    m = up['multipart']
    info = m.info(actor(up['a']), created.id, start=True)
    with path.open('rb') as file:
        for n in range(1, info['part_count']+1):
            url = m.sign(actor(up['a']), created.id, [n])['parts'][0]['url']
            r = requests.put(url, data=file.read(info['part_size']))
            assert r.status_code == 200, r.status_code
    return m.info(actor(up['a']), created.id)


def test_upload_resume_owner_and_complete_replay(large):
    up = large
    data = up['video'].read_bytes()
    path = up['tmp']/'large.mp4'
    with path.open('wb') as f:
        f.write(data)
        f.truncate(17 << 20)  # same real MP4, padding tests bounded transfer without allocating full body
    created = prepare(up)
    info = up['multipart'].info(actor(up['a']), created.id, start=True)
    with pytest.raises(ServiceError) as e:
        up['multipart'].info(actor(up['b']), created.id, start=True)
    assert e.value.status == 404
    with pytest.raises(ServiceError, match='part numbers'):
        up['multipart'].sign(actor(up['a']), created.id, [0])
    with pytest.raises(ServiceError, match='chưa đủ parts'):
        up['svc'].complete(actor(up['a']), created.id)
    assert up['multipart'].info(actor(up['a']), created.id, start=True) == info
    transferred = transfer(up, created, path)
    assert len(transferred['parts']) == 2
    uid = up['pg'].sql('SELECT multipart_id FROM rav_pilot_upload WHERE id=%s', (created.id,))[0]['multipart_id']
    key = up['pg'].sql('SELECT staging_key FROM rav_pilot_upload WHERE id=%s', (created.id,))[0]['staging_key']
    assert up['s3'].multipart_uploads(key) == [uid]
    result = up['svc'].complete(actor(up['a']), created.id)
    assert result.state == 'VALIDATING'
    assert up['svc'].complete(actor(up['a']), created.id).validation_job_id == result.validation_job_id
    assert up['dispatch'].run_once()
    done = up['svc'].get(actor(up['a']), created.id)
    assert done.state == 'READY', done.error
    source = up['pg'].sql('SELECT size_bytes FROM rav_pilot_source_asset WHERE id=%s', (done.source_id,))[0]
    assert source['size_bytes'] == path.stat().st_size
    assert up['s3'].multipart_uploads(key) == []


def test_orphan_init_recovery_abort_and_busy(large):
    up = large
    c = prepare(up)
    key = up['pg'].sql('SELECT staging_key FROM rav_pilot_upload WHERE id=%s', (c.id,))[0]['staging_key']
    orphan = up['s3'].multipart_start(key)  # API crash after S3 init, before DB reference
    up['multipart'].info(actor(up['a']), c.id, start=True)
    assert up['s3'].multipart_uploads(key) == [orphan]
    assert up['pg'].sql('SELECT multipart_id FROM rav_pilot_upload WHERE id=%s', (c.id,))[0]['multipart_id'] == orphan
    with upload_lock(up['svc'].db, c.id):
        with pytest.raises(ServiceError, match='đang xử lý'):
            up['multipart'].sign(actor(up['a']), c.id, [1])
    assert up['svc'].abort(actor(up['a']), c.id).state == 'ABORTED'
    assert not up['s3'].multipart_uploads(key)
    with pytest.raises(ServiceError):
        up['multipart'].sign(actor(up['a']), c.id, [1])


def test_gib_metadata_and_bounded_parts(large):
    c = prepare(large, 5 << 30)
    assert c.transfer.part_count == 320 and c.transfer.part_size == 16 << 20
    assert layout(50 << 40)[1] <= 10000
    with pytest.raises(ServiceError, match='giới hạn'):
        prepare(large, (5 << 30)+1)
