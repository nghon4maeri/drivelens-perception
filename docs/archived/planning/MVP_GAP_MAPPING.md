# MVP Gap Mapping — RAV-26 Data Curation Copilot (audit 2026-10-07)

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/backlog/MVP_GAP_MAPPING.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


> **Trạng thái: audit, chưa phải thiết kế.** Không sửa code, schema, dependency hay tài liệu cấp 1–2. Các khuyến nghị dưới đây
> phải được chốt rồi ghi vào `docs/design/CONTRACTS.md` / `architecture_diagram.md` trước khi code (AGENTS.md luật 1–2).
> Nhánh `duy-mvp/v2`, commit `192ff03`; working tree có sẵn thay đổi chưa commit của người dùng (quyết định #63 + `goal_intent_flow.md`), audit này không đụng tới.
>
> **Cập nhật 2026-10-07 (lần 2):** §1–§3 là kết quả audit đã xác minh từ code (giữ nguyên, trừ dòng G3 và L10). §4–§6 viết lại thành
> kế hoạch bốn giai đoạn theo phạm vi người dùng chốt; mã D1–D13 ở §1 tham chiếu bảng quyết định §5. Thiết kế giai đoạn 1:
> [`docs/archived/2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md`](../2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md). Kaggle (§7) là tuỳ chọn, không chặn giai đoạn nào.

## 0. Cách khảo sát và giới hạn

**Đã đọc:** `AGENTS.md`, `PLAN.md`, `README.md`, `docs/README.md`, `PRODUCT_STORY.md`, `architecture_diagram.md`, `design/CONTRACTS.md`,
`design/RAV26_ALIGNMENT.md`, `design/LOCAL_SINGLE_USER_LIMITS.md`, `gate-1/brief.md`, `research-pack/01_origin/*`,
`research-pack/02_problem_understanding/04_data_curation_focus.md` (gọi tắt "focus"), log quyết định #37–#63, `backlog/*`, `eval/results/report.md`;
toàn bộ `src/rav/` (core, components, pipeline, service, commands, api, cli, store) và các màn chính của `src/web/src/`.

**Kiểm tra read-only đã chạy:**

| Kiểm tra | Kết quả | Giới hạn |
|---|---|---|
| `python -m pytest tests -q` | **71 pass, 1 fail** — `test_codex_hook.py` (lỗi đường dẫn có dấu đã biết, AGENTS §3) | Test dùng video tổng hợp sinh bằng PyAV và **field giả** ghi sẵn vào cache (`tests/test_p5a.py` docstring): không chạy DINOv2 thật, không đo chất lượng chọn |
| `python -m ruff check src tests` | Sạch | – |
| `npx tsc --noEmit` (src/web) | Không lỗi | Chỉ kiểm kiểu, không chạy UI |
| Đọc thống kê `output/rav.sqlite` (mở chế độ `ro`, chỉ đếm) | 14 phiên, 610 quyết định, 62 proposal (60 `diversity`, 2 `random`); actor duy nhất `human:local` | Là dấu vết dùng thử local, không phải phép đo; không đọc nội dung goal/quyết định |
| `eval/results/report.md` §7 | Benchmark chính thức ND-Rate@K trên 1 video 5 phút (1.200 unit) | Không chạy lại trong audit này |

**Không làm:** không chạy app/browser, không chạy lại benchmark/P1 (tốn CPU/RAM), không đọc `.env`/credentials.
Kết luận "đã có và kiểm chứng" nghĩa là có code **và** có test hoặc kết quả chạy được ghi lại; "có một phần" = có code nhưng thiếu phần yêu cầu
hoặc chưa kiểm chứng phần đó.

Ký hiệu trạng thái: ✅ **đã có và kiểm chứng** · 🟡 **có một phần** · 📐 **chỉ có trong thiết kế** · ❌ **chưa có** · ❓ **chưa xác minh**

---

## 1. Bảng mapping chính

### A. Quản lý dữ liệu

| ID | Requirement / use case | Trạng thái | Evidence code | Gap / lệch | Dependency / quyết định cần chốt |
|---|---|---|---|---|---|
| A1 | Import video | ✅ | `api/upload.py` (multipart theo khúc), command `upload_video` (`commands/videos.py`), `service/video.py::upload_video` (giới hạn dung lượng, mở thử decoder, trùng nội dung → trả bản cũ); `sources/local_folder.py` quét thư mục; test `test_p6.py::test_upload_and_media_range` | Chỉ mp4/mov/mkv/avi/webm; mọi video chung một thư mục (LOCAL_LIMITS #6); không xoá/đổi tên video | – |
| A2 | Tách frame | ✅ | `decoders/pyav.py` 4 fps, serve theo yêu cầu, không ghi ảnh (#37); `units/per_frame.py`; P1 `pipeline/prepare.py` | fps cố định trong `DEFAULT_SPEC`, không chọn theo phiên | – |
| A3 | Import ảnh (thư mục ảnh / dataset ảnh) | ❌ | `local_folder.VIDEO_EXTS` chỉ có đuôi video; `Unit`, `VideoRef`, `PoolFilter` đều xoay quanh `video_id` (CONTRACTS §2, §13.3) | Không nạp được dataset ảnh (vd BDD100K images, dataset tham chiếu cũ) | **D1** cách biểu diễn nguồn ảnh |
| A4 | ID ổn định | ✅ | `video_id` = 16 hex sha256 nội dung (`local_folder._probe`); `unit_id = "{video_id}:{t0:.3f}-{t1:.3f}"` (`core/types.py::unit_id`); cache theo `spec_hash` (`core/execution.py`) | Ổn định chỉ khi giữ nguyên decoder/fps; đổi fps → unit id khác (đúng thiết kế §3) | – |
| A5 | Nguồn gốc (provenance) | 🟡 | `VideoRef.meta` (tên file, n_frames, kích thước, size, mtime); `Unit` có `video_id`, `t0`, `anchor.idx`; proposal lưu snapshot input (`service/proposal.py`) | Catalog = quét thư mục, **không lưu trong DB**: xoá/đổi chỗ file → phiên lỗi `video_not_found` (`service/session.py::make_context`). Export CSV không có tên file nguồn, recipe, proposal, người quyết định | D2 (manifest) |
| A6 | Annotation | ❌ (chỉ hợp đồng hiển thị 📐) | CONTRACTS §8.0 quy ước `det.<model>` / `tag.<nhóm>`; `service/evidence.py` chỉ đọc cache; không feature nào sinh `det.*`/`tag.*` (`components/features/`: dinov2_s, phash, pca2, kmeans) | Không import / lưu / chuyển tiếp annotation có sẵn; focus §4B cấm dùng nhãn ẩn làm bằng chứng | D1, D8 |
| A7 | Trạng thái xử lý | 🟡 | `queue_prepare`, `prepare_jobs` + `VideoDTO.prepare` (#56); `is_prepared` kiểm cache; test `test_p8e.py` | Trạng thái job chỉ trong RAM (LOCAL_LIMITS #15); "đã chuẩn bị" suy từ cache, không có bản ghi | – |
| A8 | Embedding cache | ✅ | `store/field_store.py`, `core/execution.py`: `per_sample=True` cache theo `(FieldKey, video_id, spec_hash)`, `False` theo `pool_hash`; test `test_p1.py` | Chỉ DINOv2-S, pHash, PCA2, k-means; chưa có embedding ảnh–text | D3 |
| A9 | **Dataset tham chiếu cũ** | ❌ | Kind `reference` chỉ có `session_kept` (`references/session_kept.py`); RAV26_ALIGNMENT A7 chỉ "chừa chỗ" | Không có thực thể dataset, không chọn được, novelty không so với dữ liệu cũ | **D1** |
| A10 | Pool tìm kiếm mới | ✅ | `PoolFilter.video_ids` lưu trong session spec; `create_session`, `add_videos_to_session` (chỉ thêm, #56); test `test_p5a.py`, `test_p8e.py` | Lọc chỉ theo video; chưa lọc theo thời gian / tag / loại trừ | – |
| A11 | Tập frame đang xây | ✅ | `ReviewState` = replay log `decision` chỉ ghi thêm (`service/review_state.py`, `store/db.py`); chặn vượt `target_k` (422 `target_k_reached`, test `test_p5a.py`, `test_p6.py`) | – | – |
| A12 | Snapshot danh sách cuối (cho so sánh / report / export) | ❌ | Không có bảng/command snapshot. `export` chép tập kept **tại thời điểm bấm** vào thư mục mới (`service/export.py`), `meta.json` chỉ có id, format, n_frames, created_at | Không có id phiên bản danh sách, không gắn với `decision.seq`, không biết export nào ứng với trạng thái duyệt nào | **D2** |

### B. Phiên và mục tiêu curation

| ID | Requirement / use case | Trạng thái | Evidence code | Gap / lệch | Dependency / quyết định |
|---|---|---|---|---|---|
| B1 | Chọn pool | ✅ | `NewSessionScreen.tsx` (chọn nhiều video Sẵn sàng), `POST /sessions` | – | – |
| B2 | Chọn dataset tham chiếu | ❌ | – | Phụ thuộc A9 | D1 |
| B3 | Ngân sách | ✅ | `target_k` trong spec; FE preset 50/100/200; BE chặn vượt | `batch_size` mặc định 16 cố định ở FE | – |
| B4 | Lưu mục tiêu | 🟡 | `spec.goal` (câu tự do) lưu cùng phiên, chép vào mọi `Decision.goal` (`review_state.update_state`) | **Thuật toán không đọc goal**: mọi objective `uses_goal = False` (`objectives/base.py`, `novelty.py`); `explain_proposal` báo "Recipe này chưa dùng mục tiêu phiên" | D4 |
| B5 | Agent làm rõ yêu cầu → tiêu chí có cấu trúc, người kiểm/sửa | 📐 (nháp) | `docs/archived/2026-10-13/docs/backlog/goal_intent_flow.md` + quyết định #63 — **chưa commit**, chưa vào CONTRACTS; không có code LLM nào (grep `llm/openai/anthropic` trong `src/` = 0) | Toàn bộ luồng chưa có; còn mở schema structure, gate vs điểm mềm, `tag.*`, detector | **D4** |
| B6 | Không thêm 3 chế độ UI coverage / weakness / lớp mới | ✅ (không có) | Không có chế độ nào; mẫu goal là 3 nút điền chữ | – | – |
| B7 | ODD: người dùng xác nhận điều kiện; tách metadata quan sát được / ước lượng / chưa kiểm chứng | ❌ | Chỉ có hợp đồng `tag.*` (§8.0) và telemetry sidecar `<video>.telemetry.json` (`local_folder.telemetry`, test `test_p8h.py`); `data/videos/` không có file telemetry nào | Không có nguồn điều kiện thật; không có trường phân loại mức tin cậy của điều kiện | D4, D7 (nguồn metadata) |
| B8 | (Lệch) mẫu goal trên UI hứa điều hệ thống không làm | 🟡 lệch | `NewSessionScreen.tsx::GOALS` có "Người đi bộ ban đêm…", "Xe máy cắt ngang…" | Người dùng chọn mẫu này vẫn nhận đề xuất novelty thuần → kỳ vọng sai. Nên ẩn/ghi rõ cho tới khi có relevance | Có thể sửa ngay (UI) sau khi người dùng đồng ý |

### C. Tìm kiếm và proposal

| ID | Requirement / use case | Trạng thái | Evidence code | Gap / lệch | Dependency / quyết định |
|---|---|---|---|---|---|
| C1 | Truy vấn bằng text | ❌ (📐 "Hướng tới" #38) | CONTRACTS §8 `emb.clip_b32` + objective `text_match`; DINOv2 không có nhánh text | Cần model ảnh–text (CLIP/SigLIP), tính lại embedding mọi video (CPU) | **D3** |
| C2 | Truy vấn bằng ảnh / more-like-this | ❌ (📐 §8) | Không có command tìm theo ảnh; `Decision.extra` chừa chỗ more/less-like-this | Có thể làm trên DINOv2 sẵn có (cos tới ảnh mẫu) mà không cần model mới | D3 |
| C3 | Ranking | ✅ | `pipeline/propose.py`: candidates = pool − kept − dropped → objectives → `product_strength` → selector; recipe `diversity` (novelty + `greedy_nms`), baseline `random`, `uniform` (`components/recipes.py`); test `test_p2.py` | Ranking chỉ theo "khác biệt", không theo phù hợp mục tiêu | D3, D4 |
| C4 | Batch size, seed, tái lập | ✅ | `ProposeIn.batch_size` (mặc định 16), `seed` sinh nếu trống và lưu snapshot; `return_scores` dựng lại ctx từ snapshot (test `test_p5b.py::test_return_scores_match_proposal`) | – | – |
| C5 | Keep/drop + lịch sử | ✅ | `PATCH /state` (keep/drop/clear), log chỉ ghi thêm có `proposal_n`, `shown_at`; FE lưu ngay + thử lại + hoàn tác (#61); màn "Mẫu đã lưu" (`ArchiveScreen.tsx`) | `actor` cố định `human:local` | D6 |
| C6 | Relevance với mục tiêu | ❌ | Không objective nào đọc goal | – | D3, D4 |
| C7 | Trùng trong batch | ✅ | `greedy_nms`: NMS thời gian cùng video (`nms_s` 1 s) + `1 − cos ≥ min_dist` (0,1) trên DINOv2 | Khi thiếu thì **nới tới ×0 = lấy top theo combined**, có thể đưa frame gần trùng vào batch (`relaxed=True`) — mâu thuẫn nguyên tắc #63 §6.7 (đang chờ chốt, §7.5) | D11 |
| C8 | Trùng với tập đã keep | 🟡 | `novelty` = (1 − max cos tới kept)/2 — **điểm mềm**, không phải cổng lọc; `explain_proposal.nearest_kept` báo cos tới frame đã giữ gần nhất (test `test_p8i.py`) | Không có ngưỡng "trùng"; frame giống tập kept vẫn lên nếu pool hết frame khác | D11 |
| C9 | Trùng / bổ sung so với dataset tham chiếu | ❌ | – | Cần A9 + reference mới (vd `dataset` hoặc `kept_plus_dataset`) | D1 |
| C10 | Contract cho thêm evidence mà giữ FE/BE chung | ✅ (cơ chế) | Objective plug-in + `Pick.breakdown` (tên objective → điểm); `Scores.mask` cho cổng cứng; `Proposal.fields_used` + `independent` (#44); `get_evidence` + FE `Detections.tsx` hiện/ẩn theo dữ liệu; command layer sinh route (`api/routes.py::ROUTES`) | Thêm objective mới không cần sửa FE/API; nhưng **evidence cấp tập** (cho report) chưa có hình dạng chung | D5 |
| C11 | (Lệch) người dùng không phải chọn thuật toán trên UI | 🟡 lệch nhẹ | `RecipePanel.tsx` trong khối "Nâng cao" (gập) cho chọn `diversity/random/uniform` + sửa params/JSON; `NewSessionScreen` dùng `(await api.recipes())[0]` (phụ thuộc thứ tự đăng ký) | Không bắt buộc chọn, nhưng baseline vẫn là lựa chọn trong luồng duyệt thật; recipe mặc định chọn theo thứ tự list | D10 |

### D. So sánh và benchmark

**Kiểm kê metric thật sự đã tính** (tất cả qua `pipeline/evaluate.py`, trả `independent` theo #44):

| Metric (code) | Định nghĩa hiện có | Tập tham chiếu | Nguồn dữ liệu | Ngưỡng | Tái lập | Giới hạn |
|---|---|---|---|---|---|---|
| `nd_rate_at_k` (`metrics/nd_rate.py`) | Tỉ lệ unit trong tập có ≥ 1 unit khác **trong chính tập** cách Hamming pHash ≤ ngưỡng | Chính tập được chấm | `hash.phash` (độc lập với DINOv2) | 8 (#51; spot-check 44 cặp, 1 video mưa) | Tất định | Không đo trùng với tập cũ; ngưỡng chỉ hiệu chỉnh trên 1 video |
| `min_dist` | Trung bình 1 − max cos DINOv2 tới unit khác trong tập; `/min` = cặp gần nhất | Chính tập | `emb.dinov2_s` | – | Tất định | **Không độc lập** với recipe diversity (P3 gắn cờ) |
| `cluster_coverage` | Số cụm k-means (k=10, seed 0) có unit của tập / k; entropy chuẩn hoá | Pool của phiên | `cluster.kmeans` trên DINOv2 cả pool | k=10 tuỳ ý | Tất định (seed cố định) | Cụm **không mang nghĩa ODD**; đọc cùng embedding với recipe |
| `temporal_coverage` | Tỉ lệ khoảng `bin_s`=10 s có unit của tập; `by_video/*` | Pool | `t0` | 10 s | Tất định | Là phủ thời gian, không phải phủ điều kiện |
| `keep_rate_by_recipe` | kept / (kept + dropped) theo recipe của proposal | Mọi quyết định của phiên | Log quyết định | – | Theo log | Không chứng minh chất lượng (focus §4A) |
| `review_time` | Trung vị (quyết định cuối − `shown_at`) / số quyết định mỗi đợt | Log phiên | Log | bỏ đợt > 1800 s | Theo log | Chưa tách thời gian xem clip / chờ hệ thống |
| `explain_proposal.nearest_kept` (`service/explain.py`) | cos tới frame **đã giữ lúc đề xuất** gần nhất, từng pick | Tập kept tại snapshot proposal | Embedding recipe đã dùng | – | Tất định | Theo đợt, không có phân bố / cho tập cuối |
| `explain_proposal.spread` | Số pick / khoảng thời gian theo video | Đợt | `t0` | – | Tất định | Theo đợt |

| ID | Evidence mong muốn | Trạng thái | Evidence code | Gap / lệch | Dependency |
|---|---|---|---|---|---|
| D1 | Relevance với truy vấn | ❌ | – | Cần C1/C2 | D3 |
| D2 | Redundancy trong tập mới | ✅ | `nd_rate_at_k`, `min_dist`; benchmark `eval/results/report.md` §7 | – | – |
| D3 | Redundancy với tập cũ | ❌ | – | Cần A9; metric mới đọc reference | D1 |
| D4 | Phân bố khoảng cách tới mẫu gần nhất trong dataset tham chiếu | ❌ | Chỉ có `nearest_kept` theo đợt, so với kept của phiên | Cần A9 + metric trả histogram/quantile | D1 |
| D5 | Coverage trong embedding space | 🟡 | `cluster_coverage`, view `embedding_map` (PCA2) | Mô tả, đọc cùng embedding chọn → không độc lập; không phải ODD coverage | – |
| D6 | Phân bố nguồn / video / sequence | 🟡 | Khoá `by_video/*` của `nd_rate_at_k`, `temporal_coverage`; `explain.spread`; view `timeline` | Không có metric "tỉ lệ frame theo video/nguồn" cho tập cuối; không có khái niệm sequence/chuyến đi | – |
| D7 | So với random cùng pool và ngân sách | 🟡 | `run_benchmark` (CLI, `commands/benchmark.py`): recipe × seed × K, **state rỗng, chọn một lần K** | Không so được **tập đã duyệt của phiên** với random cùng pool + cùng số frame; benchmark không mô phỏng vòng lặp keep/drop; chỉ chạy CLI | GĐ3 (3.4) |
| D8 | Benchmark proposal trước annotation | ✅ (hẹp) | `eval/results/report.md` §7.2: 1 video, 1.200 unit, K 50/200, 5 seed: diversity < random rõ; **K=200 không hơn uniform** (12,0% vs 9,5% @8) | Chỉ 1 video, 1 metric; action item §8 còn mở | – |
| D9 | Benchmark uplift sau train (mô phỏng) | ❌ | Không có script train/eval trong `main`/`duy-mvp/v2`; backlog B3; **số mAP 0,38→0,45 ở `nam-init` là giả** (`branch_review_2026-10-03.md`) | Thiếu: dataset có nhãn (BDD100K), split train/pool/val/test, export kèm nhãn (oracle), script train YOLO nhiều seed, so Random cùng ngân sách (focus §4B) | **D9**, D1, D8 |

### E. Agent và report

| ID | Requirement | Trạng thái | Evidence code | Gap / lệch | Dependency |
|---|---|---|---|---|---|
| E1 | LLM integration | ❌ | Không có client LLM, không có dependency (`requirements.txt`) | – | D4 (provider, khoá, dữ liệu gửi đi) |
| E2 | Tools cho agent | 🟡 (nền) | Command layer: `Command.Input/Output` Pydantic → JSON schema, `mutates`, `needs_confirm` (`commands/base.py`); 22 route sinh từ `COMMANDS` | Chưa có bộ sinh tool schema, chưa có event log/confirm §11.3 (LOCAL_LIMITS #12); `ui_focus` (§11.2) chưa có | – |
| E3 | Context + lịch sử hội thoại | ❌ | – | Không có bảng chat | D4 |
| E4 | Gói evidence có cấu trúc (metric, giá trị, đơn vị, tập tham chiếu, phương pháp, phiên bản, giới hạn) | 🟡 | `get_metrics` trả `{type, params, values}` + `independent`; `explain_proposal` có `similarity_field`, `field_used_by_recipe`; docstring component qua `describe_component` | Thiếu: đơn vị, tập tham chiếu tường minh, `Metric.version` không trả ra, giới hạn, id snapshot; không có baseline trong cùng gói | **D5** (hình dạng), GĐ3 (3.1) |
| E5 | Report trả lời 5 câu (muốn gì / chọn gì / bổ sung gì so với cũ / căn cứ so baseline + ngân sách / chưa kết luận được) | ❌ | Gần nhất: `BatchExplain.tsx` (theo đợt), `AnalysisScreen.tsx` (ghi rõ "chất lượng quyết định: chưa đo", "uplift: ngoài hệ thống") | Câu 3 cần A9; câu 4 cần D7; câu 1 cần B5 nếu muốn có cấu trúc | D1, D2, D5 |
| E6 | Report gắn snapshot; biết report cũ khi tập bị sửa | ❌ | – | Cần A12. Mẫu có sẵn để học: proposal lưu `kept_ids` lúc đề xuất | D2 |
| E7 | Biểu đồ / số liệu lấy từ evidence, không do LLM | 🟡 (nguyên tắc có) | View trả `ViewSpec` dữ liệu, FE vẽ theo `type` (`renderers/`); #61 "chỉ dữ liệu thật" | Chưa có report nên chưa áp dụng | D5 |

### F. Duyệt và bàn giao

| ID | Requirement | Trạng thái | Evidence code | Gap / lệch | Dependency |
|---|---|---|---|---|---|
| F1 | Keep/drop từng frame | ✅ | C5 | – | – |
| F2 | Duyệt danh sách cuối trước khi chuyển tiếp | ❌ | Không có trạng thái "đã duyệt", không có command approve. RAV26_ALIGNMENT G5 ✅ chỉ đúng với keep/drop từng frame | Hai việc khác nhau đang bị gộp trong tài liệu alignment | **D2**, D6 |
| F3 | Lưu người duyệt | ❌ | `ACTOR = "human:local"` (`service/review_state.py`, LOCAL_LIMITS #2) | – | D6 |
| F4 | Phiên bản đã duyệt | ❌ | – | Cần A12 | D2 |
| F5 | Export ảnh + manifest + report đồng nhất | 🟡 | `csv_images` (`exporters/csv_images.py`): `export.csv` (unit_id, video_id, t0, t1, frame_idx, file) + `images/`; zip; giữ mọi lần (test `test_p5b.py`) | CSV thiếu nguồn file, goal, recipe/config_hash, proposal_n, actor, decided_at, version component; **không có report**; export lấy kept hiện tại, không ràng buộc với bản đã duyệt | D2, D5 |

### G. Ràng buộc đề bài

| ID | Requirement | Trạng thái | Evidence | Gap / lệch | Dependency |
|---|---|---|---|---|---|
| G1 | Dữ liệu recorded / synthetic | 🟡 | `data/videos/`: video dashcam YouTube + `commaai-sample.mp4` (recorded); benchmark trên 1 video 5 phút | Không có synthetic; không có BDD100K trong `data/`; focus §1 nói "có dataset đã gán nhãn + checkpoint" nhưng repo không có | D9 |
| G2 | PII | ❌ | Không có blur/ẩn mặt-biển số; không có ghi nhận ranh giới | Hiện chưa gửi dữ liệu ra ngoài (không LLM), nhưng B5/E1 sẽ gửi goal và có thể gửi frame | **D7** |
| G3 | Ít nhất hai vai trò, quyền duyệt | ❌ | Không có user/role; lộ trình Owner·Curator·Viewer ở LOCAL_LIMITS "Lát 1". PRD v2.1 ghi non-goal "nhiều tài khoản/role" | **Là yêu cầu đề RAV-26** (người dùng xác nhận 2026-10-07); `research-pack/01_origin/*` thiếu câu này → bổ sung khi cập nhật docs | Q3 (§5.3), GĐ4 |
| G4 | Thiên lệch chọn mẫu dễ / một nhóm điều kiện | 🟡 rủi ro ghi nhận | ALIGNMENT A9 (novelty ưu tiên nhiễu: gạt nước, lóa, mờ); vòng đầu so với tâm pool → ưu tiên frame dị biệt (report §8 nghi chọn frame tiêu đề đầu video); objective `threshold` theo độ nét chỉ có trong ví dụ CONTRACTS §7 (📐, không có code) | Không có cổng chất lượng; không có phân tầng theo điều kiện; dữ liệu thử chủ yếu một cảnh mưa | D4, D11 |
| G5 | Requirement gốc vs phạm vi MVP được ghi rõ | 🟡 | ALIGNMENT §1–4 ghi ⏳/🟡 cho mAP, coverage, AL | ALIGNMENT §3 G5 ✅ và U4 ✅ đang rộng hơn thực tế (xem §2 dưới) | – |

### H. Model — yêu cầu sản phẩm làm rõ ở #69; cơ chế triển khai còn mở

**YOLO tham chiếu** trong yêu cầu người dùng (#69) = checkpoint hiện có cần cải thiện, đã train/finetune trên **dataset tham chiếu** và theo người dùng đang perform kém trên pool mới chưa gán nhãn. Nạp/chọn checkpoint tương ứng là yêu cầu của luồng; cơ chế/schema/API/inference chưa thiết kế. Teacher lớn hơn để pseudo-label/disagreement là một hướng mở rộng riêng (§8), không phải nghĩa của “YOLO tham chiếu” ở đây. Hiện code **không có model detection nào**; vòng label → train → đánh giá vẫn cần để chứng minh cải thiện. Bảng phương án dưới là khảo sát cũ, không tự chốt teacher/disagreement hay phạm vi triển khai.

| Phương án | Có sẵn | Cần thêm (tối thiểu) | Dependency | Chi phí tương đối | Rủi ro |
|---|---|---|---|---|---|
| H1. Chỉ đăng ký checkpoint / thông tin model | Không có gì | Bảng/JSON model card (tên, phiên bản, lớp, dữ liệu train nếu biết) gắn phiên; hiện ở ngữ cảnh phiên (focus §6 "FR còn thiếu" 1) | Không | **S** | Không tạo evidence nào; dễ bị hiểu nhầm là "hệ thống hiểu model" |
| H2. Import evaluation / prediction có sẵn | Hợp đồng `det.<model>` (§8.0), `get_evidence` đọc cache, FE vẽ box + phím M (`Detections.tsx`), test đọc-cache `test_p8h.py` | Importer ghi prediction (COCO results / YOLO txt) vào `FieldStore` đúng `FieldKey` + `(video_id, spec_hash)`; khớp ảnh ↔ unit; metric lỗi theo ảnh cần GT (focus §4C) | Không cần torch thêm | **M** | Khớp frame/timestamp; GT bị cấm dùng làm bằng chứng trong phiên giả lập |
| H3. Chạy target YOLO trên proposal | Không có | Feature `det.yolo*` + objective `disagreement` (backlog B1) | `ultralytics` (cần kiểm license — AGPL-3.0 theo hiểu biết hiện tại), weights | **L** | **Xung đột cache:** `per_sample=True` cache theo **cả video** (CONTRACTS §3) → không có "chỉ chạy trên 16 frame"; hoặc chạy cả video trong P1 (CPU, máy RAM thấp) hoặc phải đổi contract cache. Nguyên tắc B5 "lỗi thì dừng, không sinh điểm giả" |

---

## 2. Tài liệu và code lệch nhau

| # | Tài liệu nói | Code / dữ liệu thực tế |
|---|---|---|
| L1 | CONTRACTS §7 ví dụ recipe `diversity` có objective `threshold` trên `q.sharpness` | Không có objective `threshold`, không có feature `q.*`; recipe thật chỉ có `novelty` (`components/recipes.py`) |
| L2 | CONTRACTS §11.2 liệt kê `ui_focus` | Không có command `ui_focus` (`explain_pick` thì đã ghi ⏳) |
| L3 | RAV26_ALIGNMENT G5 ✅ "Approve/reject + tạo labeling batch" | Chỉ có keep/drop từng frame; không có duyệt danh sách cuối (F2) |
| L4 | PRODUCT_STORY §3 "Minh chọn chiến lược novelty và bấm Đề xuất"; §6 "dữ liệu hiện có là mock, kiểm thử trên 1 video" | UI tự đề xuất đợt đầu bằng `recipes[0]`, recipe nằm trong "Nâng cao"; `data/` có 6 video, benchmark chính thức trên video 5 phút; pool nhiều video đã có (#56) |
| L5 | PLAN "Mục tiêu MVP: nạp 1 video" | Code hỗ trợ nhiều video / phiên |
| L6 | Focus §1 "có dataset đã gán nhãn, model/checkpoint, khả năng train lại" | Không có trong repo / `data/`; không có script train |
| L7 | Quyết định #63 và `goal_intent_flow.md` | **Chưa commit**; chưa vào CONTRACTS; không có code |
| L8 | `eval/results/report.md` §1–4 | Vẫn là template BTC chưa điền |
| L9 | Mẫu goal trên UI ("Người đi bộ ban đêm", "Xe máy cắt ngang") | Không objective nào đọc goal (B8) |
| L10 | Focus §6 viện "đề gốc" về PII + hai vai trò | Đúng là yêu cầu đề RAV-26 (người dùng xác nhận); `research-pack/01_origin/*` chưa ghi → thiếu tài liệu, không phải thiếu requirement |

---

## 3. Mapping luồng người dùng

Luồng mong muốn: **Agent nhận yêu cầu → người dùng tìm và chọn frame → agent tạo report → kỹ sư duyệt và xuất dataset cùng report.**

| Bước | Mong muốn | Hiện chạy được tới đâu | Dừng ở đâu / vì sao |
|---|---|---|---|
| 0 | Nạp dữ liệu: pool mới + dataset tham chiếu cũ | ✅ Upload video → P1 nền có tiến độ (`VideosScreen`, `queue_prepare`) | ❌ Không nạp được dataset ảnh / dataset tham chiếu (A3, A9) |
| 1 | Agent nhận yêu cầu tự do | 🟡 Ô "Mục tiêu" trên `NewSessionScreen`, lưu nguyên câu | ❌ Không có agent; câu không được phân tích (B4, B5, E1) |
| 2 | Agent làm rõ thành tiêu chí có cấu trúc; người kiểm/sửa; xác nhận ODD | – | ❌ Chỉ có bản nháp #63 (B5, B7) |
| 3 | Chọn pool, tham chiếu, ngân sách | ✅ pool + K (`POST /sessions`) | ❌ Không chọn được tham chiếu (B2) |
| 4 | Tìm frame theo tiêu chí (text / ảnh) | 🟡 Hệ thống tự đề xuất 16 frame bằng novelty + NMS | ❌ Không truy vấn được; ranking không theo mục tiêu (C1, C2, C6) |
| 5 | Chọn frame: keep/drop, xem ngữ cảnh, đổi đợt | ✅ Studio: lưu ngay, hoàn tác, clip ±3 s (`VideoContextLightbox`), "Đợt mới", giải thích đợt "?" (`explain_proposal`), chặn vượt K. Đã được dùng thử local (14 phiên / 610 quyết định) | Giải thích chỉ nói "khác tập đã giữ", không nói "phù hợp mục tiêu" |
| 6 | Agent tạo report cho tập vừa chọn | 🟡 Màn Phân tích: số mô tả tập kept + keep rate + tốc độ duyệt; ghi rõ chất lượng/uplift chưa đo | ❌ Không có report, không có gói evidence, không so baseline cho tập của phiên, không so dataset cũ (E4–E6, D3, D7) |
| 7 | Kỹ sư duyệt danh sách cuối | – | ❌ Không có snapshot / approve / người duyệt (A12, F2–F4) |
| 8 | Xuất dataset + report | 🟡 Zip ảnh + CSV, giữ mọi lần (`ExportScreen`) | ❌ Không manifest provenance, không report, không gắn bản đã duyệt (F5) |

**Tóm lại:** luồng chạy thật được là **nạp video → tạo phiên → vòng đề xuất/keep-drop → xuất zip**. Ba đoạn đầu–cuối của luồng mong muốn
(agent nhận yêu cầu, report, duyệt danh sách cuối) và trục "so với dataset cũ" chưa có.

---

## 4. Kế hoạch hoàn thiện MVP — bốn giai đoạn (cập nhật 2026-10-07, thay §4–§6 bản audit đầu)

Phạm vi do người dùng chốt (2026-10-07): luồng **agent nhận yêu cầu → người dùng tìm và chọn frame → agent sinh report → kỹ sư duyệt và export
dataset kèm report**; quản lý dataset tham chiếu, pool mới và collection; một FE/BE chung, recipe/thuật toán chọn trong config; LLM diễn giải yêu cầu và
viết report từ số liệu backend tính. **Ngoài luồng MVP:** training, perception inference, MLflow, DVC. Model tham chiếu / imported predictions,
Kaggle GPU, quantization là hướng mở rộng tuỳ chọn, không chặn bốn giai đoạn.

Cách đọc bảng §4.2:

- Cột **Hiện đã có** = **[XM] đã xác minh từ code** (file/symbol ở §1). Không có gì → "–".
- Cột **Thay đổi cần làm** = **[ĐX] đề xuất thiết kế** (chưa chốt, chưa vào CONTRACTS).
- Cột **Dependency** ghi mã quyết định; **[MỞ]** = còn chờ người dùng (§5.3), còn lại là đã chốt hoặc chi tiết kỹ thuật đã tự chọn (§5.2).

### 4.1. Sửa các điểm thiết kế của bản audit đầu

| Điểm bản đầu | Sửa thành | Lý do |
|---|---|---|
| D1(b): ảnh độc lập thành `Unit(kind="frame")` với `video_id` = dataset, `t0` = chỉ số | Ảnh là **loại nguồn riêng** (`image_set`), `Unit(kind="image")`, `t0/t1 = None`; temporal/context chỉ áp dụng cho nguồn video | Không giả lập timestamp; NMS thời gian, `uniform`, `temporal_coverage`, clip ±3 s sẽ cho kết quả sai với timestamp giả |
| D2 / G1: "`SCHEMA_VERSION` tăng, xoá DB local" | Migration giữ dữ liệu + sao lưu file DB trước khi nâng | DB local đang có 14 phiên, 610 quyết định; LOCAL_LIMITS #14 phải đổi theo |
| Snapshot "hash đổi khi kept đổi" | Snapshot **bất biến**; collection đổi thì tạo snapshot mới, snapshot cũ thành "không còn hiện hành" | Report/export phải trỏ tới một bộ dữ liệu xác định |
| D11: giữ nới ngưỡng ×0 như tuỳ chọn | **Bỏ** nới tới 0: không đủ mẫu đạt thì trả batch nhỏ hơn + lý do | Nguyên tắc người dùng chốt; khớp #63 §6.7 |
| D7(a): "dữ liệu công khai" = đã ẩn danh | Không coi là bằng chứng; cần cơ chế PII ở giai đoạn 4 | Yêu cầu đề RAV-26 |
| G3 / L10: hai vai trò + PII "chưa xác minh nguồn" | **Là yêu cầu của đề RAV-26** (người dùng xác nhận); tài liệu nội bộ `research-pack/01_origin` thiếu → bổ sung khi cập nhật docs | – |
| D8 / H: model có thể vào MVP | Ngoài luồng MVP; chỉ là mở rộng | Phạm vi chốt |
| D9: uplift có thể là gói của app | Kế hoạch đánh giá **ngoài app** | Phạm vi chốt |
| Benchmark gộp | Tách **benchmark proposal** (thuật toán, state rỗng, chưa qua người) với **đánh giá collection** (sau human review = thuật toán + quyết định người) | Hai loại đo khác nhau, không so trực tiếp |
| Gói G1–G8 | Thay bằng 4 giai đoạn (§4.2); G0 dọn lệch nhỏ chia vào giai đoạn liên quan | – |

### 4.2. Bảng requirement theo giai đoạn

**Giai đoạn 1 — Nền tảng dataset và snapshot** (thiết kế chi tiết: [`docs/archived/2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md`](../2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md))

| Requirement / use case | Hiện đã có [XM] | Thiếu hoặc lệch | Thay đổi cần làm [ĐX] | GĐ | Dependency | Acceptance criteria |
|---|---|---|---|---|---|---|
| 1.1 Migration DB giữ dữ liệu | `store/db.py`: `SCHEMA_VERSION = 1`, khác version → `RuntimeError` "xoá file DB" | Đổi schema = mất phiên / log quyết định | Bộ chạy migration theo `user_version`, sao lưu file trước khi nâng, từ chối DB mới hơn app | 1 | – | DB v1 có dữ liệu → mở bằng app mới: có file backup, mọi phiên / quyết định / proposal giữ nguyên, `get_state` trùng trước khi nâng |
| 1.2 Snapshot bất biến của collection | Collection = `ReviewState.kept` replay từ log (`service/review_state.py`); export chép kept lúc bấm | Không có id phiên bản, không nối với `decision.seq` | Bảng `snapshot` + `snapshot_member` (trigger chặn UPDATE/DELETE), `members_hash`, `decision_seq`, provenance từng mẫu; command `create_snapshot`, `list_snapshots`, `get_snapshot` | 1 | Q2 (chỉ phần mẫu ảnh) | Snapshot = đúng kept lúc tạo; đổi kept → snapshot cũ không đổi nội dung/hash, `is_current = false`; UPDATE/DELETE bị DB từ chối |
| 1.3 Phân biệt loại nguồn (video / tập ảnh) | Mọi kiểu xoay quanh `VideoRef`, `Unit.video_id`, `Frame`; cache tách theo tiền tố `"{video_id}:"` (`core/execution.py`) | Không biểu diễn được ảnh độc lập mà không giả timestamp | `ImageRef`; `Unit.kind="image"`, `video_id/t0/t1 = None`; `Unit.source_id`, `Unit.order_key`; execution nhóm theo `source_id`; `Context.pixels` đọc ảnh | 1 | **Q2 [MỞ]** | Unit id + `pool_hash` của video **không đổi** (test vàng); cache video cũ vẫn trúng |
| 1.4 Import dataset ảnh tham chiếu | – (source chỉ nhận đuôi video) | Không nạp được dataset cũ | Import thư mục dưới `RAV_DATASETS_DIR`, không sao chép ảnh; dataset + version bất biến, id theo nội dung | 1 | Q1 đã chốt (#65), D1 | Import lại cùng thư mục → cùng version; sửa 1 ảnh → version mới, version cũ giữ nguyên |
| 1.5 Identity + dedup mẫu ảnh | – | – | `sample_id = "{dataset_id}:{sha256[:16]}"`; trùng byte trong một version → giữ một, báo cáo | 1 | – | Đổi tên / chuyển chỗ file trong thư mục không đổi `sample_id`; ảnh trùng byte chỉ một mẫu |
| 1.6 Annotation tối thiểu | Hợp đồng `det.*`/`tag.*` chỉ để hiển thị (§8.0) | Không đọc nhãn nào | `none` hoặc YOLO detection txt: đọc lớp + đếm theo lớp, trạng thái `labeled/empty/missing/invalid`; giữ nguyên file nhãn (hash), không chuyển đổi định dạng | 1 | D1 | Nhãn lỗi chỉ đánh dấu ảnh `invalid`, không làm hỏng import; `none` không đọc nhãn dù có thư mục `labels/` |
| 1.7 Quản lý nguồn + trạng thái | Video: catalog quét thư mục, `prepared` + job P1 RAM (`service/video.py`) | Không có danh sách dataset, trạng thái | `list_datasets`, `get_dataset_version`, `list_dataset_samples`, `get_image`, `queue_prepare_dataset`; feature DINOv2 + pHash cho version | 1 | – | UI thấy số ảnh, trạng thái nhãn, tiến độ chuẩn bị; ảnh xem được |
| 1.8 Vai trò dataset trong phiên (không cờ "đã dùng train") | `pool.video_ids` trong spec | Không có reference | Spec phiên thêm `reference.dataset_version_ids` (cố định khi tạo); dataset **không** có cờ trạng thái train | 1 | – | Tạo phiên có/không reference; phiên cũ đọc ra `reference = []` |
| 1.9 Dọn lệch nhỏ (G0) | `RecipePanel.tsx` cho chọn random/uniform; CONTRACTS ví dụ `threshold`, `ui_focus` | L1–L5, C11 | Ẩn baseline khỏi Studio (recipe mặc định lấy theo tên trong config, không `recipes[0]`); sửa docs L1–L5 | 1 | D10 | Studio không có lựa chọn thuật toán; tài liệu không mô tả thứ không có |

**Giai đoạn 2 — Goal và tìm kiếm có liên quan**

| Requirement / use case | Hiện đã có [XM] | Thiếu hoặc lệch | Thay đổi cần làm [ĐX] | GĐ | Dependency | Acceptance criteria |
|---|---|---|---|---|---|---|
| 2.1 Agent chuyển yêu cầu → goal có cấu trúc, hỏi làm rõ | Goal câu tự do lưu trong spec + mọi `Decision.goal`; command layer có JSON schema | Không LLM; thuật toán không đọc goal | Theo #63: lưu câu gốc + structure có phiên bản; LLM đề xuất structure + câu hỏi làm rõ; người sửa/xác nhận; LLM lỗi không chặn | 2 | D4, Q5 [MỞ] | Structure xác nhận được lưu và nằm trong snapshot input của proposal; tắt LLM vẫn tạo phiên được bằng nhập tay |
| 2.2 ODD do người dùng cung cấp/xác nhận | Hợp đồng `tag.*`, telemetry sidecar (không có dữ liệu thật) | Không có trường điều kiện | Mỗi điều kiện ghi `nguồn ∈ {user, metadata, estimated}` và `trạng thái ∈ {quan sát được, ước lượng, chưa kiểm chứng}`; không suy ODD từ checkpoint/embedding | 2 | D4 | Điều kiện chưa xác nhận hiện "chưa kiểm chứng"; không có điều kiện nào do hệ thống tự gán là "quan sát được" nếu không có metadata |
| 2.3 Relevance text–image | DINOv2 (ảnh–ảnh) | Không truy vấn text | Feature ảnh–text (CLIP/SigLIP) + objective `text_match` có `mask` (cổng) + điểm xếp thứ tự; giữ DINOv2 cho novelty/NMS | 2 | D3 | Trên tập nhỏ gán tay, frame qua cổng đúng chủ đề cao hơn baseline novelty; không gọi similarity là xác suất |
| 2.4 Một recipe cấu hình (relevance × novelty) | Recipe registry, `product_strength`, `greedy_nms` | Novelty chỉ so với kept | Recipe mặc định mới: `text_match` (mask) × `novelty` (reference = kept ∪ dataset tham chiếu) + `greedy_nms`; chọn trong config | 2 | GĐ1 (1.3, 1.8) | Không có chế độ UI mới; `uses_goal = true`; đổi recipe chỉ qua config |
| 2.5 Không lấp batch bằng nới ngưỡng tới 0 | `greedy_nms.RELAX = (1, .5, .25)` rồi ×0 | Có thể đưa frame gần trùng vào batch | Bỏ bước ×0; batch nhỏ hơn + lý do (`hết frame qua cổng` / `còn lại đều gần trùng`); CONTRACTS §9.6 | 2 | – | Pool cạn → batch < `batch_size`, `Proposal` ghi lý do, UI hiện |
| 2.6 Giữ review / keep-drop | Studio, log, hoàn tác | – | Không đổi; `explain_proposal` nói thêm "phù hợp mục tiêu vì…" từ điểm `text_match` | 2 | – | Test luồng L5a/L6 vẫn xanh |
| 2.7 Mẫu goal theo chủ đề trên UI (B8) | Nút "Người đi bộ ban đêm" … | Hứa điều chưa làm | Bật lại khi 2.3 xong | 2 | – | – |

**Giai đoạn 3 — Evidence và report do LLM sinh**

| Requirement / use case | Hiện đã có [XM] | Thiếu hoặc lệch | Thay đổi cần làm [ĐX] | GĐ | Dependency | Acceptance criteria |
|---|---|---|---|---|---|---|
| 3.1 Đặc tả metric đầy đủ | `get_metrics` trả `{type, params, values, independent}` | Thiếu tập tham chiếu, phiên bản dữ liệu/embedding, giới hạn | `EvidenceItem {id, metric, value, unit, reference_set, method, data_version (snapshot id, dataset version ids, FieldKey), params, limitations, independent}` | 3 | D5 | Không item nào thiếu trường; mọi số trên UI/report trỏ được tới một item |
| 3.2 Trùng trong collection và với reference | `nd_rate_at_k` (trong tập), `min_dist` | Không so với reference | Trùng byte (sha), gần trùng pHash, gần nhất embedding tới reference | 3 | GĐ1 | Ảnh tham chiếu chép vào pool → được báo trùng |
| 3.3 Phân bố khoảng cách tới reference, diversity, nguồn | `nearest_kept` theo đợt; `by_video/*` | Không có phân bố cho snapshot | Histogram/quantile khoảng cách tới reference; phân bố theo nguồn/video; diversity trong collection | 3 | GĐ1 | Tái lập: cùng snapshot + cùng version → cùng số |
| 3.4 Random baseline cùng pool, cùng n | `run_benchmark` (CLI, state rỗng, chọn một lần) | Không so collection | Random cùng pool + cùng số mẫu, N seed ghi lại, chấm cùng metric; nhãn rõ **"đánh giá collection (thuật toán + người)"** khác **"benchmark proposal"** | 3 | D12 | Seed trong evidence → chạy lại ra cùng số |
| 3.5 Biểu đồ do backend sinh | View `ViewSpec` + renderer FE | – | View mới đọc từ evidence pack | 3 | – | Không biểu đồ nào lấy số ngoài pack |
| 3.6 LLM viết report từ evidence | – | Không LLM | LLM nhận evidence JSON + structure goal, viết report 5 câu hỏi, trích `id`; kiểm tự động mọi số trong văn bản có trong pack; template = phát triển + fallback | 3 | Q5 [MỞ], D5 | Số lạ trong văn bản → report bị chặn/đánh dấu; có mục "chưa thể kết luận" |
| 3.7 Report gắn snapshot, đánh dấu cũ | – | – | Report lưu `snapshot_id`; cũ khi snapshot không còn hiện hành | 3 | GĐ1 (1.2) | Keep thêm 1 frame → report hiện "không còn cập nhật" |
| 3.8 Ngôn từ có giới hạn | ALIGNMENT §6 | – | Quy tắc trong prompt + kiểm: không novelty→rarity, embedding coverage→ODD coverage, chỉ số trước gán nhãn→mAP | 3 | – | Bộ câu cấm không xuất hiện trong report mẫu |

**Giai đoạn 4 — Duyệt và bàn giao**

| Requirement / use case | Hiện đã có [XM] | Thiếu hoặc lệch | Thay đổi cần làm [ĐX] | GĐ | Dependency | Acceptance criteria |
|---|---|---|---|---|---|---|
| 4.1 Duyệt snapshot trước khi export | – | – | Bản ghi approval (snapshot, reviewer, quyết định, lý do, thời điểm), bất biến | 4 | Q3 [MỞ] | Không export/gửi khi snapshot chưa được duyệt |
| 4.2 Export ảnh + manifest + report cùng snapshot | `csv_images` + zip, giữ mọi lần | Lấy kept hiện tại; manifest thiếu provenance; không report | `export(snapshot_id)`; manifest từ `snapshot_member`; kèm report của snapshot đó | 4 | GĐ1, GĐ3 | `unit_ids` trong manifest == snapshot; report cùng `snapshot_id`; export lại snapshot cũ ra đúng nội dung cũ |
| 4.3 Ít nhất hai vai trò | `ACTOR = "human:local"` | Không vai trò | Danh tính local tự khai + vai trò kiểm ở command layer; ghi rõ "không bảo mật" (LOCAL_LIMITS) | 4 | Q3 [MỞ] | Curator không approve được; log có actor thật |
| 4.4 PII | – | – | Cơ chế theo Q4; không gửi pixel ra LLM ngoài khi chưa qua bước PII | 4 | Q4 [MỞ] | Theo phương án chọn (§5.3) |
| 4.5 Kế hoạch uplift ngoài app | Backlog B3; cảnh báo số giả `nam-init` | – | Script `eval/` ngoài app, đầu vào = manifest export; split theo video, nhiều seed | ngoài app | D9 | Không có tính năng train trong app; báo cáo có độ biến động |

### 4.3. Thứ tự

GĐ1 → GĐ2 → GĐ3 → GĐ4. Trong GĐ1, snapshot (1.1, 1.2) không phụ thuộc quyết định Q1/Q2 nên làm trước; phần ảnh (1.3–1.8) chờ Q2.
GĐ3 cần 1.2 + 1.4 (so reference) và 2.x cho câu "người dùng muốn tìm gì"; có thể làm phần evidence không cần goal song song với GĐ2.
GĐ4 dùng lại snapshot GĐ1 và report GĐ3.

---

## 5. Quyết định

### 5.1. Đã chốt bởi người dùng (2026-10-07) — quyết định #64

> **Cập nhật (lần 3):** người dùng chốt triển khai GĐ1 + lưu trữ theo tham chiếu (manifest tới raw, chỉ đọc/decode khi tải về) + slice CLIP/Kaggle/smoke BDD100K.
> Tiến độ theo slice: `PLAN.md` mục "Giai đoạn 1"; thiết kế: `docs/archived/2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md` §0.4, §9, §11–12.

Phạm vi §4; ảnh không giả làm video; snapshot bất biến; migration không xoá DB; không cờ "đã dùng train"; không nới ngưỡng tới 0; metric phải có
reference/cách tính/phiên bản/tham số/giới hạn; tách benchmark proposal và đánh giá collection; hai vai trò + PII là yêu cầu đề RAV-26;
deliverable cuối có luồng LLM viết report; Kaggle chỉ tạm, tuỳ chọn. Đã ghi #64 vào `01_confirmed_decisions.md` (trong working tree, chưa commit — cùng file với #63 chưa commit của người dùng).

### 5.2. Chi tiết kỹ thuật đã tự chọn (đảo ngược được, ghi trong thiết kế GĐ1)

| # | Chọn | Thay thế đã cân nhắc |
|---|---|---|
| D1 | Dataset tham chiếu = thư mục ảnh dưới `RAV_DATASETS_DIR`, đọc tại chỗ (không sao chép), kiểm toàn vẹn bằng sha256 | Sao chép ảnh (an toàn hơn khi file bị sửa, tốn đĩa); upload zip (để sau, khi host) |
| D1a | Nhãn: chỉ đọc **YOLO detection txt** (+ `classes.txt` / `data.yaml`), còn lại `none`; BDD100K chuyển sang YOLO bằng script ngoài app | COCO JSON (BDD có công cụ đổi sang COCO; target model là YOLO nên chọn YOLO) |
| D2 | Snapshot idempotent: collection không đổi so với snapshot mới nhất → trả lại snapshot đó (`created = false`) | Luôn tạo mới (thừa bản trùng) |
| D10 | Ẩn baseline khỏi Studio; baseline chỉ ở benchmark/evidence | – |
| D12 | Baseline trong evidence: random + uniform cùng pool, cùng n | – |
| D13 | Kaggle/GPU ngoài: tuỳ chọn, chỉ điền cache đúng key (§7) | – |
| – | Reference cố định khi tạo phiên (như goal, #63) | Cho thêm giữa phiên (phải đưa reference vào snapshot input của proposal) |

### 5.3. Còn mở — cần người dùng chốt

| # | Câu hỏi | Khuyến nghị | Trade-off ngắn |
|---|---|---|---|
| ~~**Q1**~~ | **Đã chốt 2026-10-07 (#65):** ảnh dataset chỉ làm tham chiếu, pool phiên chỉ video | **Chỉ video** trong MVP; kiểu dữ liệu GĐ1 vẫn cho ảnh vào pool về sau | Nhận ảnh trong pool phải sửa mọi component thời gian (`greedy_nms`, `uniform`, `temporal_coverage`, `timeline`, `csv_images`, `explain`, `evidence`, lightbox clip) — thêm ~1 lát mỗi GĐ |
| ~~**Q2**~~ | ~~Đổi contract `Unit`~~ — **đã chốt 2026-10-07 (#64):** người dùng giao quyết định kỹ thuật, giữ id + cache video cũ | **Đồng ý** | Contract hiện tại không biểu diễn được ảnh mà không giả timestamp; thay đổi giữ nguyên id/cache của video. Phương án khác (bảng reference tách khỏi `Unit`) sẽ nhân đôi đường tính feature/cache |
| **Q3** | Hai vai trò nào, quyền gì, mức thực thi? | **Curator** (tạo phiên, keep/drop, tạo snapshot) / **Reviewer** (duyệt snapshot, export); danh tính local tự khai, kiểm ở command layer, ghi rõ không bảo mật | Đăng nhập thật (LOCAL_LIMITS lát 1) đúng hơn nhưng lớn hơn cả GĐ4 |
| **Q4** | Cơ chế PII | **Làm mờ mặt + biển số khi export** (detector nhẹ chạy trên ảnh xuất, manifest ghi phương pháp + phiên bản) + Reviewer xác nhận khi duyệt; không gửi pixel ra LLM ngoài | Detector là một phụ thuộc inference (khác perception target); chỉ xác nhận tay rẻ hơn nhưng không phải cơ chế ẩn danh |
| **Q5** | LLM provider, dữ liệu được gửi đi (chỉ goal + evidence JSON, không pixel?) | Một provider qua API, khoá trong `.env`, chỉ gửi văn bản + số liệu | Gửi ảnh cho VLM giúp nhận xét batch nhưng dính PII (Q4) và chi phí |
| D3 | Model ảnh–text cho GĐ2 | Chốt ở thiết kế GĐ2 sau một phép thử nhỏ (CLIP vs SigLIP trên vài câu truy vấn) | Kỹ thuật, đảo ngược được (FieldKey khác) |
| D4 | Mục còn mở của #63 §7 (schema structure, `tag.*`) | Chốt ở thiết kế GĐ2 | – |

---

## 6. Kết luận (cập nhật)

- **Giữ lại:** registry/plugin, command layer, P1 + cache, log quyết định, Studio keep/drop, export giữ mọi lần, benchmark ND-Rate. GĐ1 chỉ thêm, không viết lại.
- **Thiếu quan trọng nhất theo thứ tự phụ thuộc:** snapshot bất biến + migration (GĐ1) → dataset tham chiếu (GĐ1) → goal/relevance (GĐ2) →
  evidence + report LLM (GĐ3) → duyệt, hai vai trò, PII, export đồng nhất (GĐ4).
- **Lệch cần sửa:** C11/L1–L5 ở GĐ1; B8, C7 ở GĐ2; L3 (ALIGNMENT gộp keep/drop với duyệt cuối) sửa khi cập nhật docs GĐ4.
- **Làm trước:** slice S1 của GĐ1 (migration), rồi S2 (snapshot) — xem thiết kế GĐ1 §9.

---

## 7. Ghi chú: tính embedding CLIP trên Kaggle bằng `kaggle_extend_api` (khảo sát 2026-10-07) — tuỳ chọn, không chặn GĐ1–4

Ý tưởng: phần nặng (image tower CLIP trên mọi frame) chạy trên GPU Kaggle; ở local chỉ chạy **text tower đã quantize** để mã hoá câu
truy vấn / `find` / `avoid` rồi so cos với image embedding đã tải về. Đây là phương án D13(b), phục vụ requirement 2.3 (GĐ2). Tên "G5/G5a" bên dưới là
mã gói của bản audit đầu, nay thuộc GĐ2.

### 7.0. Kaggle chỉ là giải pháp tạm (người dùng xác nhận 2026-10-07)

Dùng Kaggle vì nhóm **chưa có GPU cloud**. Hệ quả cho thiết kế:

- **Không để app phụ thuộc Kaggle.** Code gọi `kaggle_extend_api` chỉ nằm trong một script ngoài `src/rav` (vd `scripts/`); app chỉ biết "cache field đã có
  hoặc chưa". Khi có GPU cloud, thay script bằng worker GPU (LOCAL_LIMITS, lộ trình lát 3 "Tính toán tách khỏi API") mà P2/P3/API/UI không đổi.
- **Ranh giới cần giữ** (để thay nơi tính không ảnh hưởng gì): nơi tính chạy đúng mã `src/rav` + đúng session spec, chỉ ghi file
  `FieldStore` đúng `FieldKey` + `(video_id, spec_hash)`; local kiểm `unit_ids` trước khi nhận. Không thêm khái niệm "Kaggle" vào CONTRACTS — nếu cần
  ghi thì ghi chung "cache có thể được điền bởi tiến trình ngoài".
- **Không đầu tư vào phần riêng của Kaggle:** không dựng UI/command cho session Kaggle, không tối ưu cách đẩy file qua jupyter proxy hơn mức cần
  để chạy được; `keepalive`, MCP, snapshot không cần dùng.
- Các rủi ro R1, R6, R7 bên dưới là **riêng của Kaggle**, sẽ hết khi chuyển GPU cloud; R2–R5 (khớp key, `unit_ids`, sai số phần cứng, quantize)
  **vẫn còn** với mọi nơi tính ngoài, nên làm phần kiểm tra cho chúng dùng lại được.

### 7.1. `kaggle_extend_api` làm được gì (đọc code + `README.md` + `TEST_REPORT.md`, thư mục `final/kaggle_extend_api/kaggle_extend_api/`)

| Khả năng | Evidence | Đã kiểm chứng (theo TEST_REPORT 2026-10-05) |
|---|---|---|
| Tạo / chờ / dừng **interactive session** trên notebook có sẵn, chọn accelerator (`t4`, `p100`, …), bật internet | `client.py::create_session / wait_session / stop_session` (kagglesdk `CreateKernelSession`, `CancelKernelSession`) | Live với **CPU**; **GPU/TPU chưa được thử** |
| Chạy code trong session, lấy stdout/lỗi | `tools.py::JupyterTools.execute` (websocket jupyter) | Live ✅ |
| Gửi / lấy file | `put_file` / `get_file` qua jupyter contents API, **cả file base64 trong một request** | Live ✅ (file nhỏ) |
| Snapshot / restore thư mục làm việc | `snapshot` / `restore` | Live ✅ |
| Quota GPU, log, gọi mọi method kagglesdk (datasets, kernels…) | `quota`, `logs_stream`, `raw(...)` | Quota/log ✅; từng endpoint raw **chưa** |
| MCP server 24 tool | `mcp_server.py` | Live ✅ |

**Kết luận: có — tạo được job (session) trên Kaggle và điều khiển hoàn toàn bằng code**, đủ cho luồng "đẩy video lên → chạy script → kéo file kết quả về".
Đây là session tương tác (không phải "Save Version"), chạy server-side, không cần mở trình duyệt.

Kiểm tra trong audit này: chỉ đọc code/tài liệu. **Không** chạy test offline (môi trường Python của P-026 chưa có `kagglesdk`, `mcp`; README trỏ tới
`.venv` ở `E:\GHuy\...` của máy khác), **không** chạy live (cần credentials và sẽ tạo session thật). Không đọc các file `kaggle.json` / `credentials.json`
nằm trong `.pytest_cache/` của repo đó (có vẻ là fixture test, nhưng nên kiểm tra và không commit).

### 7.2. Luồng đề xuất

```text
local                                        Kaggle (session GPU, internet bật)
──────                                       ──────────────────────────────────
1. create_session(notebook, accelerator=t4)  →  jupyter server
2. đưa video lên (xem rủi ro R1)             →  /kaggle/working/videos/
3. đưa mã src/rav (hoặc pip từ git)          →  chạy P1 chỉ cho field emb.clip_*  (cùng decoder pyav 4 fps, cùng unit builder)
4. get_file / snapshot pull                  ←  output/fields/<video_id>/<spec_hash>/emb.clip_*--…@<version>-<params_hash>.{npy,json}
5. chép vào output/fields/ local; stop_session
6. local: text tower quantize mã hoá câu → cos với emb.clip_* đọc từ FieldStore (objective text_match)
```

Vì sao khớp với kiến trúc hiện tại: `FieldStore` (`src/rav/store/field_store.py`) chỉ là cặp file `.npy` + `.json` theo đường dẫn
`<video_id>/<spec_hash>/<name>--<producer>@<version>-<params_hash>`; feature `per_sample=True` cache **theo cả video** (CONTRACTS §3). Nếu Kaggle
chạy **đúng class feature, đúng decoder/unit spec**, file sinh ra trùng key và `ctx.field` ở local đọc như cache bình thường — P2/P3/UI không phải sửa.
`unit_ids` lưu trong `.json` nên lệch tách frame sẽ lộ ra (cần kiểm khi nạp, xem R3).

### 7.3. Rủi ro / điều kiện

| # | Rủi ro | Mức | Cách xử lý |
|---|---|---|---|
| R1 | **Đẩy video lớn:** `put_file` đọc cả file vào RAM, base64 (+33%) và gửi trong **một** PUT qua proxy jupyter; chưa thử với file hàng trăm MB, giới hạn kích thước của proxy Kaggle chưa biết | Cao | Thử với video 5 phút trước; nếu lỗi: (a) Kaggle Dataset riêng tư gắn vào notebook (cần kiểm session có thấy data source của kernel không), (b) tải từ nguồn gốc bằng internet trong session, (c) tách khúc ở local rồi ghép. Kết quả trả về nhỏ (vd 1.200 frame × 512 float32 ≈ 2,4 MB) nên chiều về không đáng lo |
| R2 | **Trộn FieldKey:** CONTRACTS §10 "một phép tính không bao giờ trộn hai FieldKey". Image embedding (Kaggle, fp16/fp32 GPU) và text embedding (local, int8) **phải từ cùng một checkpoint**; nếu sau này tính image embedding ở local bằng tower quantize thì đó là feature khác (`name`/`version` khác), không được so với cache từ Kaggle | Cao | Một feature `emb.clip_<model>` duy nhất cho ảnh; text tower quantize là chi tiết bên trong objective `text_match`, ghi tên model + kiểu quantize vào docstring/params để `config_hash` đổi khi đổi |
| R3 | **Tái lập unit:** Kaggle phải tách frame giống hệt local (pyav cùng phiên bản, 4 fps) nếu không `unit_ids` lệch | TB | Chạy chính mã `src/rav` trên Kaggle; khi nạp file về, kiểm `unit_ids` == unit của video ở local, sai thì từ chối (không "căn gần đúng") |
| R4 | **Sai số phần cứng:** cùng FieldKey nhưng GPU fp16 vs CPU fp32 cho số hơi khác; cache key không ghi nơi tính | Thấp | Chạy fp32 trên Kaggle hoặc ghi rõ; kiểm cos(GPU, CPU) ≥ 0,999 trên vài frame một lần |
| R5 | **Text tower quantize lệch:** int8 làm đổi thứ hạng frame | TB | So text embedding fp32 vs int8 trên bộ câu truy vấn mẫu: cos và tương quan thứ hạng top-k trên một video; chỉ dùng khi đạt ngưỡng chốt trước |
| R6 | **Dữ liệu rời máy / PII:** video được gửi lên tài khoản Kaggle | TB | Thuộc D7; dùng notebook/dataset private; không gửi dữ liệu chưa được phép |
| R7 | **Vận hành:** session có thể xếp hàng 30–120 s, một session mỗi kernel (409 nếu trùng), quota GPU theo tuần, tắt khi idle (`keepalive` lách idle timer — nên hạn chế, kiểm điều khoản Kaggle); JWT nằm trong `jupyter_url` → không ghi URL vào log/DB | TB | Job theo lô (nhiều video một lần), luôn `stop_session` trong `finally`; khoá API trong `.env` (AGENTS §5) |
| R8 | **Kiến trúc:** CONTRACTS §5 dòng 5 nói execution là hạ tầng một chính sách, "nâng thành kind khi có chính sách thứ hai, vd job nền". Tính trên Kaggle là chính sách thứ hai | – | Bản tối thiểu không cần đổi contract: coi là **script ngoài app** điền cache (giống CLI `prepare`), ghi vào CONTRACTS §3/§13 rằng cache có thể được điền từ nơi khác nếu đúng key + `unit_ids`. Nối vào nút "Chuẩn bị" trên UI thì mới là thay đổi thiết kế (hỏi trước) |

### 7.4. Cần chốt thêm

- Model CLIP/SigLIP cụ thể (checkpoint, độ phân giải) — quyết định cùng D3; text tower quantize bằng gì (vd ONNX Runtime int8 động) → thêm `requirements.txt`.
- `kaggle_extend_api` dùng thế nào: dependency cài riêng cho script (khuyến nghị, vì là tạm — không vào `requirements.txt` của app) hay vendored.
- Khi nào chuyển sang GPU cloud và nhà cung cấp nào — không chặn G5a, nhưng nên ghi vào log quyết định rằng Kaggle là tạm để không ai xây thêm trên nó.
- Video mới upload ở local khi chưa chạy Kaggle: relevance hiện "chưa có" (ẩn, theo nguyên tắc "không có nguồn → không hiện") hay chờ job.

**Bước kiểm chứng tối thiểu trước khi làm G5a:** chạy một session T4 với video 5 phút (`data/youtube5m/`): đo thời gian đẩy video, thời gian tính,
kiểm `unit_ids` khớp, so cos GPU vs CPU trên 20 frame, so text fp32 vs int8 trên ~10 câu. Việc này tạo tài nguyên trên tài khoản Kaggle → cần người dùng
đồng ý và tự cấp credentials.

> Dừng ở đây để review. Chưa triển khai gói nào.
