"""`list_recipes`, `describe_component(kind, name)`: đọc registry + docstring + JSON schema của Params (CONTRACTS §11.2, architecture §5.3 RecipeDTO; #48)."""

from __future__ import annotations

from pydantic import BaseModel, RootModel

from ..core.registry import REGISTRY, discover
from ..core.types import RecipeConfig
from ..service import App, ServiceError
from .base import Command, command
from .videos import Empty


class RecipeDTO(BaseModel):
    name: str
    description: str
    config: RecipeConfig | None                  # bản đầy đủ; client sửa rồi gửi lại trong `propose` (#45)
    params_schema: dict                   # {reference, objectives: [...], combiner, selector} → JSON schema của Params
    components: dict[str, str]            # tên component → docstring (cách tính)
    strategy: dict | None = None
    needs: list[str] = []
    background: bool = False
    requires_training: bool = False
    requires_model: bool = False


def recipe_dto(recipe: RecipeConfig) -> RecipeDTO:
    recipe = recipe.model_copy(deep=True)
    configured = [("reference", recipe.reference), ("combiner", recipe.combiner), ("selector", recipe.selector),
                  *(("objective", o) for o in recipe.objectives), *(("feature", f) for f in recipe.features.values())]
    if recipe.candidate_stage:
        stage = recipe.candidate_stage
        configured += [("combiner", stage.combiner), ("selector", stage.selector),
                       *(("objective", o) for o in stage.objectives)]
    for kind, component in configured:
        component.params = REGISTRY.get(kind, component.type).Params.model_validate(component.params).model_dump(mode="json")
    parts = {"reference": ("reference", recipe.reference), "combiner": ("combiner", recipe.combiner),
             "selector": ("selector", recipe.selector)}
    cls = {role: REGISTRY.get(kind, c.type) for role, (kind, c) in parts.items()}
    objectives = [REGISTRY.get("objective", o.type) for o in recipe.objectives]
    schema = {role: c.Params.model_json_schema() for role, c in cls.items()}
    schema["objectives"] = [c.Params.model_json_schema() for c in objectives]
    stage = recipe.candidate_stage
    additional = [REGISTRY.get("feature", f.type) for f in recipe.features.values()]
    if stage:
        stage_objectives = [REGISTRY.get("objective", o.type) for o in stage.objectives]
        schema["candidate_objectives"] = [c.Params.model_json_schema() for c in stage_objectives]
        additional.extend(stage_objectives)
    training = bool(getattr(cls["reference"], "needs_reference_sets", False))
    strategy = REGISTRY.strategy(recipe.name)
    return RecipeDTO(strategy=strategy.metadata(), needs=list(strategy.needs), name=recipe.name, description=recipe.description, config=recipe, params_schema=schema,
                     background=strategy.background, requires_training=training,
                     requires_model=strategy.model,
                     components={c.name: c.__doc__.strip() for c in [*cls.values(), *objectives, *additional]})


def strategy_dto(name: str) -> RecipeDTO:
    strategy = REGISTRY.strategy(name)
    if name in REGISTRY.recipe_names():
        return recipe_dto(REGISTRY.recipe(name))
    return RecipeDTO(name=name, description=strategy.__doc__ or "", config=None, params_schema={}, components={},
                     strategy=strategy.metadata(), needs=list(strategy.needs), background=strategy.background,
                     requires_model=strategy.model, requires_training=strategy.reference_dataset)


class RecipeList(RootModel[list[RecipeDTO]]):
    pass


@command
class ListRecipes(Command):
    name = "list_recipes"
    description = "Các recipe (cách đề xuất) có sẵn: mô tả, config đầy đủ, schema tham số và cách tính của từng component."
    Input, Output = Empty, RecipeList

    def run(self, inp: Empty, app: App) -> RecipeList:
        discover()
        return RecipeList([strategy_dto(n) for n in REGISTRY.strategy_names()])


class DescribeIn(BaseModel):
    kind: str
    name: str


class ComponentDTO(BaseModel):
    kind: str
    name: str
    version: str
    doc: str
    params_schema: dict


@command
class DescribeComponent(Command):
    name = "describe_component"
    description = "Mô tả một component (vd kind=objective, name=novelty): làm gì, tính thế nào, tham số nào."
    Input, Output = DescribeIn, ComponentDTO

    def run(self, inp: DescribeIn, app: App) -> ComponentDTO:
        discover()
        try:
            return ComponentDTO(**REGISTRY.describe(inp.kind, inp.name))
        except KeyError as e:
            raise ServiceError("component_not_found", str(e.args[0]), 404) from None

