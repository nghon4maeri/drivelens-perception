"""YOLO original/flip consistency on requested anchors only; model/resources from Context (§18)."""
import hashlib
import json
import uuid
from pathlib import Path

from PIL import Image, ImageOps
from pydantic import Field

from ...core.component import Feature
from ...core.registry import register
from ...core.types import FieldTable
from ...pipeline import batch_yolo


def iou(a, b):
    """Normalized xyxy intersection/union."""
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0., right - left) * max(0., bottom - top)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
    return intersection / max(area - intersection, 1e-12)


def instability(original, transformed, *, with_stats: bool = False):
    """One-to-one geometric matching; class changes/unmatched boxes reduce agreement."""
    if not original and not transformed:
        res = (0., False)
        if with_stats:
            return (*res, {
                "n_original": 0,
                "n_flipped": 0,
                "n_matched": 0,
                "n_unmatched_orig": 0,
                "n_unmatched_flip": 0,
                "class_changes": 0,
            })
        return res
    pairs = sorted(((iou(a["box"], b["box"]), i, j) for i, a in enumerate(original)
                    for j, b in enumerate(transformed)), reverse=True)
    used_a, used_b, agreement = set(), set(), 0.
    class_changes = 0
    matched = 0
    for overlap, i, j in pairs:
        if overlap <= 0 or i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        matched += 1
        is_same = (original[i]["label"] == transformed[j]["label"])
        if not is_same:
            class_changes += 1
        agreement += overlap * is_same
    count = len(original) + len(transformed) - len(used_a)
    score = 1. - agreement / count
    res = (score, True)
    if with_stats:
        stats = {
            "n_original": len(original),
            "n_flipped": len(transformed),
            "n_matched": matched,
            "n_unmatched_orig": len(original) - len(used_a),
            "n_unmatched_flip": len(transformed) - len(used_b),
            "class_changes": class_changes,
        }
        return (*res, stats)
    return res


@register("feature")
class YoloConsistency(Feature):
    """YOLO detection original/flip: box/class/unmatched consistency, không phải xác suất lỗi. Cả hai rỗng là chưa có tín hiệu."""

    name = "yolo_consistency"
    version = "1"
    provides = "det.yolo_consistency"
    per_sample = True
    cache_scope = "requested"

    class Params(Feature.Params):
        model_id: str = Field("$reference_model", min_length=1)
        engine_version: str = batch_yolo.ENGINE_VERSION
        transform: str = "horizontal_flip"
        inference: dict = Field(default_factory=lambda: dict(batch_yolo.PARAMS))

    def compute(self, units, ctx):
        if (self.params.engine_version != batch_yolo.ENGINE_VERSION or self.params.transform != "horizontal_flip"
                or self.params.inference != batch_yolo.PARAMS):
            raise ValueError("Consistency engine/transform không hỗ trợ")
        if self.params.model_id not in ctx.model_files:
            raise ValueError("Thiếu checkpoint đã resolve trong Context")
        if ctx.model_lock is None or not ctx.model_lock.acquire(blocking=False):
            raise ValueError("YOLO đang chạy đợt khác; thử lại khi xong")
        try:
            path = ctx.model_files[self.params.model_id]
            raw_target = self.params.model_id.removeprefix("ym-")
            expected_sha = raw_target.split("-")[0] if "-" in raw_target else raw_target

            def verify():
                if len(expected_sha) == 64 and hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
                    raise ValueError("Checkpoint đổi; không dùng cache/model cũ")
            verify()

            out_dir = getattr(ctx, "output_dir", None)
            cache_dir = (Path(out_dir) / "inference-cache" / "yolo-consistency") if out_dir else None
            if cache_dir:
                cache_dir.mkdir(parents=True, exist_ok=True)

            model = None

            def get_model():
                nonlocal model
                if model is None:
                    try:
                        model = batch_yolo.load_model(path, ctx.output_dir)
                    except batch_yolo.YoloError:
                        raise
                    except Exception:
                        raise RuntimeError("Không nạp được YOLO detection") from None
                return model

            # Frame reads are not completed inference; report only after predictions/cache lookup.
            if hasattr(ctx, '_progress'):
                ctx._progress = None
            values = [None] * len(units)
            ordered = sorted(enumerate(units), key=lambda item: item[1].order_key)
            for completed, (idx, unit) in enumerate(ordered, 1):
                if getattr(ctx, 'cancel_event', None) is not None and ctx.cancel_event.is_set():
                    raise InterruptedError('Perception inference cancelled')
                anchor = unit.anchor
                if hasattr(anchor, "video_id") and hasattr(anchor, "idx") and hasattr(anchor, "t"):
                    pixel_id = {"kind": "frame", "video_id": str(anchor.video_id), "idx": int(anchor.idx), "t": round(float(anchor.t), 6)}
                elif hasattr(anchor, "source_id") and hasattr(anchor, "key"):
                    pixel_id = {"kind": "image", "source_id": str(anchor.source_id), "key": str(anchor.key)}
                else:
                    pixel_id = {"kind": "unit", "id": str(unit.id)}

                cache_payload = {
                    "model_sha": expected_sha,
                    "unit_id": unit.id,
                    "pixel_identity": pixel_id,
                    "engine_version": self.params.engine_version,
                    "transform": self.params.transform,
                    "inference": self.params.inference,
                    "algo_version": "yolo_consistency_v1",
                }
                cache_key = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode("utf-8")).hexdigest()
                cache_file = (cache_dir / f"{cache_key}.json") if cache_dir else None

                cached_row = None
                if cache_file and cache_file.exists():
                    try:
                        loaded = json.loads(cache_file.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict) and "instability" in loaded and "observed" in loaded:
                            cached_row = loaded
                    except Exception:
                        cached_row = None

                if cached_row is not None:
                    values[idx] = cached_row
                else:
                    loaded_model = get_model()
                    image = Image.fromarray(ctx.pixels_exact(unit.anchor))
                    try:
                        original = batch_yolo.detect(loaded_model, image, self.params.model_id)
                        flipped = batch_yolo.detect(loaded_model, ImageOps.mirror(image), self.params.model_id)
                    except batch_yolo.YoloError:
                        raise
                    except Exception:
                        raise RuntimeError("YOLO prediction lỗi") from None
                    for box in flipped:
                        x1, y1, x2, y2 = box["box"]
                        box["box"] = [1 - x2, y1, 1 - x1, y2]
                    score, observed, stats = instability(original, flipped, with_stats=True)
                    row = {"instability": score, "observed": observed, "original": original, "flipped": flipped, **stats}
                    if cache_dir and cache_file:
                        tmp_file = cache_dir / f"{cache_key}.{uuid.uuid4().hex}.part"
                        try:
                            tmp_file.write_text(json.dumps(row), encoding="utf-8")
                            tmp_file.replace(cache_file)
                        except OSError:
                            pass
                        finally:
                            tmp_file.unlink(missing_ok=True)
                    values[idx] = row

                if (completed == len(units) or completed % 8 == 0) and callable(getattr(ctx, '_on_progress', None)):
                    try:
                        ctx._on_progress("det.yolo_consistency", completed, len(units))
                    except Exception:
                        pass

            verify()
            return FieldTable(self.key, [u.id for u in units], values, "structured")
        finally:
            ctx.model_lock.release()
