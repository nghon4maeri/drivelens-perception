"""Freeze existing Torch Hub DINO source/weights into content-addressed S3 assets.

Called only by explicit DINO jobs; input cache on project storage, output scoped object refs.
No model inference or downloads on the backend. Temporary ZIP stays under output/tmp and is removed.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .remote_embed import EmbeddingValidationError


def prepare_assets(storage, output_dir):
    # local-only: backend has the frozen Torch Hub cache; deployment must provision these assets (LOCAL_LIMITS C2).
    hub = Path(os.environ.get('TORCH_HOME','data/system-cache/torch')) / 'hub'
    source = hub / 'facebookresearch_dinov2_main'
    weights = hub / 'checkpoints' / 'dinov2_vits14_pretrain.pth'
    if not (source / 'hubconf.py').is_file() or not weights.is_file():
        raise EmbeddingValidationError('Thiếu nguồn/checkpoint DINOv2-S trong TORCH_HOME; cần chuẩn bị cache trên ổ dự án.')
    temp_root = output_dir / 'tmp'
    temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dino-assets-',dir=temp_root) as folder:
        archive_path = Path(folder) / 'source.zip'
        files = sorted(source.rglob('*.py')) + [source / 'LICENSE']
        with zipfile.ZipFile(archive_path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                if not path.is_file() or path.is_symlink():
                    raise EmbeddingValidationError('Nguồn DINO không hợp lệ; không đóng gói code khác.')
                entry = zipfile.ZipInfo(path.relative_to(source).as_posix(),date_time=(1980,1,1,0,0,0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry,path.read_bytes())
        assets = {'source':storage.upload_raw(archive_path,'models/dinov2_s'),
                  'weights':storage.upload_raw(weights,'models/dinov2_s')}
    try:
        opencv = version('opencv-python-headless')
    except PackageNotFoundError:
        opencv = version('opencv-python')
    runtime = {'opencv':opencv,'imagehash':version('ImageHash'),'scipy':version('scipy')}
    return assets,runtime
