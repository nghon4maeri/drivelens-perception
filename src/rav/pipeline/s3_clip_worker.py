"""Standalone Kaggle worker: scoped S3 URLs → strict pixels → DINO/CLIP → S3 result/progress.

Run remotely: python s3_clip_worker.py config.json. No AWS/Supabase keys. Always remove downloaded raw/config.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            result.update(chunk)
    return result.hexdigest()


def download(url, path, expected, size):
    import requests
    count = 0
    with requests.get(url, stream=True, timeout=(20,120)) as response:
        response.raise_for_status()
        with path.open('wb') as output:
            for chunk in response.iter_content(1 << 20):
                count += len(chunk)
                if count > size:
                    raise ValueError('input_size')
                output.write(chunk)
    if count != size or digest(path) != expected:
        raise ValueError('input_sha256')


def upload(url, path, content_type):
    import requests
    with path.open('rb') as source:
        response = requests.put(url, data=source, headers={'Content-Type':content_type}, timeout=(20,120))
        response.raise_for_status()


class Pixels:
    """Strict decoder with at most one RGB frame per video handle, no timestamp index approximation."""
    def __init__(self, sources, paths):
        self.sources, self.paths, self.handles = sources, paths, {}

    def read(self, frame):
        import av
        import numpy as np
        from PIL import Image, ImageOps

        key = frame['source']
        if self.sources[key]['kind'] == 'image':
            with Image.open(self.paths[key]) as image:
                return np.asarray(ImageOps.exif_transpose(image).convert('RGB')).copy()
        target = frame['idx']
        handle = self.handles.get(key)
        if handle is None:
            container = av.open(str(self.paths[key]))
            stream = container.streams.video[0]
            stream.thread_type = 'AUTO'
            handle = {'container':container,'stream':stream,'next':0,'frames':container.decode(stream),'last':None}
            self.handles[key] = handle
        if handle['last'] is not None and handle['last'][0] == target:
            return handle['last'][1]
        if target < handle['next']:
            handle['container'].seek(0)
            handle.update(next=0,frames=handle['container'].decode(handle['stream']),last=None)
        for decoded in handle['frames']:
            index = handle['next']
            handle['next'] += 1
            if index == target:
                pixels = decoded.to_ndarray(format='rgb24')
                handle['last'] = (index,pixels)
                return pixels
        raise ValueError('frame_index')

    def close(self):
        for handle in self.handles.values():
            handle['container'].close()


def dino_preprocess(images, size):
    """Same float32 INTER_AREA/ImageNet transform as dinov2_s@1."""
    import cv2
    import numpy as np
    mean = np.array([0.485,0.456,0.406],np.float32)
    std = np.array([0.229,0.224,0.225],np.float32)
    return np.stack([((cv2.resize(np.asarray(image),(size,size),interpolation=cv2.INTER_AREA)
                      .astype(np.float32)/255.0-mean)/std).transpose(2,0,1) for image in images])


def dino_model(manifest, config, root):
    import torch
    assets = manifest['assets']
    for name, asset in assets.items():
        download(config['asset_urls'][name],root/name,asset['sha256'],asset['size_bytes'])
    source = root / 'dino-source'
    source.mkdir()
    with zipfile.ZipFile(root/'source') as archive:
        if sum(i.file_size for i in archive.infolist()) > 32 << 20 or len(archive.infolist()) > 2000:
            raise ValueError('model_assets')
        for entry in archive.infolist():
            path = Path(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename
                    or path.suffix != '.py' and entry.filename != 'LICENSE'
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('model_assets')
            dest = source / path
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(archive.read(entry))
    os.environ['XFORMERS_DISABLED'] = '1'
    model = torch.hub.load(str(source),'dinov2_vits14',source='local',pretrained=False)
    model.load_state_dict(torch.load(root/'weights',map_location='cpu',weights_only=True),strict=True)
    return model.eval().to('cuda')


def run(config_path, *, embed_batch=None):
    """Inject embed_batch for CPU-free transport/parity tests. Runtime production always requires CUDA."""
    from importlib.metadata import version

    import av
    import numpy as np
    import PIL
    import requests
    from PIL import Image

    config_path = Path(config_path)
    config = json.loads(config_path.read_text())
    manifest = config['manifest']
    dino = manifest.get('embedding_family') == 'dino'
    root = config_path.parent / 'raw'
    root.mkdir(exist_ok=True)
    progress = {'job_id':config['job_id'],'manifest_hash':config['manifest_hash'],'sequence':0,
                'state':'running','stage':'tải dữ liệu S3','done':0,'total':len(manifest['samples']), 'cleanup_done':False}
    reader, result, failed = None, config_path.parent / 'result.npz', False

    def report(**kwargs):
        progress.update(kwargs)
        progress['sequence'] += 1
        response = requests.put(config['progress_url'], json=progress, timeout=(20,30))
        response.raise_for_status()

    try:
        report()
        runtime = manifest['runtime']
        if (av.__version__ != runtime['av'] or PIL.__version__ != runtime['pillow']
                or {k:list(v) for k,v in av.library_versions.items()} != runtime['ffmpeg']):
            raise ValueError('decoder_runtime')
        if dino and any(version(package) != runtime[key] for package,key in
                        [('opencv-python-headless','opencv'),('ImageHash','imagehash'),('scipy','scipy')]):
            raise ValueError('decoder_runtime')
        paths = {}
        for i, (key, source) in enumerate(manifest['sources'].items()):
            paths[key] = root / f'{i:06d}.raw'
            download(config['input_urls'][key], paths[key], source['sha256'], source['size_bytes'])
        reader = Pixels(manifest['sources'], paths)
        for check in manifest['pixel_checks']:
            pixels = reader.read(check['frame'])
            if hashlib.sha256(pixels.tobytes()).hexdigest() != check['sha256']:
                raise ValueError('pixel_parity')
        report(stage='nạp '+('DINO' if dino else 'CLIP')+' GPU')
        if embed_batch is None:
            import torch
            if not torch.cuda.is_available():
                raise ValueError('cuda_required')
            if dino:
                model = dino_model(manifest,config,root)
                def embed_batch(images):
                    inputs = torch.from_numpy(dino_preprocess(images,manifest['image_size'])).to('cuda')
                    with torch.inference_mode():
                        return model(inputs).float().cpu().numpy()
            else:
                from transformers import CLIPModel, CLIPProcessor
                model = CLIPModel.from_pretrained(manifest['model'],revision=manifest['revision']).eval().to('cuda')
                processor = CLIPProcessor.from_pretrained(manifest['model'],revision=manifest['revision'],use_fast=False)
                def embed_batch(images):
                    inputs = processor(images=images,return_tensors='pt').to('cuda')
                    with torch.inference_mode():
                        values = model.get_image_features(**inputs).float()
                        values = values / values.norm(dim=-1,keepdim=True).clamp_min(1e-12)
                        return values.cpu().numpy()
        vectors, hashes = [], []
        for start in range(0,len(manifest['samples']),8):
            images, counts = [], []
            for index,frames in enumerate(manifest['samples'][start:start+8],start):
                counts.append(len(frames))
                anchor_image = None
                for frame in frames:
                    image = Image.fromarray(reader.read(frame))
                    images.append(image)
                    if dino and frame == manifest['anchors'][index]:
                        anchor_image = image
                if dino:
                    import imagehash
                    if anchor_image is None:
                        anchor_image = Image.fromarray(reader.read(manifest['anchors'][index]))
                    hashes.append(int(str(imagehash.phash(anchor_image)),16))
            values = embed_batch(images)
            offset = 0
            for count in counts:
                vector = values[offset:offset+count].mean(axis=0)
                vectors.append(vector / max(np.linalg.norm(vector),1e-12))
                offset += count
            report(stage='tính embedding GPU',done=len(vectors))
        metadata = {'manifest':manifest,'job_id':config['job_id'],'status':'done'}
        extra = {'phash':np.asarray(hashes,dtype=np.uint64)} if dino else {}
        np.savez(result, values=np.asarray(vectors,dtype=np.float32),metadata=json.dumps(metadata,sort_keys=True),**extra)
        report(stage='lưu embedding S3')
        result_sha = digest(result)
        upload(config['result_url'], result, 'application/octet-stream')
        progress.update(result_sha256=result_sha,state='done',stage='embedding đã lưu S3')
    except BaseException as exc:
        failed = True
        progress.update(state='error',stage='Kaggle worker lỗi',error='Kaggle tải/decode/embedding/upload lỗi; không nhận cache partial.')
        if isinstance(exc,ValueError) and str(exc) in ('decoder_runtime','pixel_parity','input_size','input_sha256','frame_index','cuda_required','model_assets'):
            progress['error_code'] = str(exc)
    finally:
        if reader:
            reader.close()
        try:
            shutil.rmtree(root)
            result.unlink(missing_ok=True)
            config_path.unlink(missing_ok=True)
            progress['cleanup_done'] = True
        except Exception:
            failed = True
            progress.update(state='error',stage='cleanup Kaggle lỗi',error='Không dọn được dữ liệu tạm Kaggle.')
        # Scoped progress URL remains only in process memory after config deletion.
        report()
    return not failed


if __name__ == '__main__':
    # Versions come from the backend manifest; no untrusted command strings are executed.
    config_file = Path(sys.argv[1])
    runtime = json.loads(config_file.read_text())['manifest']['runtime']
    try:
        packages = ['transformers==4.57.6','av==' + runtime['av'],'Pillow==' + runtime['pillow']]
        if runtime.get('opencv'):
            packages += ['opencv-python-headless=='+runtime['opencv'],'ImageHash=='+runtime['imagehash'],'scipy=='+runtime['scipy']]
        subprocess.run([sys.executable,'-m','pip','install','-q',*packages], check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ok = run(config_file)
    except BaseException:
        # Bootstrap failure must not leave downloaded data or presigned config on disk.
        shutil.rmtree(config_file.parent / 'raw',ignore_errors=True)
        config_file.unlink(missing_ok=True)
        ok = False
    raise SystemExit(0 if ok else 1)
