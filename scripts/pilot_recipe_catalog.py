"""Sinh catalog recipe tĩnh cho API pilot từ registry (CONTRACTS §21.15, #95).

Input: registry component + recipe (`components/recipes.py`); output: `src/rav/store/pilot_recipes.json` (RecipeDTO như v1 §11.2
+ `available`, `unavailable_reason`): recipe pilot dùng được, rồi recipe cần dataset train (`available=false`, #97).
API pilot đọc file này để không import registry/numpy; test giữ file khớp registry.
Chạy lại sau khi đổi recipe/component: python scripts/pilot_recipe_catalog.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build() -> list[dict]:
    from src.rav.commands.recipes import recipe_dto
    from src.rav.core.registry import REGISTRY, discover
    discover()
    out = []
    from src.rav.store.pilot_curation import PILOT_RECIPES, TRAINING_RECIPES
    order = list(dict.fromkeys([*PILOT_RECIPES, *TRAINING_RECIPES, *REGISTRY.strategy_names()]))
    for name in order:
        strategy = REGISTRY.strategy(name)
        if name in REGISTRY.recipe_names():
            dto = recipe_dto(REGISTRY.recipe(name)).model_dump(mode="json")
        else:
            dto = {"name": name, "description": strategy.__doc__, "config": None, "params_schema": {}, "components": {},
                   "strategy": strategy.metadata(), "needs": list(strategy.needs), "background": strategy.background,
                   "requires_training": strategy.reference_dataset, "requires_model": strategy.model}
        ok = not strategy.reference_dataset
        out.append({**dto, "available": ok, "unavailable_reason": None if ok else "missing_model_training_provenance"})
    return out


def main() -> int:
    from src.rav.store.pilot_curation import CATALOG

    CATALOG.write_text(json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"đã ghi {CATALOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
