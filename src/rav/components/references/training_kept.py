"""Read named training reference plus session keeps; pool remains video-only (§18)."""
from ...core.registry import register
from .base import Reference


@register("reference")
class TrainingKept(Reference):
    """Dataset train cố định và ảnh đã keep; không dùng dropped làm bằng chứng model đúng/sai."""

    name = "training_kept"
    version = "1"
    needs_reference_sets = True

    def select(self, ctx):
        if not ctx.reference_sets.get("training"):
            raise ValueError("Thiếu dataset train tham chiếu")
        return [*ctx.reference_sets["training"], *(u for u in ctx.units if u.id in ctx.state.kept)]
