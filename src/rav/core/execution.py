"""Tính và cache feature — hạ tầng, không phải kind (CONTRACTS §3, §5 dòng 5).

Lazy: chỉ tính khi `ctx.field` hỏi tới lần đầu; kết quả lưu đĩa qua `FieldStore`, lần sau đọc lại (mmap).
- `per_sample=True`: đơn vị cache = cả nguồn. Nguồn nào chưa có file thì `compute` trên **mọi unit của các nguồn đó** trong pool
  (một lần), rồi tách và lưu mỗi nguồn một file theo `(FieldKey, (source_id, spec_hash))`; ảnh dùng image_spec_hash.
- `per_sample=False`: `compute` trên cả pool (`ctx.units`), lưu theo `(FieldKey, pool_hash)`.
Trả về FieldTable của các nguồn / pool liên quan; `Context` tự cắt và căn theo `units` được hỏi.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .component import Component, Feature
from .types import FieldTable, Unit

if TYPE_CHECKING:
    from ..store.field_store import FieldStore
    from .context import Context


def spec_hash(decoder: Component, units: Component) -> str:
    """Hash cách tách frame (decoder + unit builder, gồm cả version và Params đã điền mặc định) — CONTRACTS §3."""
    spec = [[c.name, c.version, c.params.model_dump(mode="json")] for c in (decoder, units)]
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def image_spec_hash() -> str:
    """Hash cách đọc ảnh (PIL + EXIF transpose + RGB), độc lập cấu hình video."""
    spec = {"image_reader": {"name": "pil", "version": "1", "exif_transpose": True}, "units": "image"}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def pool_hash(units: list[Unit]) -> str:
    """Hash danh sách `unit_id` theo thứ tự ổn định `Unit.order_key` — CONTRACTS §3, §13.1.5."""
    ids = [u.id for u in sorted(units, key=lambda u: u.order_key)]
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]


class Execution:
    def __init__(self, store: FieldStore, spec_hash: str):
        self.store = store
        self.spec_hash = spec_hash

    def run(self, feature: Feature, units: list[Unit], ctx: Context) -> FieldTable:
        if feature.cache_scope == "requested":
            part = self._requested_part(units, ctx)
            table = self.store.get(feature.key, part)
            if table is None:
                table = _compute(feature, units, ctx)
                if self._requested_part(units, ctx) != part:
                    raise ValueError("Nguồn đã đổi trong lúc tính requested feature")
                self.store.put(table, part)
            return table
        if not feature.per_sample:
            part = pool_hash(ctx.units)
            table = self.store.get(feature.key, part)
            if table is None:
                table = _compute(feature, ctx.units, ctx)
                self.store.put(table, part)
            return table

        parts = self._parts(units)
        tables = {source: self.store.get(feature.key, part) for source, part in parts.items()}
        missing = {source for source, table in tables.items() if table is None}
        if missing:
            known = {u.id: u for u in [*ctx.units, *(u for group in ctx.reference_sets.values() for u in group)]}
            all_units = [u for u in known.values() if u.source_id in missing]
            full = _compute(feature, all_units, ctx)
            for source in missing:
                tables[source] = _subset(full, [i for i, u in enumerate(all_units) if u.source_id == source])
                self.store.put(tables[source], parts[source])
        return _concat([tables[source] for source in parts])

    def _parts(self, units: list[Unit]) -> dict[str, tuple[str, str]]:
        return {u.source_id: (u.source_id, self.spec_hash if u.video_id is not None else image_spec_hash())
                for u in units}

    def peek(self, feature: Feature, units: list[Unit], ctx: Context) -> FieldTable | None:
        """Như `run` nhưng chỉ đọc cache: thiếu phần nào thì None, không tính."""
        if feature.cache_scope == "requested":
            return self.store.get(feature.key, self._requested_part(units, ctx))
        if not feature.per_sample:
            return self.store.get(feature.key, pool_hash(ctx.units))
        tables = [self.store.get(feature.key, part) for part in self._parts(units).values()]
        return None if any(t is None for t in tables) else _concat(tables)

    def _requested_part(self, units, ctx):
        paths = {}
        for u in units:
            if u.video_id is not None:
                paths[u.video_id] = Path(ctx.videos[u.video_id].uri)
            else:
                paths[u.anchor.key] = Path(ctx.images[u.source_id].root) / u.anchor.relpath
        hashes = {}
        for key, path in paths.items():
            digest = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1 << 20), b""):
                    digest.update(chunk)
            hashes[key] = digest.hexdigest()
            if hashes[key][:16] != key:
                raise ValueError("Raw source đã đổi; requested cache không còn hợp lệ")
        models = {}
        for model_id, path in ctx.model_files.items():
            models[model_id] = hashlib.sha256(path.read_bytes()).hexdigest()
            if models[model_id] != model_id.removeprefix("ym-"):
                raise ValueError("Checkpoint đổi; requested cache không còn hợp lệ")
        value = {"ids": [u.id for u in units], "spec": self.spec_hash, "sources": hashes, "models": models}
        return ("requested", hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())


def _compute(feature: Feature, units: list[Unit], ctx: Context) -> FieldTable:
    ctx._start_progress(feature.provides, len(units))
    try:
        table = feature.compute(units, ctx)
    finally:
        ctx._end_progress()
    if table.key != feature.key or table.unit_ids != [u.id for u in units] or len(table.values) != len(units):
        raise ValueError(f"{feature.name}: FieldTable không khớp key / unit_ids được đưa vào")
    return table


def _subset(table: FieldTable, rows: list[int]) -> FieldTable:
    values = np.asarray(table.values)[rows] if table.kind != "structured" else [table.values[i] for i in rows]
    return FieldTable(table.key, [table.unit_ids[i] for i in rows], values, table.kind)


def _concat(tables: list[FieldTable]) -> FieldTable:
    if len(tables) == 1:
        return tables[0]
    first = tables[0]
    ids = [uid for t in tables for uid in t.unit_ids]
    if first.kind == "structured":
        values = [v for t in tables for v in t.values]
    else:
        values = np.concatenate([np.asarray(t.values) for t in tables])
    return FieldTable(first.key, ids, values, first.kind)
