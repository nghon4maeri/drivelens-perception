"""Kaggle image embedding adapter (S10): missing units → bounded PNG ZIP → GPU → validated FieldTable.

Uses the feature's identical checkpoint/preprocessing; no CPU fallback. Temp files and only this job's session are cleaned.
Callable from prepare worker; no tokens or signed URLs in errors/logs. Transport can be injected in tests.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
from dotenv import dotenv_values
from PIL import Image

from ..core.types import FieldTable


class EmbeddingValidationError(ValueError):
    """Safe adapter-owned message; third-party exceptions are always redacted."""


class KaggleJobError(RuntimeError):
    """Adapter-owned stage message, safe to display without signed URLs/third-party text."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes(units, ctx) -> dict[str, str]:
    paths = {}
    for unit in units:
        if unit.video_id is not None:
            paths[unit.source_id] = Path(ctx.videos[unit.source_id].uri)
        else:
            for frame in unit.frames or (unit.anchor,):
                paths[frame.key] = Path(ctx.images[frame.source_id].root) / frame.relpath
    hashes = {key: sha256(path) for key, path in paths.items()}
    for video in ctx.videos.values():
        if video.id in hashes and hashes[video.id][:16] != video.id:
            raise EmbeddingValidationError("Video raw đã đổi; không ghi CLIP vào cache nguồn cũ.")
    return hashes


def validate_result(path: Path, manifest: dict, *, expected_job_id=None):
    dim = manifest.get('dim',512)
    dino = manifest.get('embedding_family') == 'dino'
    if dim != (384 if dino else 512):
        raise EmbeddingValidationError('Kaggle result dimension mismatch')
    with zipfile.ZipFile(path) as archive:
        bound = len(manifest['sample_ids'])*(dim*4+8) + len(json.dumps(manifest).encode())*4 + (1 << 20)
        names = {'values.npy','metadata.npy','phash.npy'} if dino else {'values.npy','metadata.npy'}
        if set(archive.namelist()) != names or len(archive.namelist()) != len(names) or sum(i.file_size for i in archive.infolist()) > bound:
            raise EmbeddingValidationError('Kaggle result container/size mismatch')
    with np.load(path, allow_pickle=False) as result:
        metadata = json.loads(str(result["metadata"].item()))
        values = np.asarray(result["values"])
        hashes = np.asarray(result['phash']) if dino else None
    expected_ids = manifest["sample_ids"]
    if metadata.get("manifest") != manifest or not metadata.get("job_id") or metadata.get("status") != "done":
        raise EmbeddingValidationError("Kaggle result metadata mismatch")
    if expected_job_id is not None and metadata['job_id'] != expected_job_id:
        raise EmbeddingValidationError('Kaggle result job ID mismatch')
    if len(set(expected_ids)) != len(expected_ids) or values.shape != (len(expected_ids), dim):
        raise EmbeddingValidationError("Kaggle result IDs/shape mismatch")
    if values.dtype != np.float32 or not np.isfinite(values).all():
        raise EmbeddingValidationError("Kaggle result dtype/non-finite mismatch")
    if not np.allclose(np.linalg.norm(values, axis=1), 1, atol=1e-4):
        raise EmbeddingValidationError("Kaggle result is not L2 normalized")
    if dino:
        if hashes.dtype != np.uint64 or hashes.shape != (len(expected_ids),):
            raise EmbeddingValidationError('Kaggle pHash dtype/shape mismatch')
        return values.copy(),hashes.copy()
    return values.copy()


def remote_compute(feature, units, ctx, *, kernel: str, output_dir: Path,
                   max_payload_bytes: int, timeout: float, client_factory=None, on_stage=None) -> FieldTable:
    if kernel and "/" not in kernel:
        raise ValueError("RAV_KAGGLE_KERNEL tuỳ chọn phải có dạng tài-khoản/notebook.")
    if not units:
        return FieldTable(feature.key, [], np.empty((0, 512), np.float32), "vector")
    if client_factory is None:
        from .kaggle_gpu import KaggleGPUClient
        token = dotenv_values(".env").get("KAGGLE_API_TOKEN")
        if not token:
            raise ValueError("Thiếu KAGGLE_API_TOKEN trong .env repo; không dùng auth từ nơi khác.")
        def client_factory():
            return KaggleGPUClient(api_token=token)
    temp_root = output_dir / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clip-", dir=temp_root) as folder:
        payload, result = Path(folder) / "input.zip", Path(folder) / "result.npz"
        hashes = source_hashes(units, ctx)
        manifest = {"field_key": asdict(feature.key), "model": feature.params.model, "revision": feature.params.revision,
                    "sample_ids": [u.id for u in units], "source_sha256": hashes, "dim": 512,
                    "dtype": "float32", "normalization": "L2", "extraction": "strict-index/PIL-EXIF-RGB/PNG", "samples": []}
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as archive:
            for i, unit in enumerate(units):
                files = []
                for j, frame in enumerate(unit.frames or (unit.anchor,)):
                    pixels = ctx.pixels_exact(frame)
                    buf = io.BytesIO()
                    Image.fromarray(pixels).save(buf, "PNG")
                    data, name = buf.getvalue(), f"images/{i:06d}-{j}.png"
                    if archive.fp.tell() + len(data) + 4096 > max_payload_bytes:
                        raise ValueError("CLIP payload vượt RAV_CLIP_MAX_PAYLOAD_MB; chọn video ngắn hơn hoặc tăng giới hạn.")
                    archive.writestr(name, data)
                    files.append({"name": name, "sha256": hashlib.sha256(data).hexdigest()})
                manifest["samples"].append(files)
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        if payload.stat().st_size > max_payload_bytes:
            raise ValueError("CLIP payload vượt giới hạn dung lượng")
        client, session = client_factory(), None
        stage = "notebook"
        def set_stage(value):
            nonlocal stage
            stage = value
            if on_stage:
                on_stage(value)
        try:
            set_stage("notebook")
            if hasattr(client, "ensure_kernel"):
                kernel = client.ensure_kernel(output_dir, override=kernel)
            # wait=False ensures the allocated session can be stopped even if boot/poll times out.
            set_stage("cấp GPU")
            session = client.create_session(kernel, accelerator="t4", enable_internet=True, wait=False)
            set_stage("khởi động GPU")
            client.wait_session(session, timeout=timeout)
            set_stage("upload frame")
            session.tools.put_file(str(payload), "/kaggle/working/rav-clip-input.zip")
            code = REMOTE_CODE.replace("__JOB_ID__", str(session.session_id))
            set_stage("tính embedding GPU")
            reply = session.tools.execute(code, timeout=timeout)
            if reply.get("status") != "ok":
                raise RuntimeError("Kaggle CLIP thực thi lỗi; không ghi cache, không chuyển sang CPU.")
            set_stage("tải và kiểm kết quả")
            session.tools.get_file("/kaggle/working/rav-clip-result.npz", str(result))
            values = validate_result(result, manifest)
            if source_hashes(units, ctx) != hashes:
                raise EmbeddingValidationError("Nguồn đã đổi trong lúc tính CLIP; không ghi cache.")
        except Exception as exc:
            # Third-party exceptions may contain a signed proxy URL; never expose their str().
            if isinstance(exc, EmbeddingValidationError):
                raise
            raise KaggleJobError(f"Job CLIP Kaggle lỗi tại bước {stage}; kiểm notebook/quota/kết nối rồi thử lại.") from None
        finally:
            if session is not None and session.session_id:
                try:
                    client.stop_session(session.session_id)
                except Exception:
                    raise RuntimeError("Không dừng được session CLIP của job; kiểm Kaggle trước khi thử lại.") from None
        return FieldTable(feature.key, manifest["sample_ids"], values, "vector")


REMOTE_CODE = r'''
import sys, subprocess, json, zipfile, hashlib, io
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'transformers==4.57.6'], check=True)
import torch, numpy as np
from PIL import Image
from transformers import CLIPModel, CLIPProcessor
if not torch.cuda.is_available():
    raise RuntimeError('CLIP requires GPU; no CPU fallback')
with zipfile.ZipFile('/kaggle/working/rav-clip-input.zip') as z:
    m = json.loads(z.read('manifest.json'))
    model = CLIPModel.from_pretrained(m['model'], revision=m['revision']).eval().to('cuda')
    processor = CLIPProcessor.from_pretrained(m['model'], revision=m['revision'], use_fast=False)
    vectors = []
    for start in range(0, len(m['samples']), 8):
        images, counts = [], []
        for files in m['samples'][start:start+8]:
            counts.append(len(files))
            for f in files:
                raw = z.read(f['name'])
                assert hashlib.sha256(raw).hexdigest() == f['sha256']
                images.append(Image.open(io.BytesIO(raw)).convert('RGB'))
        inputs = processor(images=images, return_tensors='pt').to('cuda')
        with torch.inference_mode():
            x = model.get_image_features(**inputs).float()
            x = x / x.norm(dim=-1, keepdim=True).clamp_min(1e-12)
            x = x.cpu().numpy()
        offset = 0
        for n in counts:
            v = x[offset:offset+n].mean(axis=0)
            vectors.append(v / max(np.linalg.norm(v), 1e-12))
            offset += n
metadata = {'manifest': m, 'job_id': '__JOB_ID__', 'status': 'done'}
np.savez('/kaggle/working/rav-clip-result.npz', values=np.asarray(vectors, dtype=np.float32), metadata=json.dumps(metadata, sort_keys=True))
print('CLIP samples', len(vectors), 'GPU', torch.cuda.get_device_name(0))
'''
