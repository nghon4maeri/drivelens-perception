# views — dữ liệu để người hiểu và tin đề xuất

Contract: [CONTRACTS §5 dòng 14](../../../../../design/CONTRACTS.md); danh sách view + `ViewSpec` ở [architecture §5.3, §7](../../../../../architecture_diagram.md).

- View chỉ đọc (field qua `ctx.field`, `ctx.state`, `ctx.proposals`), trả **dữ liệu JSON**, không ảnh; FE vẽ theo `view.type`.
- View không gọi objective (không gọi chéo). Điểm objective của một vòng lấy từ `ProposalDTO.scores` (tính lại từ snapshot, CONTRACTS §7).
  `timeline`: field theo tên trong params + vị trí pick / kept / dropped.
- MVP: `embedding_map` (PCA 2D, chỉ để khám phá), `timeline`, `duplicate_pairs` (spot-check ngưỡng pHash).
  Lưới đề xuất không phải view: FE vẽ thẳng từ `ProposalDTO`.

**Thêm implementation:** class `@register("view")` trong `src/rav/components/views/` + một renderer ở `src/web/`.

Chữ ký `build(ctx)`, tham số trong `self.params` (CONTRACTS §5 dòng 14).

**Test tối thiểu:** `get_view` trả `{type, data}` `json.dumps` được.

**Ý tưởng:** `umap2`/`tsne2`, `similarity_heatmap`, `recipe_compare`, `coverage_gaps`.
