"""Media case boundary: public DTO lowercase, stored images uppercase; no implicit fallback for invalid variants."""

import pytest
from pydantic import ValidationError

from src.rav.errors import ServiceError
from src.rav.store.pilot_db import DEFAULT_SAMPLE_SPEC
from src.rav.store.pilot_frames import ENCODING_VERSION, stored_variant, validate_set
from src.rav.store.pilot_media import MediaRequestIn


@pytest.mark.parametrize("variant", ["thumbnail", "THUMBNAIL", "Thumbnail"])
@pytest.mark.parametrize("side,expected", [(None, "THUMBNAIL"), (64, "THUMBNAIL"), (320, "THUMBNAIL"), (321, "FRAME")])
def test_thumbnail_storage_case_and_size(variant, side, expected):
    assert stored_variant(variant, side) == expected


@pytest.mark.parametrize("variant", ["frame", "FRAME", "Frame"])
@pytest.mark.parametrize("side", [None, 64, 320, 4096])
def test_full_frame_never_becomes_thumbnail(variant, side):
    assert stored_variant(variant, side) == "FRAME"


@pytest.mark.parametrize("variant", ["", "thumb", "thumnail", "context", "CONTEXT", " frame"])
def test_invalid_image_variant_is_not_silently_a_frame(variant):
    with pytest.raises(ServiceError) as caught:
        stored_variant(variant, None)
    assert caught.value.code == "validation_error"


@pytest.mark.parametrize("variant", ["FRAME", "THUMBNAIL", "CONTEXT", "Frame"])
def test_public_media_contract_still_requires_lowercase(variant):
    with pytest.raises(ValidationError):
        MediaRequestIn(source_id="00000000-0000-0000-0000-000000000001", unit_id="core:0-0", variant=variant)


@pytest.mark.parametrize("variants", [("frame", "thumbnail"), ("FRAME", "thumbnail")])
def test_bad_worker_manifest_is_rejected_instead_of_normalized(variants):
    source = {"scope": "SYSTEM", "core_source_id": "core", "metadata": {"n_units": 1}}
    frames = {"frame_set_id": "00000000-0000-0000-0000-000000000001", "n_units": 1,
              "encoding_version": ENCODING_VERSION, "sample_spec_hash": DEFAULT_SAMPLE_SPEC["spec_hash"],
              "items": [{"variant": v, "frame_idx": 0} for v in variants]}
    with pytest.raises(ServiceError) as caught:
        validate_set(source, frames)
    assert caught.value.code == "result_invalid"
