"""Seed regression: canonical anchors/feature identity and fail-closed model errors."""
import numpy as np
import pytest

from scripts.pilot_seed_sample_catalog import _build_feature_tables, seed_sample_video
from src.rav.components.features.clip_b32 import ClipB32
from src.rav.components.features.dinov2_s import DinoV2S
from src.rav.components.features.phash import PHash
from src.rav.core.types import FieldTable
from src.rav.pipeline.prepare import make_context
from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC, PilotDB
from src.rav.worker.handlers import _probe
from tests.conftest import make_video


def test_seed_uses_canonical_units_and_registered_feature_keys(pg, s3_storage, tmp_path, monkeypatch):
    path = tmp_path / "seed.mp4"
    make_video(path, seconds=1, fps=10)
    ctx = make_context([_probe(path)], {"source": None, **DEFAULT_SAMPLE_SPEC}, None)
    calls = []

    def compute(self, units, context):
        calls.append(self.provides)
        assert [u.id for u in units] == [u.id for u in ctx.units]
        values = (np.zeros(len(units), np.uint64) if self.provides == "hash.phash"
                  else np.ones((len(units), 384 if self.provides == "emb.dinov2_s" else 512), np.float32))
        return FieldTable(self.key, [u.id for u in units], values,
                          "scalar" if self.provides == "hash.phash" else "vector")

    for cls in (DinoV2S, PHash, ClipB32):
        monkeypatch.setattr(cls, "compute", compute)
    result = seed_sample_video(PilotDB(pg.admin_url), s3_storage, path)
    rows = pg.sql("SELECT unit_id,frame_idx FROM rav_pilot_frame WHERE variant='FRAME' ORDER BY frame_idx")
    assert [(r["unit_id"], r["frame_idx"]) for r in rows] == [(u.id, u.anchor.idx) for u in ctx.units]
    artifacts = pg.sql("SELECT manifest FROM rav_pilot_artifact WHERE source_id=%s", (result["source_id"],))
    keys = {name: key for a in artifacts for name, key in a["manifest"]["field_keys"].items()}
    from dataclasses import asdict

    assert keys == {cls().provides: asdict(cls().key) for cls in (DinoV2S, PHash, ClipB32)}
    assert calls == ["emb.dinov2_s", "hash.phash", "emb.clip_b32"]


def test_missing_model_never_falls_back_to_synthetic_embeddings(monkeypatch):
    def broken(self, units, ctx):
        raise RuntimeError("checkpoint unavailable")

    monkeypatch.setattr(DinoV2S, "compute", broken)
    from types import SimpleNamespace

    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        _build_feature_tables(SimpleNamespace(units=[]), "DINO")
