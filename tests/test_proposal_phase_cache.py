"""§21.40: prompt cache reuse, cancellation and bounded proposal phase reporting, without models/network."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from src.rav.components.features import clip_b32 as clip
from src.rav.errors import ServiceError
from src.rav.store.pilot_storage import PilotStorage
from src.rav.worker.proposal_progress import ProposalProgress


@pytest.fixture
def text_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('RAV_CLIP_TEXT_CACHE_DIR', str(tmp_path / 'vectors'))
    calls = []
    monkeypatch.setattr(clip, '_load_text', lambda *a: (object(), object()))

    def infer(texts, *a):
        calls.append(list(texts))
        v = np.zeros((len(texts), 512), np.float32)
        for row, text in zip(v, texts, strict=True):
            row[sum(text.encode()) % 512] = 1
        return v

    monkeypatch.setattr(clip, '_infer_texts', infer)
    return tmp_path / 'vectors', calls


def test_prompt_cache_exact_key_partial_miss_order_and_model_revision(text_cache, monkeypatch):
    folder, calls = text_cache
    first = clip.encode_texts(['rain', 'night', 'rain'])
    assert calls == [['rain', 'night']]
    assert np.array_equal(first[0], first[2])
    second = clip.encode_texts(['night', 'truck'])
    assert calls[-1] == ['truck']
    assert np.array_equal(second[0], first[1])
    clip.encode_texts(['rain'], revision='new-revision')
    clip.encode_texts(['rain'], model='different-model')
    assert len(calls) == 4
    assert not list(folder.glob('*.part'))
    monkeypatch.setattr(clip, '_load_text', lambda *a: pytest.fail('cache hit must not load CLIP'))
    assert np.array_equal(clip.encode_texts(['rain', 'night', 'rain']), first)


@pytest.mark.parametrize('bad', [b'broken npy', None])
def test_corrupt_cache_is_recomputed(text_cache, bad):
    folder, calls = text_cache
    clip.encode_texts(['rain'])
    path = next(folder.glob('*.npy'))
    if bad is None:
        np.save(path, np.full(512, np.nan, np.float32))
    else:
        path.write_bytes(bad)
    clip.encode_texts(['rain'])
    assert len(calls) == 2
    assert np.isfinite(np.load(path, allow_pickle=False)).all()


def test_cancel_before_cache_io_and_between_inference_batches(text_cache, monkeypatch):
    folder, calls = text_cache
    stop = threading.Event()
    stop.set()
    with pytest.raises(ServiceError, match='dừng'):
        clip.encode_texts(['rain'], stop=stop)
    assert not folder.exists() and not calls
    stop.clear()
    infer = clip._infer_texts

    def cancel_after_batch(*args):
        result = infer(*args)
        stop.set()
        return result

    monkeypatch.setattr(clip, '_infer_texts', cancel_after_batch)
    with pytest.raises(ServiceError) as e:
        clip.encode_texts([str(i) for i in range(20)], stop=stop)
    assert e.value.code == 'cancel_requested' and len(calls) == 1
    assert not list(folder.glob('*.part'))


def test_phase_reporter_closes_timings_and_throttles_same_phase():
    updates = []
    runtime = SimpleNamespace(update_progress=lambda lease, p: updates.append(p.model_dump()))
    with ProposalProgress(runtime, None, threading.Event()) as progress:
        progress.phase('LOAD_FEATURES', 0, 20)
        for i in range(1, 21):
            progress.phase('LOAD_FEATURES', i, 20, cache_hit=True)
        progress.phase('proj.pca2', 0, 10)
        progress.phase('ODD_SELECT', 0, 4)
    assert len(updates) == 4  # phase boundaries and final flush, no write per unit
    stages = updates[-1]['stages']
    assert len(stages) == 3 and all(s['state'] == 'done' and s['elapsed_ms'] >= 0 for s in stages)
    assert stages[0]['done'] == 20 and stages[0]['cache_hit'] is True
    assert stages[1]['phase'].startswith('COMPUTE_')


def test_phase_reporter_preserves_failure_and_cancellation():
    updates = []
    stop = threading.Event()
    runtime = SimpleNamespace(update_progress=lambda lease, p: updates.append(p.model_dump()))
    with pytest.raises(ServiceError):
        with ProposalProgress(runtime, None, stop) as progress:
            progress.phase('LOAD_RAW')
            stop.set()
            progress.phase('LOAD_FEATURES')
    assert updates[-1]['stages'][0]['state'] == 'error'


def test_s3_chunk_cancel_removes_partial_download(tmp_path):
    stop = threading.Event()

    class Body:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, size):
            stop.set()
            return b'x' * size

    storage = PilotStorage(SimpleNamespace(get_object=lambda **kw: {'Body': Body()}), 'test', 'isolated')
    dest = tmp_path / 'download.part'
    with pytest.raises(ServiceError) as e:
        storage.download('isolated/raw', dest, 10 << 20, stop=stop)
    assert e.value.code == 'cancel_requested' and not dest.exists()
