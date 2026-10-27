"""CLIP B/32 image/text embeddings, float32 L2 (512); checkpoint/revision pinned in Params.

Feature input: RGB pixels from Context, output: FieldTable. encode_texts is local and uses the same tokenizer/model.
No model load on import; image execution only during explicit prepare, not HTTP proposal.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import numpy as np
from PIL import Image
from pydantic import Field

from ...core.component import Feature
from ...core.registry import register
from ...core.types import FieldTable
from ...errors import ServiceError
from ...timing import timing

MODEL = "openai/clip-vit-base-patch32"
REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
BATCH = 8
_LOCK = threading.RLock()


@lru_cache(maxsize=1)
def _load(model: str, revision: str):
    from transformers import CLIPModel, CLIPProcessor

    # local-only: repo cache avoids filling the system disk; LOCAL_SINGLE_USER_LIMITS #73.
    cache = Path(os.environ.get("RAV_CLIP_CACHE_DIR", "data/model-cache/huggingface/hub"))
    return (CLIPModel.from_pretrained(model, revision=revision, cache_dir=str(cache), use_safetensors=False).eval(),
            CLIPProcessor.from_pretrained(model, revision=revision, cache_dir=str(cache), use_fast=False))


@lru_cache(maxsize=1)
def _load_text(model: str, revision: str):
    from transformers import CLIPTextModelWithProjection, CLIPTokenizerFast

    cache = Path(os.environ.get("RAV_CLIP_CACHE_DIR", "data/model-cache/huggingface/hub"))
    return (CLIPTextModelWithProjection.from_pretrained(model, revision=revision, cache_dir=str(cache),
                                                       use_safetensors=False).eval(),
            CLIPTokenizerFast.from_pretrained(model, revision=revision, cache_dir=str(cache)))


def normalize(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, np.float32)
    norm = np.linalg.norm(values, axis=-1, keepdims=True)
    if not np.isfinite(values).all() or (norm < 1e-12).any():
        raise ValueError("CLIP returned non-finite or zero embedding")
    return values / norm


def _infer_texts(texts, net, tokenizer):
    import torch

    inputs = tokenizer(list(texts), padding=True, truncation=False, return_tensors="pt")
    if inputs.input_ids.shape[1] > net.config.max_position_embeddings:
        raise ValueError("Câu CLIP quá dài (tối đa 77 token); hãy viết câu ngắn hơn.")
    with torch.inference_mode():
        return normalize(net(**inputs).text_embeds.float().cpu().numpy())


def encode_texts(texts: list[str], model: str = MODEL, revision: str = REVISION, *, progress=None, stop=None) -> np.ndarray:
    """Reuse exact per-prompt vectors across processes; only misses load the pinned text model (§21.40)."""
    def check():
        if stop is not None and stop.is_set():
            raise ServiceError('cancel_requested', 'đã yêu cầu dừng mã hóa truy vấn', 409)

    def report(phase, done=0, total=0, **kw):
        check()
        if progress:
            progress(phase, done, total, **kw)

    check()
    folder = Path(os.environ.get('RAV_CLIP_TEXT_CACHE_DIR') or
                  Path(os.environ.get('RAV_CLIP_CACHE_DIR', 'data/model-cache/huggingface/hub')).parent / 'text-vectors')
    folder.mkdir(parents=True, exist_ok=True)
    values, paths = {}, {}
    unique = list(dict.fromkeys(texts))
    report('CLIP_TEXT_CACHE', 0, len(unique))
    with timing('worker.clip_text.cache'):
        for text in unique:
            check()
            digest = hashlib.sha256(json.dumps([model, revision, 'float32-l2-v1', text], ensure_ascii=False).encode()).hexdigest()
            path = folder / f'{digest}.npy'
            paths[text] = path
            try:
                if path.stat().st_size > 4096:
                    raise ValueError('oversized vector cache')
                vector = np.load(path, allow_pickle=False)
                if vector.shape != (512,) or vector.dtype != np.float32 or not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-4):
                    raise ValueError('invalid text vector cache')
                values[text] = vector
                os.utime(path, None)
            except (OSError, ValueError, EOFError):
                pass
    missing = [t for t in unique if t not in values]
    report('CLIP_TEXT_CACHE', len(unique), len(unique), cache_hit=not missing)
    if missing:
        report('CLIP_MODEL')
        with _LOCK:
            with timing('worker.clip_text.model'):
                net, tokenizer = _load_text(model, revision)
            report('CLIP_TEXT_ENCODE', 0, len(missing))
            with timing('worker.clip_text.encode', cache_hit=False):
                for start in range(0, len(missing), BATCH):
                    check()
                    batch = missing[start:start+BATCH]
                    vectors = _infer_texts(batch, net, tokenizer)
                    for text, vector in zip(batch, vectors, strict=True):
                        vector = np.asarray(vector, np.float32)
                        if vector.shape != (512,) or not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-4):
                            raise ValueError('invalid CLIP text vector')
                        values[text] = vector
                        tmp = folder / f'{paths[text].stem}.{uuid4().hex}.part'
                        try:
                            with tmp.open('wb') as f:
                                np.save(f, vector, allow_pickle=False)
                            tmp.replace(paths[text])
                        finally:
                            tmp.unlink(missing_ok=True)
                    report('CLIP_TEXT_ENCODE', min(start+BATCH, len(missing)), len(missing))
        # At most ~4 MiB of vectors per cache; concurrent workers may temporarily exceed the count.
        files = []
        for path in folder.glob('*.npy'):
            try:
                files.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                pass
        for _, path in sorted(files)[:-2048]:
            path.unlink(missing_ok=True)
    check()
    return np.stack([values[t] for t in texts]) if texts else np.empty((0, 512), np.float32)


def encode_images(images: list[Image.Image], model: str = MODEL, revision: str = REVISION) -> np.ndarray:
    import torch

    with _LOCK:
        net, processor = _load(model, revision)
        inputs = processor(images=images, return_tensors="pt")
        with torch.inference_mode():
            return normalize(net.get_image_features(**inputs).float().cpu().numpy())


@register("feature")
class ClipB32(Feature):
    """CLIP ViT-B/32 image projection 512D, checkpoint preprocessing, frame mean then L2; same space as text encoder."""

    name = "clip_b32"
    version = "1"
    provides = "emb.clip_b32"
    per_sample = True

    class Params(Feature.Params):
        model: str = MODEL
        revision: str = Field(REVISION, pattern=r"^[0-9a-f]{40}$")

    def compute(self, units, ctx) -> FieldTable:
        if not units:
            return FieldTable(self.key, [], np.empty((0, 512), np.float32), "vector")
        frames = [f for u in units for f in (u.frames or (u.anchor,))]
        vectors = []
        for start in range(0, len(frames), BATCH):
            images = [Image.fromarray(ctx.pixels_exact(f)) for f in frames[start:start + BATCH]]
            vectors.append(encode_images(images, self.params.model, self.params.revision))
        emb = np.concatenate(vectors)
        out, i = [], 0
        for u in units:
            n = len(u.frames) or 1
            out.append(emb[i:i + n].mean(axis=0))
            i += n
        return FieldTable(self.key, [u.id for u in units], normalize(np.stack(out)), "vector")
