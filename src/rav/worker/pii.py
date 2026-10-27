"""Xử lý che mờ PII (khuôn mặt và biển số xe) và đóng gói dữ liệu Snapshot (CONTRACTS §21.8).

Pipeline:
1. `redact(image, detections, config)`: Làm mờ bảo mật kết hợp Pixelate 16x và GaussianBlur.
2. `PIIDetector`: YuNet + YOLOv9-t end2end trên ảnh resize tối đa 640, scale box về ảnh gốc, model ghim SHA-256 trong `RAV_PII_MODEL_DIR`
   (mặc định `data/models/pii`). Thiếu/sai model → `pii_model_unavailable`, không có detector giả (fail-closed).
3. `build_export_payload(...)`: Chuẩn hóa canonical payload JSON UTF-8 (sort_keys, tagged float) và tính `payload_hash`.
4. `generate_export_report_html(...)`: Sinh báo cáo HTML offline tự chứa, không phụ thuộc script/font ngoài.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

PIPELINE_VERSION = "pii-onnx-resize-v2"
DEFAULT_CONFIG = {
    "inference_max_side": 640,
    "inference_mode": "resize_single_pass",
    "face_threshold": 0.3,
    "plate_threshold": 0.15,
    "face_confident": 0.8,
    "plate_confident": 0.6,
    "tile_size": 640,
    "tile_overlap": 0.2,
    "nms_iou": 0.35,
    "padding": 0.25,
    "min_padding": 6,
    "blur": "pixelate16+gaussian",
    "pipeline_version": PIPELINE_VERSION,
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iou(box_a: list[float], box_b: list[float]) -> float:
    x_left = max(box_a[0], box_b[0])
    y_top = max(box_a[1], box_b[1])
    x_right = min(box_a[2], box_b[2])
    y_bottom = min(box_a[3], box_b[3])
    intersection = max(0.0, x_right - x_left) * max(0.0, y_bottom - y_top)
    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def deduplicate(detections: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in sorted(detections, key=lambda x: -x["confidence"]):
        if not any(d["kind"] == o["kind"] and iou(d["box"], o["box"]) > threshold for o in out):
            out.append(d)
    return out


def redact(
    image: Image.Image,
    detections: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> tuple[Image.Image, list[dict[str, Any]]]:
    """Che mờ toàn bộ các vùng phát hiện nhạy cảm bằng Pixelate + GaussianBlur.

    Không bảo lưu EXIF. Toàn bộ tính toán bounding box được clamp trong kích thước ảnh.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    out = ImageOps.exif_transpose(image).convert("RGB")
    width, height = out.size
    regions = []

    for detection in detections:
        x1, y1, x2, y2 = detection["box"]
        px = max(float(cfg["min_padding"]), (x2 - x1) * float(cfg["padding"]))
        py = max(float(cfg["min_padding"]), (y2 - y1) * float(cfg["padding"]))
        box = (
            max(0, math.floor(x1 - px)),
            max(0, math.floor(y1 - py)),
            min(width, math.ceil(x2 + px)),
            min(height, math.ceil(y2 + py)),
        )
        if box[0] >= box[2] or box[1] >= box[3]:
            continue

        roi = out.crop(box)
        w, h = roi.size
        # Bước 1: Pixelate tỷ lệ 1/16
        small_w = max(1, w // 16)
        small_h = max(1, h // 16)
        roi_pix = roi.resize((small_w, small_h), Image.Resampling.BOX).resize((w, h), Image.Resampling.NEAREST)
        # Bước 2: GaussianBlur làm mờ viền
        blur_radius = max(8.0, min(w, h) * 0.4)
        roi_blurred = roi_pix.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        out.paste(roi_blurred, box)
        regions.append({**detection, "blur_box": list(box)})

    return out, regions


MODELS = {   # CONTRACTS §21.8 DEP6: pinned files; any mismatch keeps export fail-closed
    "face": ("face_detection_yunet_2023mar.onnx", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "plate": ("yolo-v9-t-640-license-plates-end2end.onnx", "c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076"),
}


class PIIDetector:
    """YuNet + YOLOv9-t on one resized image, boxes mapped to original pixels. No fake fallback:
    `ready` is False unless both pinned models load, and `require_ready()` raises `pii_model_unavailable`."""

    def __init__(self, config: dict[str, Any] | None = None, model_dir: Path | str | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        side = self.config["inference_max_side"]
        if isinstance(side, bool) or not isinstance(side, int) or not 1 <= side <= 640:
            raise ValueError("inference_max_side phải là số nguyên trong 1..640")
        self.model_dir = Path(model_dir or os.environ.get("RAV_PII_MODEL_DIR") or "data/models/pii")
        self.errors: dict[str, str] = {}
        self._face_detector = None
        self._plate_session = None
        threads = max(1, int(os.environ.get("RAV_MODEL_THREADS") or 1))   # CPU lane budget (BULK = 1)
        for kind, (name, digest) in MODELS.items():
            path = self.model_dir / name
            try:
                if not path.is_file():
                    raise FileNotFoundError(f"thiếu {name}")
                data = path.read_bytes()   # load the verified bytes: no swap after the check, no non-ASCII path issue in cv2
                if sha256_bytes(data) != digest:
                    raise ValueError(f"{name} sai SHA-256")
                if kind == "face":
                    import cv2
                    cv2.setNumThreads(threads)
                    self._face_detector = cv2.FaceDetectorYN.create(
                        "onnx", np.frombuffer(data, np.uint8), np.empty(0, np.uint8), (640, 640),
                        float(self.config["face_threshold"]), 0.3, 5000)
                else:
                    import onnxruntime as ort
                    opts = ort.SessionOptions()
                    opts.intra_op_num_threads, opts.inter_op_num_threads = threads, 1
                    self._plate_session = ort.InferenceSession(data, sess_options=opts, providers=["CPUExecutionProvider"])
            except Exception as exc:   # ready stays False; reason surfaces in the export error
                self.errors[kind] = str(exc)[:200]

    @property
    def ready(self) -> bool:
        return self._face_detector is not None and self._plate_session is not None

    def require_ready(self) -> None:
        if not self.ready:
            from ..errors import ServiceError
            raise ServiceError("pii_model_unavailable", "worker thiếu model che PII; không xuất ảnh chưa che", 503,
                               {"model_dir": str(self.model_dir), "errors": self.errors})

    @property
    def model_manifest(self) -> dict[str, Any]:
        return {**{kind: {"file": name, "sha256": digest, "ready": kind not in self.errors} for kind, (name, digest) in MODELS.items()},
                "pipeline_version": self.config["pipeline_version"]}

    def detect(self, image: Image.Image) -> list[dict[str, Any]]:
        """Face + plate boxes in full-image pixel coordinates, clamped, finite, deduplicated per kind."""
        self.require_ready()
        picture = image.convert("RGB")
        width, height = picture.size
        picture.thumbnail((int(self.config["inference_max_side"]), int(self.config["inference_max_side"])), Image.Resampling.LANCZOS)
        self.last_inference_size = picture.size
        rgb = np.asarray(picture)
        sx, sy = width / picture.width, height / picture.height
        detections = self._faces(rgb) + self._plates(rgb)
        valid = []
        for d in detections:
            if not math.isfinite(d["confidence"]) or not np.isfinite(d["box"]).all():
                continue
            box = [max(0.0, min(float(width), d["box"][0] * sx)), max(0.0, min(float(height), d["box"][1] * sy)),
                   max(0.0, min(float(width), d["box"][2] * sx)), max(0.0, min(float(height), d["box"][3] * sy))]
            if box[0] < box[2] and box[1] < box[3]:
                valid.append({**d, "box": box})
        return deduplicate(valid, float(self.config["nms_iou"]))

    def _faces(self, rgb: np.ndarray) -> list[dict[str, Any]]:
        height, width = rgb.shape[:2]
        self._face_detector.setInputSize((width, height))
        _, faces = self._face_detector.detect(np.ascontiguousarray(rgb[:, :, ::-1]))
        return [{"kind": "face", "confidence": float(f[-1]), "box": [float(f[0]), float(f[1]), float(f[0] + f[2]), float(f[1] + f[3])]}
                for f in (faces if faces is not None else [])]

    def _plates(self, rgb: np.ndarray) -> list[dict[str, Any]]:
        size = int(self.config["tile_size"])
        step = max(1, int(size * (1 - float(self.config["tile_overlap"]))))
        height, width = rgb.shape[:2]

        def starts(n):   # tile origins; the last tile sits flush with the edge
            return sorted({*range(0, max(n - size, 0) + 1, step), max(n - size, 0)})

        out = []
        name = self._plate_session.get_inputs()[0].name
        for y0 in starts(height):
            for x0 in starts(width):
                tile = np.full((size, size, 3), 114, np.uint8)
                crop = rgb[y0:y0 + size, x0:x0 + size]
                tile[:crop.shape[0], :crop.shape[1]] = crop
                rows = self._plate_session.run(None, {name: (tile.transpose(2, 0, 1)[None] / 255.0).astype(np.float32)})[0]
                for _, x1, y1, x2, y2, _, score in np.asarray(rows).reshape(-1, 7):
                    if score >= float(self.config["plate_threshold"]):
                        out.append({"kind": "plate", "confidence": float(score),
                                    "box": [float(x1) + x0, float(y1) + y0, float(x2) + x0, float(y2) + y0]})
        return out


def _tag_floats(obj: Any) -> Any:
    """Tagged decimal serializer cho toàn bộ float theo CONTRACTS §21.8 / spec §6."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("Non-finite float không được phép trong export payload")
        return {
            "kind": "float64",
            "value": f"{obj:.8g}",
            "precision": "binary64_roundtrip",
            "unit": None,
        }
    if isinstance(obj, dict):
        return {k: _tag_floats(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_tag_floats(x) for x in obj]
    return obj


def canonical_export_payload_bytes(payload_dict: dict[str, Any]) -> bytes:
    """Tuần tự hóa canonical UTF-8 JSON không BOM, key sắp xếp, không space thừa."""
    tagged = _tag_floats(payload_dict)
    text = json.dumps(tagged, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8")


def generate_export_report_html(
    export_id: str,
    snapshot_id: str,
    payload_hash: str,
    n_members: int,
    members: list[dict[str, Any]],
    pii_evidence: list[dict[str, Any]],
    report_core: dict[str, Any],
) -> str:
    """Sinh báo cáo HTML tự chứa, an toàn không gọi API bên ngoài."""
    faces_count = sum(len([d for d in ev.get("detections", []) if d.get("kind") == "face"]) for ev in pii_evidence)
    plates_count = sum(len([d for d in ev.get("detections", []) if d.get("kind") in ("plate", "license_plate")]) for ev in pii_evidence)

    rows = []
    for m in members:
        ord_idx = m.get("ordinal", 0)
        unit = m.get("core_unit_id", "")
        sha = m.get("output_sha256", "")[:12]
        rel = m.get("output_relpath", "")
        rows.append(f"<tr><td>{ord_idx}</td><td><code>{unit}</code></td><td>{rel}</td><td><code>{sha}</code></td></tr>")

    table_rows = "\n".join(rows)

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DriveLens — Báo cáo xuất dữ liệu PII Redaction - {export_id[:8]}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 2rem; background: #0f172a; color: #f8fafc; }}
  .card {{ background: #1e293b; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; border: 1px solid #334155; }}
  h1 {{ font-size: 1.5rem; margin-top: 0; color: #38bdf8; }}
  h2 {{ font-size: 1.2rem; color: #94a3b8; border-bottom: 1px solid #334155; padding-bottom: 0.5rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
  .stat {{ background: #0f172a; padding: 1rem; border-radius: 6px; }}
  .stat-label {{ font-size: 0.85rem; color: #94a3b8; }}
  .stat-val {{ font-size: 1.25rem; font-weight: bold; color: #f1f5f9; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid #334155; }}
  th {{ background: #0f172a; color: #94a3b8; }}
  code {{ font-family: monospace; background: #0f172a; padding: 2px 4px; border-radius: 4px; color: #38bdf8; }}
</style>
</head>
<body>
  <div class="card">
    <h1>DriveLens — Báo cáo Xuất Dữ liệu Snapshot (PII Redacted)</h1>
    <div class="grid">
      <div class="stat"><div class="stat-label">Export ID</div><div class="stat-val"><code>{export_id}</code></div></div>
      <div class="stat"><div class="stat-label">Snapshot ID</div><div class="stat-val"><code>{snapshot_id}</code></div></div>
      <div class="stat"><div class="stat-label">Tổng số ảnh</div><div class="stat-val">{n_members}</div></div>
      <div class="stat"><div class="stat-label">Vùng mặt đã che</div><div class="stat-val">{faces_count}</div></div>
      <div class="stat"><div class="stat-label">Biển số đã che</div><div class="stat-val">{plates_count}</div></div>
    </div>
    <div style="margin-top: 1rem;">
      <span class="stat-label">Payload Hash (SHA-256):</span><br>
      <code>{payload_hash}</code>
    </div>
    <p style="margin-top: 1rem;">Che tự động (YuNet + YOLOv9-t biển số) có thể sót hoặc che thừa; chủ sở hữu đã xem
    preview trước khi xác nhận. Số vùng đã che không phải số đo recall/precision.</p>
  </div>

  <div class="card">
    <h2>Danh sách mẫu xuất (Export Members)</h2>
    <table>
      <thead>
        <tr><th>STT</th><th>Unit ID</th><th>Đường dẫn file</th><th>Checksum SHA-256</th></tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
