# combiners — gộp điểm nhiều objective

Contract: [CONTRACTS §5 dòng 8](../../../../../design/CONTRACTS.md); objective đã chuẩn hoá, combiner không chuẩn hoá lại (§9.1).

- MVP: `product_strength` = tích `score ** strength`; `mask` = AND các mask.
- `objectives` rỗng (baseline) → `combined = 1` cho mọi unit, `mask` toàn `True` (§7); độ dài lấy từ tham số `unit_ids` (quyết định #40).

**Thêm implementation:** class `@register("combiner")` trong `src/rav/components/combiners/`.

**Test tối thiểu:** 2 `Scores` dựng tay → tích đúng; danh sách rỗng → toàn 1.

**Ý tưởng:** tổng có trọng số, rank fusion.
