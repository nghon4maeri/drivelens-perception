"""Recipe = công thức ghép reference + objectives + combiner + selector (CONTRACTS §7).
MVP: `diversity` (novelty + greedy_nms), `random`, `uniform` (baseline: objectives rỗng, §9.8).
Đăng ký vào registry khi `discover()`; tầng trên đọc bằng `REGISTRY.recipe(name)` (không import file này)."""

from ..core.registry import REGISTRY
from ..core.types import RecipeConfig

_BASE = {"reference": {"type": "session_kept"}, "combiner": {"type": "product_strength"}}

RECIPES = [
    {"name": "diversity", **_BASE,
     "description": "Ưu tiên frame khác tập đã keep (novelty trên DINOv2-S), chọn batch bằng greedy + NMS thời gian để không trùng nhau.",
     "objectives": [{"type": "novelty", "strength": 1.0, "params": {"field": "emb.dinov2_s"}}],
     "selector": {"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1}}},
    {"name": "random", **_BASE, "description": "Baseline: chọn ngẫu nhiên đều trong candidates.",
     "selector": {"type": "random"}},
    {"name": "uniform", **_BASE, "description": "Baseline: rải đều theo thời gian trong candidates, chia K theo tỉ lệ số unit mỗi video.",
     "selector": {"type": "uniform", "params": {"allocation": "proportional"}}},
    {"name": "text_diversity", **_BASE,
     "description": "CLIP lọc theo câu tìm/loại trừ trong config; DINOv2 ưu tiên khác tập đã giữ và chống trùng, không ép đủ đợt.",
     "objectives": [{"type": "text_match", "strength": 1.0},
                    {"type": "novelty", "strength": 1.0, "params": {"field": "emb.dinov2_s"}}],
     "selector": {"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1, "relax": False}}},
]

for _r in RECIPES:
    REGISTRY.add_recipe(RecipeConfig.model_validate(_r))

_REF = {"reference": {"type": "training_kept"}, "combiner": {"type": "weighted_mean"},
        "selector": {"type": "explore_diversity"}}
_NOVELTY = {"type": "reference_novelty", "strength": .6}
REGISTRY.add_recipe(RecipeConfig.model_validate({"name": "reference_diversity", **_REF,
    "description": "Bổ sung so dataset train và tập đã giữ; có mẫu train gần nhất để đối chiếu.",
    "objectives": [{**_NOVELTY, "strength": 1.}]}))
for _name, _text in (("model_guided", False), ("text_model_guided", True)):
    REGISTRY.add_recipe(RecipeConfig.model_validate({"name": _name, **_REF,
        "description": "Dataset train + YOLO original/flip trên tập ứng viên; tín hiệu cần người duyệt, chưa là lỗi/uplift.",
        "features": {"det.yolo_consistency": {"type": "yolo_consistency", "params": {"model_id": "$reference_model"}}},
        "candidate_stage": {"limit": 128, "objectives": [*([{ "type": "text_match", "strength": 1.}] if _text else []),
                {**_NOVELTY, "strength": 1.}], "selector": {"type": "explore_diversity"}},
        "objectives": [_NOVELTY, {"type": "detection_instability", "strength": .4}]}))

REGISTRY.add_recipe(RecipeConfig.model_validate({
    "name": "perception_instability",
    **_BASE,
    "description": "Ưu tiên frame model detection dự đoán thiếu ổn định giữa ảnh gốc và ảnh lật; kết hợp DINOv2 chống trùng lặp.",
    "features": {"det.yolo_consistency": {"type": "yolo_consistency", "params": {"model_id": "$perception_model"}}},
    "candidate_stage": {
        "limit": 48,
        "objectives": [{"type": "novelty", "strength": 1.0, "params": {"field": "emb.dinov2_s"}}],
        "combiner": {"type": "product_strength"},
        "selector": {"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1}},
    },
    "objectives": [
        {"type": "novelty", "strength": 0.6, "params": {"field": "emb.dinov2_s"}},
        {"type": "detection_instability", "strength": 0.4, "params": {"field": "det.yolo_consistency"}},
    ],
    "selector": {"type": "greedy_nms", "params": {"field": "emb.dinov2_s", "nms_s": 1.0, "min_dist": 0.1}},
}))

