"""Read requested consistency field; attach scalar/detection primitives for human review (§18)."""
import numpy as np

from ...core.registry import register
from ...core.types import Scores
from .base import Objective


@register("objective")
class DetectionInstability(Objective):
    """Ưu tiên prediction thay đổi giữa original/flip. Không box ở cả hai = chưa có tín hiệu, không kết luận ảnh dễ/model đúng."""

    name = "detection_instability"
    version = "1"

    class Params(Objective.Params):
        field: str = "det.yolo_consistency"

    def score(self, units, ctx):
        if not units:
            return Scores(self.name, [], np.empty(0))
        rows = ctx.field(self.params.field, units).values
        for u, row in zip(units, rows):
            matching_info = {
                "n_original": row.get("n_original", len(row.get("original", []))),
                "n_flipped": row.get("n_flipped", len(row.get("flipped", []))),
                "n_matched": row.get("n_matched", 0),
                "n_unmatched_orig": row.get("n_unmatched_orig", 0),
                "n_unmatched_flip": row.get("n_unmatched_flip", 0),
                "class_changes": row.get("class_changes", 0),
            }
            if row["observed"]:
                unmatched_total = matching_info["n_unmatched_orig"] + matching_info["n_unmatched_flip"]
                explanation = (f"{matching_info['n_original']} box gốc, {matching_info['n_matched']} box khớp, "
                               f"{unmatched_total} box không khớp"
                               + (f", {matching_info['class_changes']} box đổi class" if matching_info['class_changes'] else ""))
            else:
                explanation = "Chưa có tín hiệu (cả ảnh gốc và ảnh lật đều không có box)"
            ctx.evidence.setdefault(u.id, []).extend([
                {"kind": "scalar", "label": "YOLO thiếu ổn định", "value": row["instability"],
                 "observed": row["observed"], "method": "original/flip · IoU/class · không là xác suất lỗi",
                 "matching": matching_info, "explanation": explanation},
                {"kind": "detections", "label": "Prediction ảnh gốc", "boxes": row.get("original", [])},
                {"kind": "detections", "label": "Prediction ảnh lật (đã đổi tọa độ)", "boxes": row.get("flipped", []), "comparison": True}])
        mask = np.array([bool(r.get("observed", False)) for r in rows], bool)
        return Scores(self.name, [u.id for u in units], np.array([r["instability"] for r in rows]), mask=mask)
