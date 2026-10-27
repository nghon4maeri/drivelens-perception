# features — mô tả unit bằng số

Contract: [CONTRACTS §5 dòng 4](../../../../../design/CONTRACTS.md); `FieldKey`, `FieldTable` ở §2; `per_sample` + cache ở §3; đọc theo pool ở §13.4; CLIP §15, binding/requested cache §18, transport/preparation §19–20.

| Field hiện có | Giá trị theo CONTRACTS | Ghi chú |
|---|---|---|
| `emb.dinov2_s` | vector (N, 384), chuẩn hoá L2, `per_sample=True` | preparation explicit; local/Kaggle và pixel/checkpoint parity theo §20 |
| `hash.phash` | scalar (N,) uint64, `per_sample=True` | **dành cho metric** (độc lập với embedding chọn) |
| `proj.pca2` | vector (N, 2) | `per_sample=False`: PCA fit một lần trên cả pool; `requires` `emb.dinov2_s`; cho view `embedding_map` |
| `emb.clip_b32` | vector (N, 512), float32/L2, `per_sample=True` (§15) | ảnh/text cùng checkpoint + revision; proposal đọc cache ảnh đã prepare (§15/18/20) |
| `det.yolo_consistency` | structured evidence/diagnostics (§18) | model binding cố định, requested subset; không infer toàn pool hoặc dùng prediction làm nhãn thật |

Quy ước tên `<nhóm>.<tên>`: `emb.*` vector chuẩn hoá · `hash.*` · `q.*` scalar [0,1] cao = tốt · `det.*` structured · `score.*` · `proj.*` (N, 2).

- `per_sample=True` khi giá trị của mẫu chỉ tính từ chính nó (`emb.*`, `hash.*`); `False` khi phụ thuộc cả tập (PCA, percentile, clustering). Chỉ ảnh hưởng cache (CONTRACTS §3).
- `requires` một field `per_sample=False` → feature cũng phải `per_sample=False` (CONTRACTS §3, quyết định #33).
- Gom nhóm (vd theo video) viết trong `compute`; cùng thuật toán, cách gom khác = implementation khác, field khác (vd `proj.pca2` vs `proj.pca2_video`).
- Feature không đọc `ctx.state`.
- Đổi logic → đổi `version`: chỉ cache feature đó tính lại (`FieldKey` khác).

**Cache (không phải việc của feature):** `core/execution.py` resolve field qua Context/FieldStore. Quy tắc source cache theo CONTRACTS §3;
requested cache và feature bindings theo §18. Requested subset không làm source thành prepared; proposal CLIP chỉ đọc cache đã prepare (§15/18).

**Thêm implementation:** class `@register("feature")` có `provides` trong `src/rav/components/features/`.

**Thêm biến thể:** tuân theo `name`/`provides`/`Params` và version tại CONTRACTS §3. Recipe có thể bind field tới feature/config qua
`RecipeConfig.features` theo §18; producer/version/params và model được resolve, kiểm rồi đóng băng trong proposal, không tự đổi cache cũ.

**Test tối thiểu:** feature giả `per_sample=True` chỉ được tính cho video chưa có cache, `False` nhận cả pool (CONTRACTS §3); feature giả qua `Context` → `FieldTable` căn đúng `unit_ids`; đổi `version` một feature không mất cache feature khác.
Cache: gọi hai lần → `compute` chạy một lần; vòng `requires` → raise; thêm video vào pool chỉ tính video mới; cùng video khác fps → cache khác;
hai pool khác nhau → cache `per_sample=False` khác; feature `per_sample=True` `requires` field `per_sample=False` → raise.
V2 kiểm source/requested isolation, ordered subset IDs, source/checkpoint đổi và replay không tự infer lại theo CONTRACTS §18.

**Ý tưởng:** `proj.pca2_video`, `q.sharpness`, `q.brightness`, detector disagreement giữa các model theo CONTRACTS §8.
CLIP/text và YOLO consistency đã có ở §15/18; model đa ngôn ngữ hoặc checkpoint mới phải có binding/FieldKey và parity riêng.
