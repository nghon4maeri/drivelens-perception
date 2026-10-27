"""DINO transport pins the installed OpenCV wheel on worker hosts."""
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace

from src.rav.pipeline import dino_assets


def test_regular_opencv_worker_metadata(monkeypatch, tmp_path):
    source = tmp_path / 'hub/facebookresearch_dinov2_main'
    source.mkdir(parents=True)
    (source / 'hubconf.py').write_text('# frozen source')
    (source / 'LICENSE').write_text('license')
    weights = tmp_path / 'hub/checkpoints/dinov2_vits14_pretrain.pth'
    weights.parent.mkdir()
    weights.write_bytes(b'frozen weights')
    monkeypatch.setenv('TORCH_HOME', str(tmp_path))

    def version(package):
        if package == 'opencv-python-headless':
            raise PackageNotFoundError(package)
        return {'opencv-python': '4.11.0.86', 'ImageHash': '4.3.1', 'scipy': '1.15.0'}[package]

    monkeypatch.setattr(dino_assets, 'version', version)
    _, runtime = dino_assets.prepare_assets(SimpleNamespace(upload_raw=lambda *args: {}), tmp_path)
    assert runtime == {'opencv': '4.11.0.86', 'imagehash': '4.3.1', 'scipy': '1.15.0'}
