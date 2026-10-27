"""Kiểm thử thuật toán che mờ PII, canonical payload và HTML report (CONTRACTS §21.8).

- Kiểm tra redact: kích thước ảnh không đổi, pixelate + blur bảo mật.
- Kiểm tra PIIDetector: fail-closed khi thiếu/sai model, tile biển số về tọa độ ảnh, model thật nếu có trong data/.
- Kiểm tra canonical_export_payload_bytes: serialize tagged float deterministic, sha256 bất biến.
- Kiểm tra generate_export_report_html: HTML tự chứa, không remote script/font.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.rav.errors import ServiceError
from src.rav.worker.pii import (
    MODELS,
    PIIDetector,
    canonical_export_payload_bytes,
    deduplicate,
    generate_export_report_html,
    iou,
    redact,
)


def test_iou_and_deduplicate():
    box1 = [10.0, 10.0, 50.0, 50.0]
    box2 = [20.0, 20.0, 60.0, 60.0]
    score = iou(box1, box2)
    assert 0.0 < score < 1.0

    # Hai box giống hệt nhau
    assert iou(box1, box1) == 1.0

    detections = [
        {"kind": "face", "confidence": 0.95, "box": [10.0, 10.0, 50.0, 50.0]},
        {"kind": "face", "confidence": 0.80, "box": [12.0, 12.0, 52.0, 52.0]},  # trùng box trên
        {"kind": "license_plate", "confidence": 0.90, "box": [100.0, 100.0, 200.0, 150.0]},
    ]
    deduped = deduplicate(detections, threshold=0.5)
    assert len(deduped) == 2
    assert deduped[0]["confidence"] == 0.95
    assert deduped[1]["kind"] == "license_plate"


def test_redact_pixels_and_dimensions():
    # Tạo ảnh RGB kích thước 200x200
    img = Image.new("RGB", (200, 200), color=(120, 150, 200))
    # Tạo một vùng màu đỏ nổi bật để che
    for x in range(40, 80):
        for y in range(40, 80):
            img.putpixel((x, y), (255, 0, 0))

    detections = [
        {"kind": "face", "confidence": 0.92, "box": [40.0, 40.0, 80.0, 80.0]},
    ]
    redacted_img, regions = redact(img, detections)

    # 1. Kích thước không đổi
    assert redacted_img.size == (200, 200)
    assert len(regions) == 1

    # 2. Vùng màu đỏ ban đầu đã bị thay đổi (pixelate + blur)
    redacted_rgb = np.asarray(redacted_img)
    original_rgb = np.asarray(img)
    # Tâm của vùng (60, 60) không còn là (255, 0, 0)
    assert not np.array_equal(redacted_rgb[60, 60], [255, 0, 0])
    # Vùng ngoài không bị ảnh hưởng (0, 0)
    assert np.array_equal(redacted_rgb[0, 0], original_rgb[0, 0])


def test_pii_detector_fails_closed_on_missing_or_tampered_models(tmp_path):
    missing = PIIDetector(model_dir=tmp_path)
    assert not missing.ready and set(missing.errors) == {"face", "plate"}
    for call in (missing.require_ready, lambda: missing.detect(Image.new("RGB", (32, 32)))):
        with pytest.raises(ServiceError) as exc:
            call()
        assert exc.value.code == "pii_model_unavailable"
    for name, _ in MODELS.values():
        (tmp_path / name).write_bytes(b"not the pinned model")
    tampered = PIIDetector(model_dir=tmp_path)
    assert not tampered.ready and all("SHA-256" in e for e in tampered.errors.values())


class _Rows:
    """Fake plate session: one box in tile coordinates for every tile, recording tile origins via pixel marker."""
    def __init__(self):
        self.calls = 0

    def get_inputs(self):
        return [type("I", (), {"name": "images"})()]

    def run(self, _, feeds):
        self.calls += 1
        assert feeds["images"].shape == (1, 3, 640, 640) and feeds["images"].max() <= 1.0
        return [np.array([[0, 10, 20, 50, 40, 0, 0.9], [0, 0, 0, 5, 5, 0, 0.01]], np.float32)]


def test_plate_tiles_map_back_to_full_image_and_threshold():
    detector = PIIDetector(model_dir="/nonexistent")
    detector._plate_session = _Rows()
    boxes = detector._plates(np.zeros((720, 1280, 3), np.uint8))
    # tiles: x in {0, 512, 640}, y in {0, 80}; low-score row dropped
    assert detector._plate_session.calls == 6 and len(boxes) == 6
    assert {tuple(b["box"]) for b in boxes} >= {(10.0, 20.0, 50.0, 40.0), (650.0, 100.0, 690.0, 120.0)}
    small = PIIDetector(model_dir="/nonexistent")
    small._plate_session = _Rows()
    assert len(small._plates(np.zeros((240, 320, 3), np.uint8))) == 1   # one padded tile


@pytest.mark.parametrize("original", [(1280, 720), (501, 1001), (320, 240)])
def test_resize_inference_once_and_maps_boxes_to_original(original, monkeypatch):
    detector = PIIDetector(model_dir="/nonexistent")
    detector._plate_session = _Rows()
    detector._face_detector = object()
    observed = []
    monkeypatch.setattr(detector, "_faces", lambda rgb: observed.append(rgb.shape) or [])
    image = Image.new("RGB", original)
    boxes = detector.detect(image)
    w, h = detector.last_inference_size
    assert max(w, h) <= 640 and w <= original[0] and h <= original[1]
    assert detector._plate_session.calls == 1 and observed == [(h, w, 3)]
    assert boxes[0]["box"] == pytest.approx([10 * original[0] / w, 20 * original[1] / h,
                                            50 * original[0] / w, 40 * original[1] / h])
    redacted, regions = redact(image, boxes)
    assert redacted.size == original and regions[0]["blur_box"][0] <= boxes[0]["box"][0]


REAL_MODELS = Path("data/models/pii")


@pytest.mark.skipif(not all((REAL_MODELS / n).is_file() for n, _ in MODELS.values()), reason="cần model PII trong data/models/pii")
def test_real_pinned_models_load_and_run():
    detector = PIIDetector(model_dir=REAL_MODELS)
    assert detector.ready, detector.errors
    assert detector.model_manifest["plate"]["sha256"] == MODELS["plate"][1]
    assert detector.detect(Image.new("RGB", (1280, 720), (90, 90, 90))) == []


def test_canonical_export_payload_determinism():
    payload_a = {
        "format_version": 1,
        "export_id": "00000000-0000-0000-0000-000000000001",
        "snapshot_id": "00000000-0000-0000-0000-000000000002",
        "decision_seq": 10,
        "proposal_cutoff_n": 2,
        "members": [
            {
                "ordinal": 0,
                "core_unit_id": "src1:0.0-1.0",
                "width": 1280,
                "height": 720,
                "confidence": 0.87654321,
            }
        ],
        "report_core": {"goal": "test goal", "score": 0.12345},
    }

    # Đảo thứ tự keys trong dict B
    payload_b = {
        "report_core": {"score": 0.12345, "goal": "test goal"},
        "snapshot_id": "00000000-0000-0000-0000-000000000002",
        "format_version": 1,
        "export_id": "00000000-0000-0000-0000-000000000001",
        "decision_seq": 10,
        "proposal_cutoff_n": 2,
        "members": [
            {
                "confidence": 0.87654321,
                "height": 720,
                "width": 1280,
                "core_unit_id": "src1:0.0-1.0",
                "ordinal": 0,
            }
        ],
    }

    bytes_a = canonical_export_payload_bytes(payload_a)
    bytes_b = canonical_export_payload_bytes(payload_b)

    # 1. Hai lần serialize cho chính xác cùng byte và cùng SHA-256
    assert bytes_a == bytes_b
    hash_a = hashlib.sha256(bytes_a).hexdigest()
    hash_b = hashlib.sha256(bytes_b).hexdigest()
    assert hash_a == hash_b

    # 2. Toàn bộ float đã được đóng gói thành tagged decimal
    parsed = json.loads(bytes_a.decode("utf-8"))
    member_conf = parsed["members"][0]["confidence"]
    assert member_conf["kind"] == "float64"
    assert member_conf["precision"] == "binary64_roundtrip"


def test_generate_export_report_html():
    members = [
        {"ordinal": 0, "core_unit_id": "unit_001", "output_relpath": "images/000000.png", "output_sha256": "abc123456789"},
    ]
    pii_evidence = [
        {"ordinal": 0, "detections": [{"kind": "face"}, {"kind": "license_plate"}]},
    ]
    report_core = {"goal": "giảm trùng lặp"}

    html = generate_export_report_html(
        export_id="exp-1234",
        snapshot_id="snap-5678",
        payload_hash="payload_hash_mock_12345",
        n_members=1,
        members=members,
        pii_evidence=pii_evidence,
        report_core=report_core,
    )

    assert "<!DOCTYPE html>" in html
    assert "exp-1234" in html
    assert "snap-5678" in html
    assert "payload_hash_mock_12345" in html
    assert "images/000000.png" in html
    # HTML tự chứa, không chứa link tới external script hoặc font
    assert "<script" not in html
    assert "http://" not in html and "https://" not in html
