# Components — hướng dẫn viết từng kind

> Tài liệu **cấp 3** (dẫn xuất). Signature, kiểu dữ liệu và luồng chỉ định nghĩa ở [CONTRACTS](../../../../design/CONTRACTS.md)
> và [architecture](../../../../architecture_diagram.md); ở đây chỉ trích dẫn. Thấy chỗ nào khác CONTRACTS → CONTRACTS đúng, sửa file này.

Mỗi thư mục ở đây khớp 1–1 với `src/rav/components/<kind>/` và một dòng ở CONTRACTS §5.

| Kind | Câu hỏi | CONTRACTS | Hiện có |
|---|---|---|---|
| [sources](sources/README.md) | Video nào có trong catalog? | §5.1, §13, §19–20 | `local_folder` + upload raw; preparation explicit |
| [decoders](decoders/README.md) | Video → frame nào, lấy pixel thế nào? | §5.2 | `pyav` 4 fps |
| [units](units/README.md) | Thứ được chấm/chọn/duyệt là gì? | §2 `Unit`, §5.3 | `per_frame` |
| [features](features/README.md) | Mỗi unit được mô tả bằng số nào? | §2 `FieldKey`/`FieldTable`, §5.4, §15, §18–20 | DINO/pHash/PCA, `emb.clip_b32`, `det.yolo_consistency`; source/requested cache |
| [references](references/README.md) | So với tập nào? | §5.6, §14.0/14.6, §18 | `session_kept`; tập train + kept cho recipe V2 |
| [objectives](objectives/README.md) | Mỗi unit đáng chọn tới đâu theo một tiêu chí? | §2 `Scores`, §5.7, §9.5, §15/18 | `novelty`, `text_match`, `reference_novelty`, `detection_instability` |
| [combiners](combiners/README.md) | Gộp nhiều điểm thế nào? | §5.8, §18 | `product_strength`, `weighted_mean` |
| [selectors](selectors/README.md) | Chọn tập nào từ điểm gộp? | §5.9, §9.2/9.6/9.8, §15/18 | `greedy_nms`, `random`, `uniform`, `explore_diversity` |
| [feedback](feedback/README.md) | Quyết định keep/drop đổi state thế nào? | §5.10, §12.3 | `keep_drop` |
| [metrics](metrics/README.md) | Tập đã chọn tốt tới đâu? | §5.11, §11.2, §12.5, §9.10 | `nd_rate_at_k`, `keep_rate_by_recipe`, `review_time`, `min_dist`, `temporal_coverage`, `cluster_coverage` |
| [views](views/README.md) | Người cần thấy gì? | §5.14, architecture §7 | 3 view |
| [exporters](exporters/README.md) | Xuất frame đã keep ra đâu? | §5.12, §14.7 | CSV + ảnh local; export snapshot qua service |
| [judges](judges/README.md) | Frame có đáng giữ theo `goal`? | §5.15, §12 | chỉ contract (người làm qua UI) |
| [routers](routers/README.md) | Vòng sau làm gì? | §5.16, §12 | chỉ contract (người làm qua UI) |

**Recipe** ("cách đề xuất") không phải kind: là công thức ghép reference + objective + combiner + selector,
khai báo trong `src/rav/components/recipes.py` (CONTRACTS §7). Storage (`store`, §5.13) nằm ở `src/rav/store/`.
Recipe tìm theo câu và V2 đã có theo CONTRACTS §15/18; dataset reference không trở thành candidate pool.
Pilot multi-user/PII là target §21, chưa phải capability đã implement chỉ vì có spec deployment.

## Quy tắc chung (trích CONTRACTS)

- Thư mục = tên kind + "s" (`unit` → `units/`; `feedback` giữ nguyên). `store` và `execution` là hạ tầng, không phải kind đăng ký
  (cache feature: CONTRACTS §3, code ở `core/execution.py`).
- Công thức và chữ ký **chỉ** ghi ở CONTRACTS; README ở đây mô tả bằng lời và trỏ tới mục tương ứng.
- Implementation là class kế thừa `Component`, có `kind`, `name`, `version`, `Params` Pydantic, docstring bắt buộc (§3, §9.9).
- Đăng ký bằng `@register("<kind>")`; mỗi kind có `base.py` + 1 file / implementation; auto-discover (§3).
- Không gọi chéo, chỉ đọc qua `ctx`; stateless; căn theo `unit_id` (§1).
- Không biết có mấy video; so thời gian chỉ trong cùng `video_id` (§13).
- Đổi logic → đổi `version` (vô hiệu cache theo `FieldKey`, §2).

## Checklist thêm một implementation

- [ ] Đọc mục CONTRACTS của kind → viết class trong `src/rav/components/<kind>/<name>.py`.
- [ ] Docstring: làm gì + tính thế nào (hiện lên UI).
- [ ] Test tối thiểu theo README của kind; `python -m pytest tests -q` + `python -m ruff check src tests` xanh.
- [ ] Objective/selector mới: thêm recipe trong `recipes.py` nếu muốn nó hiện trên UI/benchmark; chạy benchmark, ghi `eval/results/report.md`.
- [ ] Rà [RAV26_ALIGNMENT](../../../../design/RAV26_ALIGNMENT.md) §6: có claim vượt bằng chứng không?
