# Các quyết định đã được chốt trong quá trình làm RAV-26

**Status: CONFIRMED DECISION LOG**

File này dùng để tránh scope drift.

## Perception release review (2026-10-15)

Người dùng yêu cầu kiểm tra, sửa và deploy #140. Tích hợp VPS upload đã live trước release; giữ §21.30 cho VPS,
chuyển mục Perception sang §21.31 để không trùng số. Job PROPOSE nền chia sẻ dispatcher/slot YOLO khi TEXT là HTTP,
không tăng budget; checkpoint batch tạm trước khi chuyển sang job. Giữ nguyên S3-before-decode, không làm upload UI recovery ở lát này.

Release đã kiểm chứng: source `86c10ab7`, Render `dep-db8e6kdg1s2s739vu5n0`; giữ schema/policy/env.
QA YOLO11n COCO trên dashcam60s đạt batch 16 từ 32 ứng viên, matching thật, cache không ghi lại,
Giữ/Bỏ loại đúng và chuyển Interactive/job nền đúng số batch. [QA](../../setup/PERCEPTION_INSTABILITY_RELEASE_QA.md).

## Sửa review landing DriveLens (2026-10-15)

Người dùng duyệt sửa 5 finding trên `feat/drivelens-landing-page`: bỏ số liệu/cam kết chưa có bằng chứng,
không tuyên bố PII hoàn chỉnh khi detector biển số chưa chạy đầy đủ, sửa responsive và filter,
giới thiệu đúng phím tắt Studio. Demo công khai chỉ chọn frame và đổi preview PII, ghi rõ dữ liệu mô phỏng;
không thay contract hay triển khai PII/backend trong lát này. Không deploy/push.

## 1. Giữ đề bài gốc làm origin

RAV-26 xuất phát từ bài toán:

> Data Curation Copilot for ADAS/Robotics: chọn sample giá trị dưới annotation budget hữu hạn.

Không được xóa lịch sử này dù MVP đã thu hẹp.

## 2. MVP đầu tiên tập trung sample/frame selection

Không cố xây full active-learning loop ngay.

## 3. Tách Data Curation và Active Learning

- Data curation trước.
- Model uncertainty + retraining loop để phase sau.

## 4. Video redundancy là pain point trung tâm của MVP đầu

Tập trung dữ liệu video có temporal correlation, không giả định frame độc lập.

## 5. Agent không phải phần bắt buộc của MVP

Agent Review/Agent orchestration từng có trong PRD gốc nhưng đã được bỏ khỏi MVP vì use case chưa đủ rõ.

## 6. Baseline selection

Dùng cùng K:
- Random;
- Uniform Temporal;
- Embedding Diversity.

## 7. Evaluation đơn giản trước

Primary MVP metric:
- Near-Duplicate Rate@K.

Không claim mAP uplift khi chưa retrain/test thực sự.

## 8. Annotation editor không phải core build

Có thể handoff sang CVAT/Label Studio thay vì xây lại annotation platform.

## 9. Competitive audit phải có trước khi quyết định build thêm

FiftyOne và LightlyStudio được khảo sát vì đã có nhiều capability mà MVP ban đầu định xây.

## 10. Build-vs-buy: xem #15

## 11. Giá trị MVP 4 ngày: giảm trùng + vòng duyệt

Chọn K frame ít trùng hơn Random/Uniform, cộng vòng keep/drop: vòng sau tránh frame giống tập đã giữ.
So sánh hai model (YOLO11s/m vs 11x) **không** vào MVP 4 ngày; chỉ chừa chỗ trong contract (§8).

## 12. Đơn vị đề xuất: frame + ngữ cảnh ngắn, contract dạng span

`Unit(video_id, t0, t1)`; MVP dùng frame (`t0 == t1`), UI tua ±vài giây. Chấm cả sequence về sau không phá contract.

## 13. Dữ liệu cũ = tập đã keep trong phiên

Không có tập train của khách; novelty tính so với tập đã keep, bắt đầu rỗng.

## 14. Dataset: MVP thử trên 1 video; BDD100K (tập con video) sau MVP

## 15. Giao diện tự làm (React + FastAPI), không dùng FiftyOne/Lightly làm host

Lý do (nhóm xác nhận 2026-09-27): hai tool khó tiếp cận cho việc chọn + duyệt frame; thuật toán chọn không vượt trội selector tự viết;
vòng duyệt frame kèm ngữ cảnh thời gian phải tự viết dù dùng tool nào → build trên đó có thể tốn công hơn tự build.
Bằng chứng: `05_hands_on_audit/06_findings_2026-09-27.md`. Đây là đánh giá công sức, không phải kết luận hai tool không hỗ trợ được.

## 16. Kiến trúc: mọi component thay được, giao tiếp qua Context + tên field

Extension point (13 lúc đầu, nay 16 kể cả view/judge/router), registry + config; session spec cố định, recipe đổi được giữa các vòng.
Feedback MVP: keep/drop. Lưu trữ: SQLite + file. Export: CSV + thư mục ảnh.

## 17. Agent là client của command layer

Chatbot chỉ ra lệnh thao tác UI và report chỉ số, qua cùng command layer với UI. Chừa chỗ, không bắt buộc trong 4 ngày.

## 17b. Hỗ trợ trực quan hoá

View là component thay được (embedding map, timeline, pick grid, duplicate pairs); phép chiếu 2D là Feature.
Xem `docs/architecture_diagram.md` §4.

## 18. Viết lại structure code mới

Port logic dùng được từ `src/` cũ rồi xoá file cũ.

## 19. Thuật ngữ và vị trí state (2026-09-27)

- `recipe` = cách đề xuất (#24); `review_state` = kept/dropped của phiên; `proposal` = batch frame thuật toán trả về.
- BE giữ `review_state`; FE xem (`GET .../state`) và sửa (`PATCH .../state`).
- Hàm lõi `propose(recipe, batch_size, seed, ctx)` không đọc DB (state lấy từ `ctx.state`); mỗi proposal trả kèm snapshot đúng input đã dùng.

## 20. Tự động hoá quyết định: interface + log, chưa build (2026-09-27)

- Ba vai trò Router / Proposer / Judge; người là một implementation. MVP chỉ có người + log quyết định đầy đủ.
- Ưu tiên: (a) đánh giá recipe không cần người → (b) giảm công duyệt nhờ dữ liệu log → (c) tự động khi có judge đủ tin.
- Mỗi phiên có `goal` tường minh; schema log quyết định giữ ổn định từ MVP (CONTRACTS §12.3).

## 21. Pool tổng quát, session_id = uid (2026-09-27)

Thuật toán không phụ thuộc số video (1 video / 1 frame là trường hợp n = 1). Pool của phiên là bộ lọc trên bảng unit toàn cục
(MVP lọc 1 video); `session_id` là uid ngẫu nhiên, không chứa video. Video chỉ dùng để lọc pool, lọc hiển thị
và làm tham số nội bộ của component so thời gian (CONTRACTS §13). Thay cho `{video_id}_{uid}` chốt trước đó.

## 22. Ngân sách K, batch, giải thích (2026-09-27)

- `target_k` (K) = tổng số frame muốn chọn ra, đặt khi tạo phiên; mỗi vòng đề xuất `batch_size` frame; lặp tới khi kept = K.
- Dữ liệu trong `data/` là mock; cần chọn video thật với pool ≥ 1.000 frame trước khi benchmark.
- Giải thích trực quan để sau; MVP chỉ trả điểm từng objective.

## 23. Upload video trong MVP; product story hứa nhiều video (2026-09-27)

- Người dùng tải video lên qua UI (`POST /videos`), không phải tự chép vào thư mục; upload kiểm tra định dạng + dung lượng, `video_id` = hash nội dung.
- Product story mô tả ở mức tổng quan: làm việc với một hoặc nhiều video. PLAN giữ phạm vi kiểm thử MVP là 1 video.

---

# Bổ sung 2026-09-29 (làm lại MVP từ mốc docs `0fd6c91`)

Bối cảnh: code ở nhánh `duy-mvp/init` lệch contract (thu hẹp phạm vi không hỏi, rồi docs bị viết lại theo code).
Làm lại trên nhánh `duy-mvp/v2` theo `docs/IMPLEMENT_GUIDE_V2.md`.

## 24. `strategy` đổi tên thành `recipe`

Recipe ("cách đề xuất") = công thức ghép reference + objective + combiner + selector (CONTRACTS §5–7), khai báo trong code Python
`src/rav/components/recipes.py`, không dùng file JSON ngoài `src/`. API/DTO: `POST /sessions/{sid}/proposals {recipe, config?, batch_size, seed?}` (#45),
`GET /api/v1/recipes`, `RecipeDTO`, `RecipeConfig`, metric `keep_rate_by_recipe`.

## 25. Thứ bậc nguồn sự thật và quy trình

- Cấp 1 — sản phẩm: `docs/PRODUCT_STORY.md`, `docs/design/RAV26_ALIGNMENT.md`, file này.
- Cấp 2 — thiết kế, code phải theo: `docs/design/CONTRACTS.md` + `docs/architecture_diagram.md`.
- Cấp 3 — dẫn xuất: `docs/components/*` (chỉ trích dẫn CONTRACTS), `PLAN.md`, code.
- Docs trước, code sau: muốn code khác cấp 2 → sửa docs trước (cùng commit) và hỏi người dùng nếu đổi thiết kế.
- Một phiên làm việc = một lát cắt trong `PLAN.md`: test xanh → commit → tick PLAN → dừng.

## 26. Không tự cắt phạm vi contract

Muốn làm ít hơn contract (vd chưa làm command layer) → hỏi người dùng trước, ghi quyết định vào file này và đánh dấu "Hướng tới" trong CONTRACTS.
Người dùng hỏi thì trả lời, không tự chọn đáp án rồi làm tiếp.

## 27. Cấu trúc thư mục

- Frontend nằm trong `src/web/` (project Node riêng), cạnh backend `src/rav/`.
- Tên thư mục `components/` cho cả `src/rav/components/` và `docs/components/`, khớp 1–1 với kind ở CONTRACTS §5.
- Mỗi tầng kiến trúc một thư mục trong `src/rav/` (`api`, `cli`, `commands`, `service`, `pipeline`, `core`, `components`, `store`); cây ở architecture §9.

## 28. Chốt các chỗ còn mở trước khi code P2 (CONTRACTS §9.5–9.10)

- Novelty khi chưa keep gì: so với tâm pool (1 − cos tới mean embedding, min-max về [0,1]).
- Selector thiếu `batch_size` vì NMS/ngưỡng: nới bộ lọc mềm, báo `relaxed: true`; không nới tính duy nhất của `unit_id`.
- Không cho keep vượt `target_k`: chặn ở service.
- Baseline `random` / `uniform` là selector với objective rỗng, không qua bộ lọc diversity.
- `Params`: mọi implementation là class `Component` có `Params` Pydantic.
- Ngưỡng pHash mặc định 5; spot-check bằng view `duplicate_pairs` trước benchmark chính thức.

## 29. Decoder 4 fps

Thay 2 fps trong docs `0fd6c91`: video 5 phút ≈ 1.200 frame, đủ pool ≥ 1.000 (PRD FR-01) cho benchmark.
Thuật toán không phụ thuộc số video, phiên dùng `pool`, `session_id` = uid (#21) — giữ nguyên.

## 30. Vá chữ ký CONTRACTS trước L1 (2026-09-29, người dùng đồng ý sau review)

1. Selector trả `SelectResult(indices, relaxed)` thay cho `list[int]`, để báo được `relaxed` (§9.6).
2. `Context.proposals` (read-only): lịch sử đề xuất cho metric `keep_rate_by_recipe` và view `timeline`.
3. Novelty có reference = `(1 − max cos) / 2` để luôn trong [0,1] (§9.1); nhánh reference rỗng giữ min-max (#28).
4. Kind `unit_builder` đổi thành `unit`; quy ước thư mục = tên kind + "s" (`units/`).
5. `store` là tầng hạ tầng (`src/rav/store/`), không đăng ký qua registry.
6. Dọn code/script cũ + xoá `AGENT.md` (guide bước 0.4, 1.4) là việc đầu tiên của L1.

## 31. `pool` vs `candidates` (2026-09-29)

- **pool** = unit của phiên theo `PoolFilter` (cố định); **candidates** = pool − kept − dropped. Cách tính feature theo tập: #32.

## 32. `Feature.per_sample`: cache theo mẫu hay theo pool (2026-09-29)

- `compute(units, ctx)` nhận các mẫu và tự xử lý; **gom nhóm** (theo video, theo pool...) là logic bên trong `compute`, không phải
  cơ chế của execution. Chỉ tách thành helper/callback khi nhiều implementation lặp lại cùng kiểu gom.
- `Unit` = mẫu; MVP là frame, vẫn giữ span (#12).
- `per_sample: bool` trên feature — giá trị của một mẫu có chỉ tính từ chính nó không. Chỉ để execution cache đúng:
  `True` → tính mẫu còn thiếu, cache mỗi video một file (thêm video không tính lại video cũ, §13.4) — đơn vị "còn thiếu" làm rõ ở #33;
  `False` → tính một lần trên cả pool, cache theo `pool_hash`.
- Bỏ mức `session` / `round`: feature không đọc `ctx.state`; reference/objective/selector đọc `ctx.state` mỗi lần propose, không cache.
  Bỏ luôn cơ chế invalidate khi có feedback.
- MVP: `proj.pca2` (`per_sample=False`, fit trên cả pool); `proj.pca2_video` (tự gom theo video) là ý tưởng.

## 33. Làm rõ cache feature trước L2 (2026-09-29, người dùng đồng ý sau review)

1. `per_sample=True`: đơn vị cache là **cả video**. "Còn thiếu" = video chưa có file cache → tính mọi unit của video đó; không có file đầy một nửa.
2. `pool_hash` = `sha256` của danh sách `unit_id` trong pool theo thứ tự `(video_id, t0)`. Đổi fps (`decode_version`) hay đổi pool → hash khác; phiên cùng pool dùng chung cache.
   (`decode_version` không được định nghĩa và cache `per_sample=True` chưa tính tới fps → sửa ở #34 bằng `spec_hash`.)
3. Feature `requires` một field `per_sample=False` thì bản thân phải `per_sample=False`; vi phạm → raise khi resolve.
4. Lưu ý (không sửa): novelty vòng đầu dùng min-max, các vòng sau dùng `/ 2` → điểm `breakdown` không so trực tiếp giữa vòng đầu và các vòng sau.

## 34. Dọn chỗ trùng và thiếu nhất quán trước L1 (2026-09-29, người dùng đồng ý sau review)

Sửa chỗ sai:
1. **Cache theo cách tách frame.** Frame/thumbnail của P1 và feature `per_sample=True` cache theo `(video_id, spec_hash)`,
   `spec_hash` = hash cấu hình `decoder` + `units`. Trước đó cùng video khác fps sẽ đọc nhầm cache cũ. Bỏ chữ `decode_version`.
2. **Một kiểu cho log quyết định.** Xoá kiểu `Feedback` ở §2 (trùng vai `Decision`, lại có action `skip` mâu thuẫn).
   `Decision` thêm `extra: dict` (cho more/less-like-this, tag lý do) để schema log không phải đổi về sau.
   Kind `feedback` và component `keep_drop` giữ nguyên.
3. **`Proposal.n` và `Proposal.round` là hai thứ.** `n` = thứ tự proposal trong phiên (`Decision.proposal_n` trỏ tới);
   `round` = vòng duyệt (re-roll sau MVP tạo proposal mới cùng round). MVP: `round == n`.
4. `RecipeConfig` (architecture §5.3) thêm `reference` cho khớp CONTRACTS §7; `MetricsDTO` bỏ key `kept` lặp; định nghĩa `History`.

Bỏ chỗ trùng:
5. `propose(recipe, batch_size, seed, ctx)`: bỏ tham số `review_state`, kept/dropped chỉ đọc từ `ctx.state`. `JudgeRequest` bỏ `review`.
6. Bỏ `Pick.source_recipe` (trùng `Proposal.recipe` khi mỗi proposal một recipe). Thêm lại khi trộn nhiều recipe trong một proposal.
7. Bỏ view `pick_grid` (trùng dữ liệu `ProposalDTO`); MVP còn 3 view.
8. `execution` là hạ tầng (`core/execution.py`), không phải kind đăng ký — chỉ có một cách chạy. Nâng lại thành kind khi có job nền.

Gộp nội dung docs lặp (mỗi thứ một chỗ, chỗ khác trỏ tới):
9. Thứ bậc nguồn sự thật: `docs/README.md`. Cây thư mục: architecture §9. Công thức, chữ ký: CONTRACTS
   (ND-Rate@K định nghĩa ở §9.10, keep rate theo recipe ở §10). Phạm vi MVP: `PLAN.md`.
10. `docs/IMPLEMENT_GUIDE_V2.md` là kế hoạch ban đầu; chỗ nào khác PLAN/CONTRACTS thì theo PLAN/CONTRACTS.

## 35. Sơ đồ tầng + luật import; command cho video (2026-09-29)

- Architecture §2 vẽ lại: mỗi hộp là một thư mục ở §9, mũi tên là chiều import được phép, kèm bảng luật import.
  Mỗi lát tự kiểm không có import ngược chiều. `pipeline/` lấy component qua registry theo tên, không import trực tiếp.
- Thêm command `list_videos`, `upload_video` (endpoint `/videos` ở architecture §5.2 trước đó chưa có command tương ứng,
  trái nguyên tắc "1 route = 1 command") và `prepare_video` (để CLI `prepare` cũng đi qua command, không gọi thẳng pipeline).

## 36. Tham số của feature (2026-09-29)

- Feature luôn chạy với `Params` mặc định; recipe chỉ gọi field bằng tên.
- Biến thể (vd DINOv2 ảnh 336) = feature mới kế thừa feature cũ, đổi `name`, `provides`, default `Params` → field riêng (`emb.dinov2_s336`).
  Tường minh, cache riêng, benchmark so được hai biến thể. Giá: mỗi biến thể một file ngắn.
- `Params` của feature chỉ chứa thứ làm đổi kết quả (nằm trong `params_hash`). `batch_size`, `device`, số thread là cấu hình của execution.
- Khi cần quét nhiều biến thể mới thêm chỗ cấu hình feature trong session spec; `params_hash` đã sẵn trong `FieldKey`.

## 37. Decoder serve frame theo yêu cầu bằng PyAV (2026-09-29, người dùng chốt trước L2)

- Decoder là component **serve frame**, không phải bước sinh file ảnh: chỉ lưu video + metadata; pixel decode khi được hỏi.
  Thay phần "frame/thumbnail của P1 lưu theo `(video_id, spec_hash)`" ở #34.1; field vẫn cache theo `(video_id, spec_hash)`.
- Implementation MVP `pyav` (thư viện `av`, ffmpeg trong tiến trình; đọc frame tăng dần thì decode tiếp, không seek lại), thay `opencv`.
- `frames(video)` tất định từ metadata + tham số decoder → dựng lại unit không cần cache danh sách frame.
- Signature `read(frame)` → `read(video: VideoRef, frame)` (trước đó `Frame` chỉ có `video_id`, decoder không biết mở file nào).
  `Context` nhận `videos: dict[video_id, VideoRef]` của pool; `ctx.pixels(frame)` giữ nguyên.
- Frame/thumbnail cho UI: command mới `get_frame(video_id, idx, max_side?)`; `GET /media/...` chỉ còn serve file video tĩnh.

## 38. Hướng tới: chọn frame theo mô tả text (2026-09-30, người dùng đồng ý; không làm trong MVP)

- Curator đặt bộ câu mô tả (và câu phủ định) để ưu tiên frame khớp; ghép với `novelty` qua combiner, `strength` quyết định nặng bên nào.
- Cắm không đổi contract: feature `emb.clip_b32` (CLIP/SigLIP, ảnh cùng không gian text) + objective `text_match`
  (params `field`, `texts`, `negative`; tự mã hoá text bằng cùng model). Đặt text trong `config` gửi kèm đề xuất (#45), không cần API mới.
- DINOv2 không có text encoder nên không dùng được. CLIP hiểu tiếng Anh tốt hơn tiếng Việt → cần model đa ngôn ngữ hoặc dịch trước.
- Ngoài MVP 4 ngày vì thêm model, thời gian embedding, RAM. Ghi ở CONTRACTS §8, `docs/components/{objectives,features}`.

## 39. Chốt trước L3: config lồng, recipe qua registry, Proposal gọn (2026-09-30, người dùng chốt)

1. **Config component dạng lồng** `{"type", "params"}` cho cả session spec lẫn recipe; mọi tham số (kể cả `field`) trong `params`,
   khớp 1–1 `params_schema`; ngoài `params` chỉ có `type` và `strength` (objective). Sửa luôn code L2 (`Registry.build`, `DEFAULT_SPEC`).
2. **`RecipeConfig` ở `core/types.py`**; `components/recipes.py` đăng ký recipe vào registry, tầng trên đọc qua registry (giữ luật import).
   Curator chọn recipe có sẵn, chỉnh bằng `config` đầy đủ gửi kèm (#45); không lưu recipe riêng.
3. **`seed` là nguồn ngẫu nhiên duy nhất**: `propose` tạo lại `ctx.rng` từ `seed`. Seed mặc định (khi không truyền) là việc của command ở L5.
4. **`Proposal` chỉ chứa kết quả chọn.** Tách `get_score_candidates(recipe, ctx)` (nửa đầu của `propose`: gắn reference → objectives → combiner);
   service lấy điểm một vòng bằng cách dựng lại `ctx` từ snapshot và gọi lại (tất định, không lưu). Timeline vẽ field qua `ctx.field`.
   Thêm `Context.set_reference` (reference thuộc recipe, đổi được giữa vòng).
5. `propose` đặt `n = len(ctx.proposals) + 1`, `round = n`, `session_id = None`; service điền `session_id`.
6. `config_hash` trên recipe đã validate (type, version, params điền mặc định, strength), key sắp xếp.
7. Bất biến selector: `min(batch_size, |candidates có mask True|)`.
8. Chi tiết thuật toán trong một component (`greedy_nms`, `uniform`) = mặc định ghi ở `docs/archived/2026-10-13/docs/components/selectors/README.md`;
   code khớp mô tả, sửa sau nếu cần (người dùng: ưu tiên scaffold kiến trúc).
9. `Scores.name` = `type`; hai objective cùng type trong một recipe → Hướng tới (khoá `name` tuỳ chọn).

## 40. Combiner nhận `unit_ids` (2026-09-30, người dùng chốt trong L3)

- Chữ ký `combine(scores, unit_ids) -> (combined, mask)`: `unit_ids` = candidates đang chấm. Không có nó thì `objectives` rỗng
  (baseline) không biết trả mảng dài bao nhiêu. Combiner kiểm mỗi `Scores.unit_ids` khớp `unit_ids`. Sửa CONTRACTS §5 dòng 8, §6.

## 41. Benchmark L4 là sơ bộ; spot-check pHash ở L7 (2026-09-30, người dùng chốt)

- View `duplicate_pairs` (công cụ spot-check ngưỡng pHash, §9.10) thuộc L7, nên L4 chạy benchmark và ghi `eval/results/report.md`
  với nhãn **sơ bộ** (ngưỡng mặc định 5, chưa spot-check).
- L7: spot-check ngưỡng bằng `duplicate_pairs`, chỉnh ngưỡng nếu cần, chạy lại benchmark → số chính thức.

## 42. Metric trả dict phẳng (2026-09-30, L4)

- `Metric.evaluate(unit_ids, ctx) -> dict[str, float]` (CONTRACTS §5 dòng 11); khoá: `<tên metric>` = tổng, `by_video/<video_id>`.
  P3 thêm `independent` (#44).
- `nd_rate_at_k`: `by_video/<id>` = tỉ lệ frame của video đó bị đánh dấu gần trùng khi xét cả tập → tổng = trung bình có trọng số.

## 43. `run_benchmark`: metric chọn qua config của lần benchmark (2026-09-30, người dùng chốt)

- `run_benchmark(recipes, K, seeds, metrics?, video_ids?)`: mỗi lượt recipe × seed × K dựng `Context` mới (state rỗng),
  `propose(batch_size = K)`, rồi P3 chấm bằng từng metric. `video_ids` = pool (mặc định mọi video trong catalog).
- `metrics: list[{"type", "params"}]` (mặc định `[{"type": "nd_rate_at_k"}]`), áp chung mọi recipe, **không** nằm trong recipe:
  recipe là thứ bị chấm, không tự chọn thước (§12.5). Mỗi dòng kết quả có một dict số đo cho mỗi metric, cùng thứ tự `metrics`
  (cùng type khác params, vd quét ngưỡng pHash, không đè nhau). CLI: `--metric <type> [key=value ...]`, lặp được.

## 44. Kiểm độc lập theo `Proposal.fields_used` (2026-09-30, người dùng chốt)

- Field recipe dùng là thông tin của proposal (CONTRACTS §12.5, snapshot `ProposalDTO.input.fields_used`), không phải trạng thái của ctx đang chạy.
- `Proposal.fields_used: list[FieldKey]` (CONTRACTS §2): P2 chép `ctx.fields_used()` cuối lượt propose (ctx dựng riêng cho lượt đó).
- Metric khai báo field mình đọc (`Metric.fields`). P3 `evaluate(metrics, unit_ids, fields_used, ctx)` kiểm một lần cho mỗi metric
  và điền `independent` (1.0/0.0); metric chỉ tính số, `ctx` chỉ để đọc dữ liệu → chấm lại proposal đã lưu không cần propose lại.

## 45. Chỉnh recipe = gửi `RecipeConfig` đầy đủ (2026-09-30, người dùng chốt, trước L5)

- Luồng thuần input → output: `propose(recipe_config, batch_size, seed, ctx)`. Chỉnh tham số hay đổi component = đưa vào config khác.
- Request đề xuất: `recipe` (tên, bắt buộc) + `config?: RecipeConfig` **đầy đủ** (client lấy từ `list_recipes`, sửa, gửi lại);
  server chỉ validate (`RecipeConfig` + `Params` `extra="forbid"`) rồi chạy, không ghép một phần. Bỏ trống = recipe có sẵn.
- `config.name` phải trùng `recipe` (khác → 422) → keep rate theo recipe (§10) gom theo tên; `config_hash` phân biệt bản gốc với bản đã chỉnh;
  snapshot proposal lưu config đã chạy.

## 46. Chốt trước L5: chia lát, cách làm, quy tắc nhỏ, `list_units` (2026-09-30, người dùng chốt)

1. **Chia L5 theo luồng:** **L5a** = vòng duyệt chạy từ đầu đến cuối (db, feedback `keep_drop`, phiên, review_state, proposal, command tương ứng);
   **L5b** = export, `list_recipes` / `describe_component`, `list_units`, `return_scores`, `n_sessions`.
   Khi dựng ctx từ DB, nạp `proposals` cũ vào ctx (cho `keep_rate_by_recipe` ở L7).
2. **DB:** `sqlite3` chuẩn, không ORM; 3 bảng session / decision / proposal; snapshot proposal là một cột JSON.
   Bảng decision chỉ ghi thêm, `review_state` = replay log. Không có migration: đổi schema thì xoá file DB (local-only).
   **Test:** video nhỏ sinh bằng PyAV + ghi sẵn field giả vào field store → execution đọc cache, không chạy DINOv2.
3. **Quy tắc:**
   - không truyền `seed` → command sinh ngẫu nhiên, lưu vào snapshot (proposal vẫn tái lập được);
   - `update_state` làm `|kept| > target_k` → từ chối **cả lô** (422 `target_k_reached`, §9.7), không ghi phần nào;
   - keep/drop/clear unit không thuộc pool của phiên → 422;
   - `clear` unit chưa có quyết định → không làm gì, không lỗi;
   - export ghi vào `output/exports/<session_id>/`, lần sau ghi đè.
4. **Command `list_units(session_id, video_id?)`** (CONTRACTS §11.2) cho `GET /sessions/{sid}/units`: API phải đi qua command.


## 47. Chi tiết L5a (2026-09-30, làm theo #46)

- `create_session` resolve `pool.video_ids` lúc tạo (bỏ trống = mọi video đang có) và lưu vào session spec → pool cố định khi catalog có thêm video.
  Session spec = `app.spec` (source, decoder, units) + `pool` + `feedback: keep_drop` + `goal` + `target_k`, một cột JSON.
- `keep_drop.apply` không ghi `clear` của unit chưa có quyết định vào `history`; service chỉ ghi DB phần `history` mới → log không có dòng vô nghĩa.
- `actor` của quyết định qua command = `"human:local"` (chưa có đăng nhập; local-only, LOCAL_SINGLE_USER_LIMITS #2).
- Lỗi: `session_not_found` 404, `recipe_not_found` 404, `recipe_mismatch` 422, `invalid_config` 422 (type / tham số sai), `unit_not_in_pool` 422, `target_k_reached` 422.
- Lịch sử proposal là command `list_proposals(session_id)` (CONTRACTS §11.2). Snapshot lưu `fields_used` dạng FieldKey đầy đủ (dựng lại `Proposal` cho `ctx.proposals`); DTO trả `"name@version"`.
- DB không migration: `PRAGMA user_version` khác → báo xoá file DB (LOCAL_SINGLE_USER_LIMITS #14).

## 48. Chốt trước L5b: chữ ký exporter, mô tả recipe (2026-09-30, người dùng chốt)

- Exporter: `export(units: list[Unit], dest: Path, ctx) -> Path` (thay `export(picks, dest)`): unit đã keep không nhất thiết là `Pick`
  (keep ngoài proposal; `Pick` mang điểm của một proposal), ảnh lấy qua `ctx.pixels`. Service đưa unit kept theo thứ tự pool,
  xoá `output/exports/<session_id>/` trước khi ghi (#46). `csv_images`: `export.csv` (unit_id, video_id, t0, t1, frame_idx, file) + ảnh JPEG.
- `RecipeConfig.description: str = ""` (viết trong `recipes.py`), không vào `config_hash`. `RecipeDTO.params_schema` = JSON schema `Params`
  của từng component, cùng hình dạng config: `{reference, objectives: [...], combiner, selector}`; `components` = tên → docstring.
- `return_scores`: service dựng ctx chỉ từ snapshot (kept/dropped, config, seed) rồi gọi `get_score_candidates`; trả dạng cột trên candidates.
- `list_units`: `thumb_url` = `/api/v1/videos/{video_id}/frames/{idx}?max_side=320`, `frame_url` = cùng đường dẫn không `max_side`.
- `list_videos.n_sessions` = số phiên có video trong `pool.video_ids`.

## 49. Chốt trước L6: media theo `video_id`, route từ bảng, Output danh sách là mảng, khoá decoder (2026-09-30, người dùng chốt)

1. **Media:** `GET /media/videos/{video_id}` → command `get_video_file(video_id)` tra catalog ra file → `FileResponse` (có Range để tua).
   `VideoDTO.media_url` = `"/media/videos/{id}"`. DB không lưu đường dẫn video (chỉ `video_id` = hash nội dung; source quét thư mục lúc chạy)
   → đổi tên / chuyển file trong thư mục không làm hỏng phiên. `api/` không import `store/` nữa.
2. **Output danh sách là mảng:** `list_videos`, `list_sessions`, `list_units`, `list_proposals`, `list_recipes` trả `RootModel[list[...]]`
   → command, API, agent cùng hình dạng (khớp `VideoDTO[]` … ở architecture §5.2).
3. **Route từ bảng** `(method, path, command)` + handler chung (gom path / query / body → `Input`), CONTRACTS §11.1.
4. **Khoá decoder:** handle PyAV dùng chung → mỗi video một `threading.Lock` (route đồng bộ chạy trong threadpool; lưới UI tải nhiều thumbnail song song
   → không khoá thì đọc nhầm frame / crash, kể cả khi chỉ một người dùng).
5. Chi tiết L6: `Cache-Control: immutable` cho ảnh frame; upload ghi file tạm theo khúc 1 MB, vượt `RAV_MAX_UPLOAD_MB` thì dừng ngay (400 `file_too_large`, cùng mã với service);
   lỗi validate → 422 `validation_error`, lỗi lạ → 500 `internal_error`; `views` / `metrics` thêm vào bảng route ở L7; không bật CORS (FE dev proxy);
   `App.from_env()` đọc `.env` qua `python-dotenv`.

## 50. Chốt trước L7: view `build(ctx)`, `get_view` / `get_metrics` theo phiên, metric chọn qua config, chia L7 (2026-09-30, người dùng chốt)

1. **View:** `build(ctx) -> ViewSpec`, tham số trong `self.params` như mọi component (bỏ `params` thừa ở `build`).
2. **`get_view(session_id, name, params)`**: view dựng trên ctx của phiên (pool, state, proposals). Route `GET /sessions/{sid}/views/{name}`, query → `params`.
3. **`get_metrics(session_id, metrics?, on?)`**: metric chọn qua config `{"type", "params"}` như `run_benchmark` (#43), không cố định trong DTO.
   Mặc định `[nd_rate_at_k, keep_rate_by_recipe]`. `on` = `"kept"` (mặc định) hoặc `proposal_n`. Chấm bằng P3 `evaluate` (một cách chấm duy nhất);
   `fields_used` = của proposal đó, hoặc gộp `fields_used` của các proposal có pick là unit đang kept. Output: `progress` + `results` (dict phẳng #42).
   Route: `GET /sessions/{sid}/metrics` (mặc định, query `on`) và `POST` cùng path với body `{metrics, on}` (không ghi gì).
4. **CLI `spotcheck`**: gọi view `duplicate_pairs` + `get_frame`, ghép lưới ảnh theo mức khoảng cách vào `eval/results/images/`.
5. **Chia L7:** L7a = code (view, metric, command, route, CLI); L7b = người xem lưới, chốt ngưỡng → benchmark chính thức.
6. Chi tiết component (mặc định, không hỏi):
   - `keep_rate_by_recipe`: tính trên **mọi quyết định của phiên** (không phụ thuộc `unit_ids`, vì chấm tập kept thì luôn 100%). Mỗi unit đang kept/dropped
     tính theo quyết định keep/drop cuối cùng của nó, quy về recipe của proposal `proposal_n` (quyết định ngoài proposal không tính).
     `keep_rate_by_recipe` = keep rate chung; `recipe/<tên>` = từng recipe; recipe chưa có quyết định thì không có khoá. Không đọc field → luôn độc lập.
   - `embedding_map` / `timeline`: "picked" = proposal mới nhất, tham số `proposal_n` để xem vòng khác; trạng thái ưu tiên kept > dropped > picked > pool.
     `timeline`: tham số `video_id` (mặc định video đầu của pool), `series` = danh sách field scalar; `picks/kept/dropped` = index vào `t`.
   - `duplicate_pairs`: tập `on` ∈ pool / kept / proposal; `hist` = số cặp theo khoảng cách 0..64; `pairs` = lấy mẫu tối đa `per_distance` cặp
     ở mỗi khoảng cách trong `distances` (seed cố định → tái lập).
7. **Phát hiện khi chạy thử `spotcheck` (L7a):** pHash (imagehash) đặt bit = hệ số DCT > trung vị của 64 hệ số → mọi hash có **đúng 32 bit 1**
   (đã kiểm trên cache 1.200 + 120 + 86 frame) → khoảng cách Hamming giữa hai hash **luôn chẵn**. Ngưỡng lẻ không có nghĩa riêng
   (ngưỡng 5 hiện tại ≡ 4); L7b chỉ xét ngưỡng chẵn.

## 51. Ngưỡng ND-Rate chính thức = Hamming ≤ 8 (2026-09-30, người dùng chốt sau spot-check L7b)

- Spot-check `duplicate_pairs` trên pool 1.200 unit (`data/youtube5m`): d ≤ 6 là gần trùng; d = 8 lẫn (khoảng nửa cùng cảnh); d ≥ 10 khác thời điểm.
  Chi tiết + lưới ảnh: `eval/results/report.md` §7.1.
- `nd_rate_at_k.Params.threshold` mặc định 5 → **8** (CONTRACTS §9.10). pHash luôn chẵn nên chỉ ngưỡng chẵn có nghĩa.
- Số benchmark chính thức: `report.md` §7.2. `diversity` hơn `random` rõ; **ở K=200 không hơn `uniform`** (ở cả ngưỡng 6/8/10) → việc riêng sau MVP-core.

## 52. Chi tiết L8 — giao diện web (2026-09-30, mặc định theo lát L8, không đổi backend)

- Công nghệ: Vite + React 19 + TypeScript 7, **không thêm thư viện** (state bằng hook, fetch thuần, biểu đồ SVG tự vẽ). Không router:
  phiên đang mở nằm ở hash `#s=<sid>` (tải lại trang vẫn giữ phiên).
- Chỉnh recipe: form sinh từ `params_schema` + ô JSON config đầy đủ; luôn gửi `{recipe, config}` với `config.name = recipe` (#45).
  Tham số view: form sinh từ `describe_component(view, name).params_schema`, gửi qua query (mảng → khoá lặp).
- `proposal_n` / `shown_at` của quyết định: chỉ gán khi unit nằm trong proposal đang xem; `shown_at` = lúc lưới proposal hiện ra ở FE.
- `duplicate_pairs` tô đỏ cột ≤ ngưỡng mặc định của `nd_rate_at_k` (đọc từ `describe_component`, không hardcode).
- Dev proxy `/api`, `/media` → `RAV_API` (mặc định `http://127.0.0.1:8000`). BE phục vụ FE build: chưa làm (chỉ cần khi demo).
- **Hai lỗi BE phát hiện khi chạy thử, đã sửa (người dùng đồng ý):**
  1. Upload lại file đã có (khác tên cũng vậy) trả 400 `invalid_video` thay vì video cũ: `catalog()` khoá theo `video_id` nên bản chép trùng bị
     bản gốc che (tuỳ thứ tự tên file). Sửa: `upload_video` tìm bản vừa chép trên `p1.iter_videos` (mọi file, kể cả trùng). Test cũ chỉ qua nhờ thứ tự tên.
  2. Metric với field sai kiểu (vd `nd_rate_at_k` + `emb.dinov2_s`) trả 500: `nd_rate_at_k` kiểm field là hash `uint64`, `get_metrics` đổi
     `ValueError` → 422 `invalid_params` (giống `get_view`).

## 53. Giao diện mới thay giao diện L8, nối API (2026-10-02, người dùng chốt)

- Teammate đưa UI duyệt frame (làm trên AI Studio, nhánh `thien`) vào `src/web/` (commit `51b5e1e`, dựng trên L3, ghi là "quyết định #41"
  và LIMITS #14 — trùng số với log hiện hành; nội dung đó được thay bằng mục này). UI đó chạy riêng trên trình duyệt: dữ liệu giả, cắt frame
  và chấm điểm trên trình duyệt, phiên trong IndexedDB.
- Người dùng chốt: **dùng giao diện mới thay giao diện L8** (#52) và **nối backend**; giữ bố cục / theme / phím tắt, thay toàn bộ lõi dữ liệu
  giả bằng API v1 (architecture §3, §5). Bỏ: dữ liệu giả lập, phiên giả lập, cắt frame trên trình duyệt, IndexedDB, thanh trượt trọng số giả.
- **Số/thuộc tính UI cần mà BE không cung cấp → hiện `-/-` màu đỏ** (tooltip "chưa hỗ trợ"), không bịa: tốc độ km/h, telemetry,
  DINOv2 min-dist, coverage entropy, tiết kiệm chi phí, cụm. **Chủ đề** (10 chủ đề ADAS, quota, tìm theo tiêu chí) để sau — Hướng tới,
  hướng làm ở CONTRACTS §8 "Chọn theo mô tả text"; phải chốt CONTRACTS trước khi làm.
- Thư viện FE: thêm `tailwindcss` (`@tailwindcss/vite`) và `lucide-react` (thay ý "không thêm thư viện" của #52). Vẫn fetch thuần, SVG tự vẽ.
- Luồng duyệt theo BE: mỗi thao tác Giữ/Bỏ gửi `PATCH state` ngay (giữ đúng `decided_at`, `shown_at`, `proposal_n`); Ctrl+Z = gửi lại
  trạng thái trước (`keep` / `drop` / `clear`). "Duyệt batch" (Enter) = `POST proposals` với recipe đang chọn; frame chưa duyệt vẫn là
  candidate (CONTRACTS §9.3), UI hỏi lại trước. Vượt K do BE chặn (`target_k_reached`), UI hiện lỗi. "Đợt" = `proposal.n`.
- Giữ phạm vi L8 (#52): chọn recipe + form `params_schema` + JSON config (#45), 3 renderer theo `view.type` + tham số view, lịch sử proposal,
  metric mặc định + tự chọn, export (command `export`, CSV + ảnh ở BE). Xuất JSON / CSV / Markdown phía trình duyệt của UI teammate bỏ
  (exporter là component BE). Xoá phiên: BE chưa có command → không có nút. Tạo phiên: chọn video có sẵn và/hoặc tải lên (pool nhiều
  video, BE hỗ trợ); cấu hình bước cắt frame của UI cũ bỏ (decoder BE cố định theo spec).
- `.gitignore` không cần ngoại lệ `!src/web/src/data/` (thư mục dữ liệu giả đã xoá).
- **Tiến độ P1 trên UI: Hướng tới** (người dùng chốt ghi chú, chưa làm). `POST /sessions` chạy P1 đồng bộ (architecture §5.1) nên UI
  chỉ có spinner, không có %; video dài (12 phút ≈ 2.900 unit) chờ nhiều phút. Muốn có % phải đổi thiết kế: P1 chạy nền + endpoint tiến độ —
  ghi ở architecture §10, chốt CONTRACTS/architecture trước khi làm.
- Chạy thử (2026-10-02): video YouTube 12 phút (`epbi6ucVgic`, 2.916 unit) — upload 6,6 s, P1 ~13 phút, sau đó mở phiên / đề xuất ~0,9 s,
  clip ±3 s phát được. **Vấn đề chưa rõ nguyên nhân:** lần `POST /sessions` 13 phút đó BE đã tạo phiên nhưng trình duyệt (Playwright) không nhận
  phản hồi (uvicorn không ghi dòng access) → UI kẹt spinner; tải lại trang và mở phiên ở cột trái thì dùng bình thường. Đã loại trừ proxy Vite
  cắt ở 5 phút (request 330 s qua proxy vẫn 200). Hướng xử lý gốc: job nền + tiến độ (dòng trên).

## 54. Lưu mọi lần xuất + màn "Đã xuất" + tải zip (2026-10-02, người dùng chốt)

- Trước: `export` ghi `output/exports/<sid>/` và ghi đè lần trước; chỉ trả đường dẫn trên server (LIMITS #8), trình duyệt không lấy được file.
- **Giữ mọi lần xuất**: mỗi lần một thư mục `output/exports/<sid>/<export_id>/` (`export_id` = thời điểm UTC `YYYYMMDDTHHMMSSmmmZ`),
  kèm `meta.json` (session_id, format, n_frames, created_at). Không dùng bảng DB: liệt kê bằng cách đọc các `meta.json`.
- Command mới `list_exports(session_id?)` (`GET /api/v1/exports`) và `get_export_file(session_id, export_id)` (route
  `GET /api/v1/exports/{sid}/{export_id}/download` trả `.zip` CSV + ảnh; zip tạo lần đầu cạnh thư mục rồi dùng lại). `export` trả `ExportDTO`.
- UI: màn **"Đã xuất"** chung cho mọi phiên (mở từ cột Phiên làm việc, hash `#exports`), lọc theo phiên, nút tải `.zip`;
  màn Xuất dữ liệu của phiên có nút tải ngay bản vừa xuất + các lần xuất của phiên đó.
- id kiểm theo mẫu (hex / thời điểm) trước khi ghép đường dẫn — không cho `..`. LIMITS #8 cập nhật (đĩa đầy dần, không kiểm quyền).

## 55. Giao diện gọn hơn; quản lý quyền là bắt buộc sau MVP (2026-10-02, người dùng chốt sau góp ý mentor)

- Mentor chấp nhận trọng tâm Data Curation Copilot (`research-pack/02_problem_understanding/04_data_curation_focus.md`) và góp ý hai điểm.
- **UI gọn, ít chữ, ít viền — làm ngay (trước hạn nộp MVP):** card frame chỉ còn ảnh, thời điểm, trạng thái; điểm / video / breakdown
  vào tooltip (và Inspect Math); sidebar một panel gọn (mục tiêu, tiến độ K, nút đợt mới, bản đồ nhỏ + 2 chỉ số backend có), chỉnh recipe
  thành phần "nâng cao"; phân vùng bằng nền + khoảng trắng thay khung. **Số backend chưa có không hiện nữa** (thay quy ước `-/-` đỏ của #53,
  khớp tài liệu focus §6: không hiện tiền tiết kiệm, cụm, telemetry khi chưa có dữ liệu). Không đổi API.
- **Quản lý quyền kiểu Google Drive — must-have sau MVP (3 tuần dev tiếp):** Project chứa dataset + phiên, vai trò Owner / Curator / Viewer,
  nhiều người cùng project; bước sau nhiều người cùng một phiên (chia đợt, cập nhật trực tiếp — CONTRACTS §11.3). Khớp yêu cầu "ít nhất hai
  vai trò" của đề gốc. Chưa thiết kế chi tiết: cấp gán quyền (đề xuất: project), cách đăng nhập — chốt và ghi CONTRACTS / architecture trước khi làm.

## 56. Tiền xử lý (P1) chạy nền có tiến độ; nút Thêm video; thêm video vào phiên có sẵn (2026-10-02, người dùng chốt)

- Người dùng yêu cầu: upload dataset thì thấy trạng thái tiền xử lý; nút "Thêm video" riêng; thêm video vào phiên đang có hoặc chỉ vào kho chung.
- **P1 nền:** `upload_video` xếp P1 vào hàng đợi một worker (một thread daemon đọc `queue.Queue` trong tiến trình API — máy dev ít RAM; tắt server không phải chờ P1). Trạng thái job
  (`queued` / `running` / `done` / `error`, field đang tính, `done` / `total` frame) nằm trong RAM, trả kèm `VideoDTO.prepare`; FE poll `GET /videos` ~2 s.
  Tiến độ = số lần `ctx.pixels()` khi execution tính một field (Context nhận `on_progress` tuỳ chọn) → không phải sửa từng feature.
  `POST /videos/{id}/prepare` (`queue_prepare`) để chạy lại sau khi BE khởi động lại / lỗi. `prepare_video` (đồng bộ) giữ cho CLI. LIMITS #15.
  `App.auto_prepare` (env `RAV_AUTO_PREPARE`, mặc định bật) để test không vô tình chạy DINOv2.
- **Thêm video vào phiên:** thay "pool cố định khi tạo" bằng "pool chỉ được thêm video đã chuẩn bị" (CONTRACTS §7). Command `add_videos_to_session`,
  route `POST /sessions/{sid}/videos`. Video đang chuẩn bị → 409 `video_preparing` (tạo phiên cũng vậy). Field theo pool (PCA) tự tính lại theo `pool_hash` mới.
- **UI:** một hộp "Thêm video" dùng chung: danh sách video kèm trạng thái / thanh tiến độ / nút Chuẩn bị, vùng tải lên; mở từ cột trái (vào kho chung)
  hoặc từ phiên (chọn video đã sẵn sàng → thêm vào phiên). Tạo phiên chỉ chọn được video đã chuẩn bị. Thay mục "Hướng tới" tiến độ P1 của #53.

## 57. Thiết kế UI có spec riêng; sidebar kiểu web chat; màn Kho video; refactor FE (2026-10-02, người dùng giao quyền quyết định UI)

- Người dùng: sidebar trái nên như web chat (trên: điều hướng màn global, dưới: danh sách phiên); cần màn quản lý video kiểu Google Drive /
  file explorer có nút Thêm video; refactor FE và viết docs. Người dùng không có kinh nghiệm UI → **giao phần lớn quyết định UI cho agent**.
- Thêm `docs/design/UI_DESIGN.md` (cấp 2, cạnh CONTRACTS): nguyên tắc, bố cục, route hash (`#videos` mặc định, `#exports`, `#s=<sid>&t=<tab>`),
  màn hình, token, thành phần dùng chung `src/web/src/ui/`, cách giữ state (`useSessionData`, `useHotkeys`).
- **Không thêm thư viện** quản lý state / data (TanStack Query…): dự án nhỏ; sửa việc tải lại thừa bằng cập nhật từ phản hồi `PATCH`, bản đồ chỉ tải
  khi pool / proposals đổi, chỉ số tải trễ.
- API thêm `VideoDTO.thumb_url` (frame giữa video, 320 px) cho thẻ ở Kho video. Xoá / đổi tên video chưa làm (cùng đợt quản lý quyền).

## 58. Màn "Phiên mới" thay hộp thoại; chọn nhiều ở Kho video; thêm video vào phiên dễ thấy hơn (2026-10-02, người dùng chốt)

- Hộp thoại Tạo phiên chật và không cần thiết → **màn riêng `#new`** (UI_DESIGN §3): trái chọn video (lưới thumbnail, tải lên tại chỗ), phải mục tiêu
  (mẫu bấm điền) / K (gợi ý theo số frame ước tính = thời lượng × 4 fps) / tên (tự điền) / tóm tắt.
- **"Tạo & bắt đầu duyệt" tự đề xuất đợt đầu** bằng recipe mặc định (`propose` không gửi `config`) rồi vào Studio — người dùng chốt; lỗi đề xuất thì vẫn
  vào Studio, bấm Đề xuất lại.
- **Kho video chọn nhiều** kiểu Drive → thanh hành động: Tạo phiên mới (`#new&v=…`), Thêm vào phiên… (`add_videos_to_session`), Bỏ chọn.
- Thêm video vào phiên (đã có từ #56, nút "Video" ở thanh Studio khó thấy) → nút "N video · + Thêm" trên thanh tiêu đề phiên, thấy ở mọi tab; bỏ nút
  "Video" khỏi thanh Studio. Không đổi API.

## 59. Đủ chỉ số cho UI trước giai đoạn thuật toán (2026-10-02, người dùng chốt)

- **Nhóm 1 — tính từ dữ liệu đang có:** metric `review_time` (từ `shown_at` / `decided_at` của log: theo từng đợt = (quyết định cuối − lúc hiện) / số quyết
  định; trả trung vị giây / frame, số frame / phút, số đợt — nền cho kiểm chứng A), `min_dist` (trung bình 1 − max cos DINOv2 tới frame khác trong tập;
  không độc lập với recipe diversity → P3 tự gắn cờ), `temporal_coverage` (tỉ lệ khoảng `bin_s` giây của mỗi video có frame trong tập),
  `cluster_coverage` (feature `cluster.kmeans` k-means trên DINOv2 cả pool; tỉ lệ cụm được phủ + entropy chuẩn hoá — số mô tả, cụm không có nghĩa
  nghiệp vụ). Thêm vào metric mặc định của `get_metrics`. `VideoDTO` thêm width / height / fps / size_bytes / added_at.
- **Nhóm 2 — hợp đồng bằng chứng, dữ liệu đến sau (giai đoạn thuật toán):** CONTRACTS §8.0 — field `det.<model>` (khung detector), `tag.<nhóm>` (nhãn cảnh),
  `Source.telemetry(video)` (`local_folder` đọc `<video>.telemetry.json`); command `get_evidence` chỉ đọc cache (`Context.cached`), route
  `POST /sessions/{sid}/evidence`. UI có sẵn chỗ hiện, không có dữ liệu thì ẩn. Nhãn ẩn của dataset không đi qua đây.
- **Nhóm 3 — bỏ:** tiền tiết kiệm gán nhãn, cụm / độ phủ suy từ bản đồ 2D (tài liệu focus §6).

## 60. Lộ trình lên nhiều người dùng / deploy: thay hạ tầng theo lát, không viết lại (2026-10-02, ghi theo yêu cầu người dùng)

- Người dùng hỏi: sau MVP phải deploy, thêm đăng nhập / đăng ký, quản lý quyền, dịch vụ ngoài — có phải đập đi xây lại? Trả lời: không; FE chỉ gọi
  HTTP qua `api.ts`, kiểm quyền đặt ở command layer, dịch vụ ngoài nằm sau BE. Phần phải thay là hạ tầng BE (SQLite → Postgres, đĩa → object storage,
  P1 trong API → worker + hàng đợi bền) và vài chỗ FE (router, auth, cache server-state, realtime).
- Ghi lộ trình 4 lát ở `design/LOCAL_SINGLE_USER_LIMITS.md` ("Lộ trình lên nhiều người dùng / deploy thật"): đăng nhập + quyền → dữ liệu bền → tính toán
  tách khỏi API → nhiều người cùng phiên; mỗi lát gỡ các dòng giới hạn tương ứng. FE: `UI_DESIGN.md` §8. Đăng nhập dùng dịch vụ có sẵn, không tự viết.
- Chưa thiết kế chi tiết; lát 1 trùng đợt quản lý quyền (#55) — chốt CONTRACTS / architecture trước khi làm.

## 61. Studio ưu tiên ra quyết định; lưu ngay có trạng thái lưu; batch là đơn vị giải thích (2026-10-02, người dùng chốt)

- **Lưu ngay (giải FR-02 của tài liệu focus theo hướng (a)):** mỗi Giữ / Bỏ tự lưu; ô đổi trạng thái tức thì, hiện đang lưu / lưu lỗi + Thử lại;
  có hoàn tác; không toast từng quyết định. Enter / "Đợt mới" chỉ yêu cầu đề xuất tiếp — không chốt, không lưu batch, **bỏ hộp hỏi lại**
  khi còn frame chưa duyệt (thay bằng một dòng ghi chú); frame chưa duyệt không tự thành Bỏ.
- **Batch là đơn vị giải thích:** khối giải thích ngắn trên lưới trả lời (a) vì sao đợt này cho mục tiêu phiên, (b) bổ sung gì so với tập đã giữ —
  command mới `explain_proposal` (CONTRACTS §11.2), `Objective.uses_goal` để nói thẳng khi recipe chưa dùng mục tiêu. Card bỏ dải điểm số;
  điểm từng frame + frame đã giữ gần nhất nằm ở cửa sổ chi tiết. Nhận xét AI (sau MVP) là khối riêng.
- **Studio:** bỏ thời gian duyệt khỏi panel (về Phân tích, chỉ giây / frame); bản đồ, chỉ số mô tả, recipe vào khối gập. Phân tích xếp theo focus:
  Đánh giá (tốc độ, chất lượng quyết định — chưa đo, uplift — đo ngoài) → số mô tả → Kỹ thuật. Không thêm bước thiết lập thí nghiệm vào luồng tạo
  phiên; không ép lý do Bỏ (chưa làm).
- **Nhất quán:** M chỉ bật khung detector (điểm chi tiết rời khỏi card); U đổi tên "Huỷ quyết định" (khác D "Bỏ"); focus = viền ngoài, Giữ = viền
  trong; ô ≥ 220 px, lưới tự giảm cột; thêm video / chỉ số không xáo trộn đợt đang duyệt.

## 62. Giải thích đợt ẩn sau nút "?" (2026-10-03)

- Người dùng: khối giải thích luôn hiện trên lưới là "wall of text", làm mất tập trung khi duyệt → thu về nút **?** cạnh "Đợt n", bấm mới
  mở khung nổi (nội dung giữ như #61, viết gọn hơn). Chỉ khi đợt mới lặp lại frame của đợt trước, thanh công cụ hiện chữ nhỏ "lặp lại m".

## 63. Goal → structure qua LLM; chốt phạm vi chat và goal cố định trong phiên (2026-10-03, người dùng chốt)

- Luồng: người dùng nhập goal tự do → LLM (gọi **API**) suy intent thành structure + alert → người dùng sửa tay hoặc chat → xác nhận →
  thuật toán dùng structure (mặt semantic `find`/`avoid` + mặt symbolic). Chi tiết và phần còn mở: `docs/archived/2026-10-13/docs/backlog/goal_intent_flow.md`.
- **Lưu cả câu goal gốc lẫn structure.** LLM lỗi / chậm không chặn duyệt.
- **Không sửa goal giữa phiên** trong MVP (giữ cache, thuật toán, tái lập proposal không vỡ); ghi để cân nhắc nâng cấp sau.
- **Chat / agent chỉ sửa goal**, không gọi command khác.
- **Mục tiêu + mô tả cho từng đợt: implement luôn** (hình dạng contract còn chốt).
- Cách đo goal có ích: người dùng research thêm. Chưa sửa CONTRACTS — sẽ sửa trước khi code (AGENTS.md luật 1).

## 64. Kế hoạch 4 giai đoạn; giai đoạn 1 dataset + snapshot; lưu trữ theo tham chiếu (2026-10-07, người dùng chốt)

- Luồng MVP: agent nhận yêu cầu → người dùng tìm và chọn frame → agent sinh report → kỹ sư duyệt và export dataset kèm report. Bốn giai đoạn:
  (1) dataset tham chiếu + snapshot, (2) goal + tìm kiếm liên quan, (3) evidence + report do LLM viết, (4) duyệt + bàn giao (≥ 2 vai trò, PII — yêu cầu đề RAV-26).
  Training, perception inference, MLflow, DVC ngoài luồng MVP. Chi tiết: `docs/backlog/MVP_GAP_MAPPING.md` §4–5, `docs/archived/2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md`.
- Nguyên tắc: ảnh độc lập không giả làm video (không timestamp giả); snapshot bất biến (collection đổi → snapshot mới, có `members_hash` + `context_hash`);
  migration giữ dữ liệu, không xoá DB; dataset không có cờ "đã dùng train"; không nới ngưỡng tới 0 để lấp batch; metric phải ghi tập tham chiếu,
  cách tính, phiên bản dữ liệu / embedding, tham số, giới hạn; tách benchmark proposal với đánh giá collection sau human review.
- **Lưu trữ theo tham chiếu:** dataset / snapshot chỉ lưu manifest tới raw (video + chỉ số frame + cấu hình decoder; ảnh + hash). Không materialize ảnh khi
  keep / tạo snapshot; chỉ khi tải về mới đọc / decode, đóng ZIP tạm rồi dọn. Thumbnail là cache. Raw đổi → báo nguồn không khả dụng, không sửa manifest cũ.
- Đổi `Unit` / cache là quyết định kỹ thuật của agent, điều kiện giữ id + cache video cũ.
- Thêm CLIP (image trên Kaggle qua `kaggle_extend_api`, text local, cùng checkpoint) và smoke test trên subset BDD100K ≤ 200 ảnh; Kaggle là tạm thời, không
  âm thầm fallback CPU. Q1 (pool có nhận ảnh) còn mở.

## 65. Manifest nhãn xung đột và pool chỉ video (2026-10-07, người dùng chốt)

- Import S4: manifest lưu đầy đủ đường dẫn, trạng thái và hash nhãn của mọi bản trong nhóm ảnh trùng byte có nhãn xung đột.
  Hash từ manifest đầy đủ (không cắt theo giới hạn báo cáo); nhãn của bất kỳ bản nào đổi → version mới, version cũ bất biến.
  Không tự chọn nhãn của một bản để dùng. Schema v3 lưu JSON manifest trong dataset_version để giữ đầy đủ provenance xung đột.
- Q1 đã chốt: ảnh dataset chỉ làm tham chiếu trong MVP; pool phiên vẫn chỉ video, không đề xuất/keep/drop ảnh độc lập.

## 66. Tiếp tục S5–S7 trong phiên và kiểm thử FE (2026-10-07, người dùng yêu cầu)

- Người dùng yêu cầu tự test, cập nhật FE và làm tiếp: triển khai các dependency S5–S6 rồi UI S7 trong cùng phiên,
  kiểm từng lát và kiểm luồng FE trước khi báo xong. Đây là ngoại lệ được người dùng cho phép với luật một phiên/một lát.
- Theo thiết kế GĐ1 đã chốt: dataset chỉ tham chiếu, chưa ảnh hưởng propose; UI snapshot tách khỏi export kept hiện có tới S8.

## 67. Tiếp tục S8 sau khi cập nhật FE (2026-10-07)

- Theo yêu cầu “cập nhật FE và làm tiếp luôn”, tiếp tục S8 trong cùng phiên sau S5–S7; giữ phạm vi export snapshot đã chốt ở #64/PHASE1 §12.
- Giữ export kept cũ, thêm tải theo snapshot bất biến; ZIP tạm được dọn, không materialize ảnh lâu dài. Pool vẫn chỉ video.
- Đã bật FE chính cổng 3000 + API 8000 và kiểm tra trực tiếp 6 video/15 phiên cũ. Cổng 3001/8001 dành dữ liệu smoke riêng.
- Strict reader đếm index theo thứ tự giải mã thật, kiểm VFR và chuyển qua lại với read UI; không dùng timestamp × fps để xác nhận frame_idx. ID/cache video cũ giữ nguyên.

## 68. CLIP dùng được trong cấu hình đề xuất trên FE (2026-10-07, người dùng yêu cầu)

- Người dùng yêu cầu làm luôn CLIP, text_match và recipe trên FE; thực hiện S9 + S9b + S10 trong cùng phiên để có luồng sử dụng được, không chỉ feature backend.
- Checkpoint nhỏ đã có trên máy: openai/clip-vit-base-patch32, pin revision 3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268, transformers 4.57.6. Không quantize; ảnh/text cùng preprocessing/checkpoint, L2. Ngưỡng text_match heuristic 0.2, negative thắng thì loại, human review vẫn quyết định.
- Câu tìm/loại trừ nằm trong config proposal và được giải thích từ snapshot config; goal tự do vẫn cố định, không giả lập LLM structure/dịch tự động (#63 chưa có schema chốt). CLIP recipe không nới NMS/min_dist để lấp đợt. Dataset tham chiếu chưa tham gia novelty; không claim hiệu quả nghiệp vụ/coverage.
- Image prepare nền mặc định Kaggle, giữ P1/cache DINO cũ và recipe diversity. Local chỉ chọn rõ cho smoke; không fallback CPU khi Kaggle lỗi.
- Người dùng nhắc auth phải từ .env repo và kaggle_extend chỉ tham khảo: adapter repo dùng kagglesdk chính thức, token + RAV_KAGGLE_KERNEL đọc trực tiếp .env; không dùng auth/account/notebook từ nơi khác. Notebook giahuytran1 trong báo cáo package không phải auth người dùng, không được chọn hay chạy job; không tự điền .env bằng notebook người khác.
- Người dùng hỏi tự tạo notebook bằng credential: cho phép tự tạo notebook private riêng bằng token .env, RAV_KAGGLE_KERNEL chỉ override tuỳ chọn. Introspect account thật, quick-save, lưu identity worker dưới output và chỉ reuse khi account khớp. Không yêu cầu người dùng tự điền slug nếu token đủ quyền. Token thiếu quyền mới báo cần can thiệp.

## 69. Dataset và YOLO tham chiếu là dữ liệu train và checkpoint cần cải thiện (2026-10-07, người dùng làm rõ)

- Dataset tham chiếu là dữ liệu đã dùng train/finetune chính checkpoint YOLO tham chiếu. Không mặc định coi đó là validation/test độc lập.
- YOLO tham chiếu là model hiện có cần cải thiện; người dùng cho biết nó perform kém trên dữ liệu mới chưa gán nhãn. Không đồng nhất tên này với teacher model lớn hơn dùng pseudo-label/disagreement trong các phương án cũ.
- Pool là dữ liệu mới chưa gán nhãn. Mục tiêu curation là chọn mẫu bổ sung phù hợp để gán nhãn rồi train/finetune lại model đó; dataset cũ làm mốc dữ liệu model đã học, model hiện tại cung cấp prediction trên pool.
- Nạp/chọn checkpoint YOLO tương ứng và xác định liên hệ với dataset train là yêu cầu của luồng, còn chưa triển khai. Chưa chốt schema/API/inference/tiêu chí chọn; không tự thêm teacher, pseudo-label, training trong app hay dùng nhãn ẩn của pool.
- Nhận định perform kém trên pool là thông tin người dùng cung cấp, chưa phải metric hệ thống đo. Prediction/confidence và khoảng cách embedding chỉ là tín hiệu chọn ứng viên; đo lỗi/AP/mAP trên dữ liệu mới cần nhãn và tập đánh giá phù hợp.
- Code GĐ1/S9 hiện chỉ import/cache/lưu reference và tìm theo câu + novelty với kept; chưa nối dataset train hoặc YOLO vào thuật toán. Không báo luồng mục tiêu đã hoàn thành chỉ vì CLIP chạy được.

## 70. Nạp mô hình tham chiếu trên FE (2026-10-07, người dùng yêu cầu)

- Người dùng yêu cầu cho upload model tham chiếu. Lát M1 nạp/list/verify .pt, metadata theo hash, chọn model + dataset train trong phiên, giữ reference bất biến và snapshot. Lựa chọn kỹ thuật theo CONTRACTS §16; chưa thiết kế/chạy inference hay đưa model vào scoring ở lát upload này.
- Không nạp pickle để kiểm file upload: chỉ kiểm PyTorch ZIP và checksum, ghi rõ container_only/chưa inference. Model/dataset train do người dùng chọn và khai báo tương ứng, không suy rằng dataset là validation/test hoặc model là teacher.

## 71. Xem kết quả YOLO trên riêng đợt đề xuất (2026-10-07, người dùng yêu cầu)

- Người dùng yêu cầu gắn model và có option chỉ chạy YOLO trên N ảnh của đợt đã gợi ý (ví dụ 16), để xem kết quả, không chạy toàn dataset. M2 theo CONTRACTS §17: explicit run, box/nhãn/confidence thật, cache theo đợt/model/nguồn, giữ nguyên scoring và quyết định.
- Chọn model xem thử trong Studio độc lập với reference model/dataset train bất biến của phiên. CPU local cho batch nhỏ, không cần Kaggle. Chưa có nhãn pool nên không tính accuracy/AP/mAP hoặc coi prediction là lỗi model.

## 72. Mở rộng hạ tầng trước thuật toán v2 (2026-10-07, người dùng đồng ý)

- Người dùng đồng ý triển khai đề xuất: Context reference tách pool, runtime feature binding, requested cache, proposal hai bước chạy nền và evidence primitives. Component không DB/HTTP/job; service không nhánh theo tên thuật toán. Chi tiết CONTRACTS §18.
- V2 dùng dataset train + kept để tính mức bổ sung, chính YOLO phiên chạy original/flip để tìm thiếu ổn định, thêm phần khám phá; không teacher. Triển khai cùng lát V2 với hạ tầng, recipe cũ tương thích. Weights/quota là heuristic, chưa claim error probability hoặc uplift.

## 73. Dọn dung lượng phát sinh trên ổ C (2026-10-07, người dùng yêu cầu)

- Người dùng yêu cầu kiểm tra và dọn file/cache phát sinh trước khi tiếp tục. CLIP tải hai container trọng số ≈1,13 GiB vào cache toàn máy; chuyển checkpoint revision pin sang data/model-cache trên ổ repo và bỏ bản trùng. Cache tải gói pip có thể xoá, không gỡ thư viện đã cài.
- CLIP local dùng RAV_CLIP_CACHE_DIR, mặc định dưới data/, tắt auto-conversion sang safetensors revision khác. Không đổi embedding/FieldKey hoặc Kaggle auth; giữ model/cache khác của người dùng.

## 74. Ưu tiên lưu cache và file tạm trên D (2026-10-07, người dùng yêu cầu)

- Người dùng yêu cầu kiểm tra vì C liên tục gần hết và lưu trên D nếu phát sinh file. Đo riêng cache/output và file bộ nhớ ảo, không quy mọi biến động cho output dự án.
- Chuyển cache HF hub/Torch/npm/pip sang data/system-cache trên ổ repo, kiểm đầy đủ SHA-256 trước khi bỏ bản C và dùng junction giữ đường cũ; không loại model/cache của app khác. Log xet đang mở không di chuyển trong khi backend chạy. Cấu hình terminal dự án bằng script PowerShell để cache/temp của lệnh mới ở data/output.
- Không thay thiết kế thuật toán/API, credential hoặc bộ nhớ ảo Windows. Pagefile trên C vẫn có thể lớn khi thiếu RAM; chuyển cache không được báo là đã giải quyết mọi nguyên nhân C đầy.

## 75. S3 lưu lâu dài và Supabase lưu job Kaggle (2026-10-08, người dùng chốt)

- Người dùng yêu cầu wire upload → S3 → Kaggle tải raw/tính embedding → xoá raw đã tải trên Kaggle → S3 result → backend kiểm và lưu embedding; không xoá raw S3 sau job. Supabase lưu tiến trình job; credential chỉ lấy .env repo.
- Backend cấp presigned URL theo object/job, progress/result qua HTTPS 443; không đưa AWS/Supabase key lên Kaggle, không mở inbound port máy local. Durable job + lease + session ownership cho phục hồi sau restart; result kiểm strict manifest/pixel/IDs/hash/shape/L2 trước atomic cache.
- Lát C1 giữ bản làm việc local D và SQLite nghiệp vụ hiện có; Supabase là job database, S3 giữ raw/result. Không claim đã migrate toàn session/feedback/catalog, hỗ trợ nhiều tenant hoặc resume vector từng batch sau mất GPU. Hợp đồng §19 và architecture cloud transport cập nhật trước code.

## 76. Video lưu S3 trước; DINO và CLIP chỉ chạy explicit (2026-10-08)

Người dùng yêu cầu upload chỉ lưu video, hiển thị từng feature đã chạy chưa, nút gửi Kaggle và theo dõi khi cần. C2 bỏ auto prepare video; giữ hai endpoint/status riêng, DINO+pHash cùng job GPU, CLIP job GPU độc lập, cache cũ tương thích. Backend giữ credential/orchestration; scoped URLs qua S3, Supabase giữ job như #75. DINO nguồn/trọng số/preprocessing được đóng băng để không thay thuật toán. CONTRACTS §20 + architecture là thiết kế trước code.

## 77. Chốt push hiện trạng; thiết kế đăng nhập làm sau (2026-10-09)

Người dùng yêu cầu commit và push phần đã hoàn tất. Luồng đăng nhập cần thiết kế lại và người dùng sẽ làm sau; chưa triển khai đăng nhập trong lát hiện tại. Credential và orchestration cloud tiếp tục chỉ ở backend theo #75–76.

## 78. PII mặt/biển số, web cloud và quota không payment (2026-10-09, người dùng làm rõ)

- Phạm vi PII: detect mặt và biển số rồi che; không cần OCR/NER. Checkpoint, chất lượng detector, preview/raw-access vẫn cần thiết kế và benchmark.
- Hướng hosting: web trên Render/Railway hoặc cloud phù hợp, phần AI tách riêng. Cần kiểm bandwidth và đường truyền ảnh để cân nhắc provider; chưa chốt provider hay migrate storage.
- Tài khoản mới phải có quota vì GPU Kaggle dùng chung và CPU yếu. Không làm cơ chế nạp tiền/payment; người vận hành có thể cấp thêm thủ công trong database hoặc dùng reset sau 3–5 giờ.
- Reset 4 giờ, CPU/GPU credit riêng, reserve/settle, manual bonus và trần toàn hệ thống là đề xuất trong `docs/backlog/BANDWIDTH_AND_QUOTA_PLAN.md`, chưa phải thiết kế đã duyệt. Reset quota user không tăng quota GPU thật của Kaggle.
- Tiếp tục chỉ lập/cập nhật plan; chưa sửa code, CONTRACTS/architecture, database hay cấu hình triển khai. Những lựa chọn còn mở phải được duyệt trước implementation.

## 79. Scope deployment pilot 2–3 người và PII khi export (2026-10-09, người dùng chốt)

- Pilot 2–3 người; operator tự tạo/cấp tài khoản, không public signup/nút tạo tài khoản. Upload, phiên và kết quả cá nhân mặc định private; người dùng tự review rồi export dataset kèm report cho người khác review ngoài app. Mời reviewer trong app làm sau.
- Bản đầu user chỉ upload video. Seed subset nhỏ BDD100K **cả ảnh và video**, cộng dữ liệu local chọn làm demo; nguồn hệ thống/demo chỉ đọc, user không có quyền xóa. Phiên và kết quả tạo trên demo vẫn thuộc riêng user. Dataset/model upload của user giữ Hướng tới trên bản deploy, không xóa capability local.
- **PII chỉ detect/che mặt và biển số khi xuất dataset**, không OCR/NER hoặc che lúc upload. Preview raw kiểm quyền; YOLO nano/text trong curation, DINO+pHash/CLIP image preparation vẫn explicit. Thời điểm export thay đề xuất che trước preview trong PL2/PL3.
- Hướng web/API Render Free Web, frontend static; ping mỗi 10 phút từ Contabo; Python AI worker và điều phối GPU Kaggle trên Contabo dự kiến 3 vCPU/8 GB/200 Mbit/s, không thêm AI API public. Giữ React/Vite + FastAPI nếu phù hợp; S3 private và direct browser transfer theo quyền API cấp.
- Quota theo đề xuất đã nhận: CPU/GPU riêng, base reset 4 giờ, top-up thủ công DB, không payment, có global Kaggle budget. Lượng credit/caps cụ thể và headroom phải đo; reset user không tăng entitlement provider.
- Ngân sách phát sinh ngoài VPS khoảng tối đa 2 USD/tháng; admission limits và monitoring, không tự xóa raw/demo để giảm bill. Chỉ chủ thể có quyền xóa được yêu cầu xóa, bảo vệ nguồn còn snapshot/job tham chiếu; không cascade phá manifest bất biến.
- Người dùng yêu cầu viết spec đầy đủ để duyệt, **chỉ tài liệu**, không code/deploy/cloud job/push. `docs/design/DEPLOYMENT_PILOT_SPEC.md` phân biệt yêu cầu trên với đề xuất schema/API/auth provider/quota values/PII confirmation/delete retirement/backup; thiết kế chi tiết phải duyệt tại D0 trước implementation.

## 80. Chốt host web Render và ứng dụng AI Contabo (2026-10-09, người dùng xác nhận)

- Web host trên Render: frontend React/Vite bằng **Render Static Site**, Web API FastAPI nhẹ bằng **Render Free Web Service** theo target #79. Không coi frontend provider còn là lựa chọn mở; đổi provider cần quyết định mới.
- Ứng dụng AI chạy trên **Contabo** dưới dạng Python worker riêng, xử lý YOLO/text/scoring và PII/export/report, điều phối GPU Kaggle như #79. Không cần một AI API public riêng. VPS dự kiến **3 vCPU/8 GB RAM/200 Mbit/s**; thông số và capacity phải kiểm/benchmark, chưa là cam kết hiệu năng.
- Theo phạm vi evaluation người dùng vừa giao Nam, bộ phát triển thuật toán nằm folder riêng và **chỉ chạy local**, không đưa lên Render/Contabo. Scoring/metric/report phục vụ curation của app vẫn chạy ở runtime đã phân công.
- Lượt này cập nhật tài liệu/spec target; không provision cloud, sửa code/runtime, chạy AI/cloud job hoặc push. D0–D8 và các gate deployment vẫn chưa hoàn thành.

## 81. Chốt auth, xác nhận export, xóa nguồn và tài liệu thực thi deployment (2026-10-09)

- Người dùng chốt **Supabase email/password**, operator tạo/cấp và reset mật khẩu; không public signup.
- Export: chủ phiên **xem ảnh đã che và report, xác nhận rồi mới tải ZIP**. PII vẫn chỉ lúc export theo #79; chi tiết DTO/hash/UI và quality thresholds chốt D0/D1, không coi xác nhận người dùng thay kiểm chất lượng detector.
- Xóa: nguồn còn snapshot/phiên/job tham chiếu phải **chặn và báo nơi đang dùng**; không làm release-pin/retire để giải phóng các tham chiếu trong pilot. Nguồn private không còn được dùng vẫn có thể yêu cầu xóa; system/demo protected.
- Dữ liệu demo phải có **phiên và lịch sử mẫu** để mọi tài khoản xem, hiểu cách dùng. Phiên mẫu dùng chung chỉ đọc; quản lý phiên/lịch sử/proposal/snapshot/job/export của user vẫn riêng. Bắt đầu từ mẫu tạo bản riêng có provenance, không sửa mẫu hoặc dùng chung state giữa users.
- Chưa có subset BDD100K và checkpoint YOLO nano; người dùng muốn agent tìm sau khi hạ tầng deploy được. Bootstrap để đo trước; BDD/model provenance và quality gates chưa đạt vẫn phải hoàn thiện trước mở đủ pilot, không giả quan hệ train/reference.
- Người dùng xác nhận có tài nguyên host, yêu cầu docs đủ cụ thể để coding agent yếu như Antigravity thực thi và chia việc subagent song song. PL6 tạo task/dependency/file ownership/DTO/transaction/test/gate drafts; mỗi phiên vẫn một lát, parent tích hợp và commit. Không tự đổi signature/thuật toán hoặc coi D0 đã duyệt toàn bộ.
- Render API giữ logic nhẹ auth/quyền/metadata/keep-drop/quota enqueue/URL; Contabo worker giữ decode/inference/scoring/PII/report/export và điều phối Kaggle như #80. Tái dùng S3/Supabase/Kaggle hiện có với namespace riêng là đề xuất kỹ thuật trong docs, chưa suy câu “có host” là đã duyệt resource mapping.

## 82. Lưu trữ docs cũ và giữ một bộ tài liệu thực thi hiện hành (2026-10-09, người dùng yêu cầu)

- Người dùng yêu cầu rà docs cũ/outdated và đưa vào archived. PL6 chuyển các plan đã được pilot thay thế, guide khởi tạo lại MVP, intro hiện trạng cũ, PRD/audit snapshot và Technical Book/khung từ template vào `docs/archived/`; index ghi lý do và tài liệu thay thế, sửa liên kết. Không dùng archived làm contract hiện hành.
- Giữ CONTRACTS/architecture/UI, local limits, chi tiết dataset/snapshot local, component guides, cloud setup local, evaluation và log quyết định vì còn được dùng; sửa đoạn stale theo contract. Giữ đề xuất tương lai trong lịch sử/backlog, không xóa phạm vi chỉ vì archive.
- Guide/PRODUCT_STORY/README frontend và các khung entry docs có bản hiện hành ngắn ở đường dẫn cũ, để AGENTS và coding agent đọc đúng hiện trạng. Các file archived là lịch sử, không chứng minh provider/version/capacity hiện tại.
- Không di chuyển/sửa `goal_intent_flow.md` hoặc quyết định #63 sẵn có của người dùng. Không code/deploy/GPU/push trong PL6. Bộ schema/DTO/API v2, direct TLS PG roles, fork copied-history, receipt/hash và bootstrap modes vẫn là **đề xuất D0 cần duyệt**, không phải quyết định triển khai đã được người dùng chốt.

## 83. Từ mẫu tạo phiên trống; dùng tài nguyên cloud hiện có (2026-10-09, người dùng chốt)

- “Tạo phiên riêng từ mẫu” chỉ tạo **phiên trống** của user, dùng cùng nguồn và cấu hình khởi tạo hợp lệ của mẫu. Không sao chép proposal, keep/drop, snapshot, job, export, confirmation, lịch sử hoặc metric; không tự chạy đề xuất đợt đầu. User thao tác sau đó mới có lịch sử/kết quả riêng. Liên kết tới template chỉ là provenance của việc khởi tạo.
- Phiên/lịch sử mẫu gốc vẫn shared read-only để mọi account học cách dùng; quản lý phiên và dữ liệu thao tác riêng giữa nhiều user như #81. Luồng copied-history đề xuất trong PL6 được thay bằng luồng trống này; không cần duyệt policy sao chép event nữa.
- Dùng **S3/Supabase/Kaggle hiện có**; người dùng xác nhận credential trong `.env` repo. Operator tooling có thể đọc file này khi chỉ định explicit để bootstrap, sau đó inject environment đã validate theo từng host/role. Không in secret, chép `.env` vào image/seed/backup artifact hoặc đưa credential backend vào frontend.
- Exact account/project/bucket/region/prefix/schema và scope credential phải được preflight kiểm trước ghi; dùng namespace pilot riêng trong tài nguyên hiện có để tránh đụng baseline. Có `.env` không chứng minh direct-PG roles/config đề xuất đã đủ: thiếu tên config/quyền nào phải báo rõ, không fallback admin/SQLite hoặc tự tạo project/bucket/account mới.
- PL7 chỉ cập nhật docs/acceptance theo hai câu trả lời trên; các interface/schema/RPC/API/runtime/measurement khác vẫn draft D0, chưa được duyệt hay triển khai. Không code/deploy/cloud job/push.

## 84. Khóa thiết kế kỹ thuật deployment pilot DEP0 vào Source of Truth (2026-10-09, người dùng đồng ý)

- Người dùng đồng ý triển khai tuần tự từ DEP0. Toàn bộ thiết kế kỹ thuật từ các tài liệu chi tiết (`DEPLOYMENT_IDENTITY_DATA_SPEC.md`, `DEPLOYMENT_RUNTIME_STORAGE_SPEC.md`, `DEPLOYMENT_UI_EXPORT_OPS_SPEC.md`) chính thức được chuẩn hóa và khóa vào Source of Truth: `CONTRACTS.md §21`, `architecture_diagram.md §11` và `UI_DESIGN.md`.
- Các chuẩn kỹ thuật đã khóa:
  1. `ActorContext(user_id: UUID | None, request_id: UUID)` gắn vào command layer. Runtime deploy pilot bắt buộc `user_id` hợp lệ được xác thực từ Supabase Auth, actor string `human:<user_id>`. Runtime local v1 dùng `user_id=None`, actor string `human:local`. Cấm client truyền `owner_id`/`actor`/`admin` từ body hoặc header (`extra="forbid"`).
  2. Toàn bộ tài nguyên HTTP dùng UUID; tầng Core (`Unit`, `Frame`, `FieldKey`, `spec_hash`, `pool_hash`) giữ nguyên 100% định danh và thuật toán hiện có.
  3. Bảng cơ sở dữ liệu PostgreSQL dùng prefix `rav_pilot_*` trên Supabase với kết nối direct TLS. Mọi foreign keys nghiệp vụ dùng `ON DELETE RESTRICT`. Trigger chặn tuyệt đối UPDATE/DELETE trên các bảng sự kiện và snapshot bất biến. Bật RLS trên toàn bộ bảng. Thứ tự transaction write lock: `profile` → `sources` (UUID sorted) → logical resource → `admission/quota/accounts` → `job` → `slots`.
  4. API v2 dùng prefix `/api/v2/*`, mã lỗi chuẩn (401, 403, 404, 409 `resource_in_use`, 409 `state_version_conflict`, 409 `idempotency_conflict`, 422, 503). Không mount API v1 upload/media/export trên deploy.
  5. Hàng đợi bền vững (Durable queue): job kinds, states, lease, fence token, single queue authority trên PostgreSQL; Contabo AI pull-worker nhận việc và điều phối Kaggle GPU với xác nhận `cleanup_done=true`.
  6. Quota Engine: Chu kỳ cửa sổ UTC 4 giờ (`floor(epoch/14400)*14400`), CPU ms và GPU session wall ms, holds, bonus ledger, và trần ngân sách Kaggle toàn cục.
  7. Tải lên trực tiếp S3 (Presigned POST vào `staging/` → worker sandbox verify → canonical raw key) và phục vụ media riêng tư (Presigned GET TTL 5m qua API kiểm quyền).
  8. Pipeline xuất dữ liệu PII: Snapshot freeze → YuNet/ONNX che mặt & biển số → Report core HTML/JSON → `payload_hash` → Owner xem trước & bấm xác nhận → Finalize ZIP private trên S3 → Download URL có hạn. Cấm tải raw ZIP bypass.
  9. Xóa nguồn an toàn: Chặn xóa nguồn SYSTEM và nguồn còn tham chiếu (409 `resource_in_use`). Nguồn private không còn tham chiếu đánh dấu `DELETE_PENDING` rồi xóa file S3 và lưu receipt tombstone audit.
  10. Phiên mẫu và Fork: Mọi user xem phiên/lịch sử mẫu SYSTEM chỉ đọc; "Tạo phiên riêng từ mẫu" tạo phiên PRIVATE trống cho user, giữ nguồn và cấu hình khởi tạo, counts = 0, state_version = 1, không copy lịch sử cũ và không tự enqueue đề xuất.
- Tạo contract fixtures JSON dùng chung (`tests/fixtures/pilot_contract_fixtures.json`) và test kiểm tra tính hợp lệ (`tests/test_pilot_fixtures.py`). Khóa gate DEP0, sẵn sàng cho lát DEP1.

## 85. Bootstrap hạ tầng nhẹ và harness đo đạc DEP1 (2026-10-09, người dùng đồng ý)

- Triển khai lát cắt DEP1 phục vụ bootstrap hạ tầng nhẹ cho deployment pilot:
  1. Tách dependency profiles: tạo `requirements-api.txt` siêu nhẹ (chỉ chứa FastAPI, Uvicorn, Pydantic, Boto3...; không chứa Torch, Ultralytics, CUDA) cho Render Web API và `requirements-worker.txt` cho Contabo AI Worker.
  2. Nâng cấp `Dockerfile` sang Python 3.12-slim đồng bộ môi trường phát triển; hỗ trợ multi-stage targets `api` (mặc định) và `worker`. Sửa `HEALTHCHECK` trỏ đúng vào endpoint `http://localhost:8000/health`. Cập nhật `.dockerignore` loại trừ `data/`, `output/`, `node_modules/` và các file `.env`.
  3. Bổ sung các endpoint kiểm tra sức khỏe nhẹ vào FastAPI: `GET /health` và `GET /api/v2/health` (liveness probe), `GET /health/ready` (readiness probe). Thêm `runtime_mode` vào cấu hình `App`.
  4. Xác nhận qua kiểm thử và đo đạc: Tiến trình Web API khởi động nhanh (~0.8s), bộ nhớ RAM chỉ ~67.6 MB (đạt chuẩn giới hạn 512MB của Render Free), và 100% không rò rỉ bất kỳ thư viện ML nặng nào (`torch`, `ultralytics`, `transformers`, `cv2`) vào tiến trình API.
  5. Xây dựng harness đo đạc `scripts/pilot_benchmark_harness.py` đo cold start, RAM RSS, và độ trễ p95 (< 3ms), lưu artifact tại `output/pilot-benchmark/measurements.json`.
  6. Tạo template biến môi trường `.env.pilot.example` phân định rõ biến cho Render API vs Contabo Worker, và runbook vận hành `docs/setup/PILOT_RUNTIME_RUNBOOK.md`.
  7. Viết unit tests `tests/test_pilot_bootstrap.py` xác thực toàn bộ hạ tầng nhẹ (3/3 tests passed). Hoàn thành lát cắt DEP1.

## 86. Hướng tới: Tối ưu trích xuất thumbnail Codec I-frame cho Preview và Proposal-only Frame cho Curation (2026-10-10, người dùng chốt; làm sau deployment)

- Bối cảnh: Cơ chế on-demand decode qua PyAV (Quyết định #37) gây nghẽn cổ chai CPU và lock tuần tự `h.lock` khi Frontend load đồng thời 20–50 frame. Nhưng việc giải nén toàn bộ frame (4 fps) ra Full-HD lại gây bùng nổ hàng vạn file và tốn hàng GB đĩa / S3 requests.
- Quyết định thiết kế kết hợp (Hybrid):
  1. Cho hiển thị lướt nhanh trên UI (Preview, Scrubber, Video library thumbnail): Sử dụng cơ chế trích xuất Codec I-frame siêu tốc (`ffmpeg -skip_frame nokey` / `packet.is_keyframe`). Tốc độ trích xuất ~1s cho video 5p, chỉ tạo ~150–300 thumbnail 320px (~3 MB), phục vụ hiển thị mượt mà tức thì cho Frontend.
  2. Cho thuật toán Curation & Active Learning (DINOv2, pHash, YOLO): Vẫn quét toàn diện ở tần số 4 fps để không bỏ lọt tình huống nguy hiểm / corner cases ngắn (0.2–0.5s), nhưng chỉ trích xuất pixel thật cho các frame được chọn vào Proposals (16–32 frame/vòng).
- Thời điểm thực hiện: Đưa vào backlog sau deployment; không làm gián đoạn phạm vi các lát cắt DEP hiện tại.

## 87. Triển khai Auth khép kín, PostgreSQL Persistence và RLS cho lát DEP2 (2026-10-10, người dùng đồng ý)

- Hoàn thành lát cắt DEP2:
  1. Xây dựng migration `src/rav/store/migrations/001_pilot_identity.sql`: các bảng `rav_pilot_profile`, `rav_pilot_source_asset`, `rav_pilot_session`, `rav_pilot_session_source`, `rav_pilot_state`, `rav_pilot_snapshot`. Kèm trigger khóa bất biến `trg_snapshot_immutable` và toàn bộ các RLS policies. Ràng buộc `ON DELETE RESTRICT` trên các foreign key nghiệp vụ.
  2. Xây dựng module xác thực `src/rav/api/auth.py`: trích xuất Bearer token từ header, xác minh chữ ký JWT (JWKS / HMAC), issuer, audience, expiry, kiểm tra `profile.active`, sinh `ActorContext(user_id, request_id)`. Cấm đọc `owner_id`/`actor` từ body. Hỗ trợ fallback `human:local` trên local và ép buộc 401 trên pilot deploy.
  3. Xây dựng adapter `src/rav/store/pilot_db.py`: quản lý transaction với PostgreSQL RLS context (`SET LOCAL ROLE authenticated; set_config('request.jwt.claims', ...)`), hỗ trợ optimistic locking trên `state_version` (409 conflict nếu gửi version cũ), kiểm tra RESTRICT khi xóa source, bảo vệ snapshot bất biến.
  4. Triển khai router `/api/v2/*` trong `src/rav/api/routes_v2.py` và mount vào FastAPI: `/auth/me`, `/sources`, `/sessions`, `/sessions/{id}/state`, `/sessions/{id}/snapshots`.
  5. Cập nhật `requirements.txt`, `requirements-api.txt`, `requirements-worker.txt` với `PyJWT` và `psycopg`.
  6. Toàn bộ 22 tests của pilot (`test_pilot_auth.py`, `test_pilot_store.py`, `test_pilot_api_v2.py`, `test_pilot_bootstrap.py`, `test_pilot_fixtures.py`) và toàn bộ 226 tests của repo đạt kết quả xanh.
## 88. Triển khai Hàng đợi bền vững (Durable Queue) và Động cơ hạn mức (Quota Engine) cho lát DEP3 (2026-10-10, người dùng đồng ý)

- Hoàn thành lát cắt DEP3 theo CONTRACTS §21 và DEPLOYMENT_RUNTIME_STORAGE_SPEC §3, §4, §5, §7:
  1. Xây dựng migration `src/rav/store/migrations/002_pilot_runtime.sql`: các bảng `rav_pilot_quota_policy`, `rav_pilot_quota_window`, `rav_pilot_quota_bonus`, `rav_pilot_job`, `rav_pilot_job_attempt`, `rav_pilot_quota_hold`, `rav_pilot_quota_hold_part`, `rav_pilot_usage_receipt` (kèm trigger khóa bất biến `trg_usage_receipt_immutable`), `rav_pilot_resource_slot`, `rav_pilot_source_pin`, và `rav_pilot_dispatch_cursor`. Seed cấu hình `default_pilot_v1` và các slot ban đầu (`cpu-slot-1`, `cpu-slot-2`, `gpu-slot-1`).
  2. Xây dựng module `src/rav/store/pilot_runtime.py`:
     - `enqueue`: atomic transaction kiểm tra quyền profile, idempotency replay (409 conflict nếu trùng key nhưng khác payload), kiểm tra và giữ chỗ hạn mức CPU/GPU trong cửa sổ 4h UTC (trả 429 `quota_exceeded` nếu hết hạn mức), ghim nguồn (`source_pin`) với `consumer_kind="JOB"` chống xóa nguồn khi đang xử lý, và đưa job vào hàng đợi `QUEUED`.
     - `claim`: worker pull-job bằng Compare-And-Swap (CAS), phân bổ slot tài nguyên khả dụng, cấp `fence` token tăng dần và lease 30s (`AttemptLease`).
     - `heartbeat`: worker gia hạn lease định kỳ, cập nhật tiến độ (`phase`, `done`, `total`), phát hiện tín hiệu `cancel_requested`.
     - `publish`: công bố kết quả `SUCCEEDED`, ghi biên lai `rav_pilot_usage_receipt` bất biến, tất toán quota hold (hoàn trả phần chưa dùng), nhả ghim nguồn, và giải phóng slot tài nguyên.
     - `cancel`: hủy ngay lập tức nếu job đang `QUEUED` và hoàn trả 100% quota hold; đánh dấu `CANCEL_REQUESTED` nếu job đang `RUNNING` để worker dừng an toàn.
     - `recover_expired_leases`: supervisor tự động phát hiện attempt quá hạn lease do worker gặp sự cố, giải phóng slot, tự động requeue job (nếu attempt < 2) hoặc chuyển `FAILED` và hoàn trả quota.
     - `read_quota`: tra cứu hạn mức khả dụng và thời điểm reset tiếp theo của cửa sổ 4h UTC.
  3. Xây dựng các module Worker:
     - `src/rav/worker/dispatcher.py`: `PilotDispatcher` triển khai worker pull-loop với luồng heartbeat nền và cooperative cancellation.
     - `src/rav/worker/supervisor.py`: `PilotSupervisor` định kỳ thu hồi các lease quá hạn.
     - `src/rav/worker/kaggle_adapter.py`: `KaggleLifecycleAdapter` quản lý vòng đời GPU Kaggle với marker định danh intent `pilot:<deployment>:<job>:<attempt>:<fence>` và đối soát trạng thái (`FOUND`, `ABSENT_VERIFIED`, `UNKNOWN`).
  4. Mở rộng API routes trong `src/rav/api/routes_v2.py`: `GET /api/v2/jobs`, `GET /api/v2/jobs/{id}`, `POST /api/v2/jobs/{id}/cancel`, `GET /api/v2/quota`. Cập nhật `delete_source` trong `pilot_db.py` kiểm tra `rav_pilot_source_pin` (409 `resource_in_use` nếu đang bị ghim).
  5. Kiểm thử: Viết mới `tests/test_pilot_queue.py` (8 tests) và `tests/test_pilot_quota.py` (4 tests), cập nhật `tests/test_pilot_api_v2.py` (3 tests). Toàn bộ 35 tests của pilot và 238 tests toàn repo đều đạt kết quả xanh, ruff linting 100% đạt chuẩn.

## 89. Sửa nền DEP2/DEP3 cho khớp CONTRACTS §21 trước DEP4 — lát DEP2F (2026-10-10, người dùng chốt sau khi thống nhất với Duy)

- Rà trước DEP4 thấy DEP2/DEP3 (#87, #88) lệch hợp đồng đã khóa: schema `source_asset` thiếu `scope/protected/raw_key/availability/resource_version`, SYSTEM có owner;
  profile có `role` và RLS đọc `role=admin` từ claims; `ON DELETE CASCADE`/`SET NULL`; thiếu `DATABASE_URL` thì fallback SQLite và migration chạy lại ở mọi request;
  `transaction()` không bao giờ truyền claims nên RLS không có hiệu lực; client gửi thẳng kept/dropped và cả `snapshot_hash`/`manifest`; xóa nguồn DELETE hàng;
  migration 002 gieo số quota + policy fallback cứng trong code; claim không khóa slot, publish không kiểm lease/state/replay, lease hết hạn trả slot FREE ngay;
  dispatcher publish SUCCEEDED giả khi không có handler; route v1 upload/media luôn mount; auth cho qua khi thiếu profile, không kiểm audience, chỉ đọc khóa RSA.
- Người dùng chốt: **tự sửa cho đúng** bằng một lát riêng **DEP2F** trước DEP4; test trên **PostgreSQL thật** (bản portable trong `data/tools/pgsql`, cluster test `data/pg-test`, cổng 54329, chỉ local).
- Chi tiết triển khai đã chốt trong lát (ghi ở CONTRACTS §21.11):
  1. Store pilot **chỉ PostgreSQL**; deploy không fallback. Migration do operator chạy `scripts/pilot_migrate.py` (khóa advisory + bảng `rav_pilot_schema_migration`);
     API/worker chỉ kiểm version, thiếu → 503 `schema_not_ready`. Hai migration 001/002 được **viết lại tại chỗ** vì chưa từng chạy trên Postgres thật (`DATABASE_URL` trong `.env` vẫn là giá trị mẫu).
  2. Đọc catalog/session qua `SET LOCAL ROLE authenticated` + claims (RLS); ghi qua role backend `rav_pilot_api` với `ActorContext` đã xác thực, kiểm owner tường minh,
     `SELECT … FOR UPDATE` theo thứ tự §21.3. Worker dùng role `rav_pilot_worker`. Nghiệp vụ viết bằng adapter Python (không SQL RPC) — một nơi, không hai authority.
  3. Review state = log `rav_pilot_decision` append-only (replay); snapshot do server tự tính `members_hash`/`context_hash` từ state; xóa nguồn = tombstone `DELETE_PENDING` + receipt + job `DELETE_SOURCE` (handler ở DEP6).
  4. Không seed số quota/slot trong migration; thiếu policy hợp lệ → 503 `policy_unconfigured`; slot do operator khai báo.
  5. App deploy riêng `src.rav.api.pilot:app` chỉ mount `/api/v2` + health; app local không mount v2.
- Phần DEP3 chưa từng có, **không** làm trong DEP2F và ghi rõ lát nhận: storage account/hold → DEP4 (caller đầu tiên là upload); GPU budget snapshot/hold → DEP5; admission/cost snapshot → DEP7.

## 90. Danh sách phiên mẫu SYSTEM cho DEP4 (allowlist import) (2026-10-10, người dùng chốt)

- Nguồn: `output/rav.sqlite` máy người dùng (Thiện). Hai video `1a2c259281acc216` (354 s) và `400d676ae83a185e` (153 s) là video người dùng tự quay để test → được dùng làm nguồn SYSTEM chia sẻ cho các tài khoản pilot.
- Tiêu chí chọn mẫu: ≥ 3 đợt, ≥ 20 quyết định, có snapshot; file nguồn còn và hash khớp; không khai báo quan hệ model ↔ dataset giả; mỗi mẫu một mục tiêu/recipe khác nhau; loại phiên thử.
- **Nhận:** `d389b08a…` → tên mẫu "Mẫu: Đa dạng cảnh — dashcam đô thị" (4 đợt `diversity`, 36 quyết định của người `human:local`, snapshot 1 = 34 frame).
  **Nhận (mới dựng):** `dfbeca2c…` "Mẫu: Đêm mưa lớn" trên `400d676a` — 3 đợt `text_diversity` (CLIP) + 1 đợt `uniform`, 64 quyết định, giữ 5, snapshot 1 = 5 frame.
  **Loại:** `d2c250e2…`, `83db90ae…`, `7767d4a1…` (1 đợt, 0–3 quyết định).
- Phiên "Đêm mưa lớn" do AI (Claude) duyệt theo yêu cầu người dùng: mọi quyết định ghi actor `ai:claude-opus-5-5` kèm lý do (CONTRACTS §12.3), không ghi `human:local`.
  Hai snapshot do AI chốt nên `created_by = ai:claude-opus-5-5`. Mục tiêu ban đầu "Người đi bộ ban đêm" được đổi vì video không có người đi bộ (đợt thử bị hủy, khôi phục DB từ bản sao trước khi tạo).
- Quan sát giữ lại làm bằng chứng: `text_diversity` dồn đề xuất vào 90 s đầu (mưa tệ nhất, 3 đợt chỉ giữ 3/48), đổi sang `uniform` tìm ra 2 frame rõ trong 16 — khớp V2E: CLIP chưa đủ để lọc theo ngữ nghĩa; dùng làm cơ sở cho hướng VLM.
- DINOv2/pHash/CLIP của hai video tính local trên CPU (không S3/Kaggle). Bản sao DB trước khi dựng: `output/rav.sqlite.pre-demo-20261010T121506.bak`.

## 91. Chi tiết triển khai DEP4 — upload trực tiếp, media riêng tư, phiên mẫu (2026-10-10, người dùng giao làm DEP4)

- Migration storage/mẫu là `003_pilot_storage.sql`; export dời sang `004_pilot_export.sql` (DEP6). Đã sửa tham chiếu trong spec runtime/UI-export và execution plan.
- Giữ chỗ dung lượng (storage account/hold) làm ở DEP4 như đã ghi #89, giữ `2 × declared` cho upload; staging chỉ giải phóng khi supervisor xóa sau hạn + grace.
- Worker dùng lại `local_folder._probe` và decoder `pyav` để `core_source_id`/`n_units` của video upload trùng local (CONTRACTS §21.2); cùng bytes trong kho owner → dùng lại nguồn.
- Media mỗi owner một cache; `context` là mp4 H.264 ±3 s do worker dựng (PyAV có libx264); job media không tính vào `pending_per_user`, có giới hạn `max_media_requests`.
- Code đặt trong `store/` (`pilot_storage.py`, `pilot_uploads.py`, `pilot_media.py`, `pilot_templates.py`) để app pilot giữ import nhẹ (gói `service` kéo pipeline/numpy);
  handler worker ở `worker/handlers.py` (import nặng chỉ ở worker).
- Test S3 bằng `moto` (thêm vào `requirements.txt`); `moto` không kiểm `content-length-range` nên worker tự kiểm byte thật. Chưa ghi S3/Supabase thật trong lát này (người dùng chọn).
- Màn FE deploy (đăng nhập, Dữ liệu của tôi, Phiên mẫu) không nằm trong mô tả DEP4 trong PLAN → chưa làm, hỏi người dùng xếp lát.
- Sửa tại chỗ `001_pilot_identity.sql` (chưa áp trên môi trường thật nào): unique `(owner, core_source_id)` / `core_source_id` bỏ qua nguồn `DELETED`, để xóa rồi upload lại cùng nội dung được.
  Từ khi có DB thật, mọi thay đổi schema phải là migration mới (checksum đã áp bị khóa).

## 92. Hoàn thiện DEP4F sau review nhánh thien (2026-10-10, người dùng chốt phạm vi)

- Bổ sung migration `004_pilot_worker_objects.sql` cho receipt object bền trước PUT; export chuyển sang `005_pilot_export.sql`. Không sửa checksum migration đã áp. Worker lưu bằng chứng process trước handler và đối soát riêng sau khi handler/process đã dừng; deadline dừng độc lập với kết nối DB.

- Fast-forward `duy-mvp/v2` tới nhánh `thien`, sửa năm lỗi review và nối worker upload/media + supervisor chạy được.
- Phạm vi người dùng chọn: DEP4 + worker; frontend đăng nhập/phiên mẫu và DEP5–DEP8 ở lát sau. Không deploy cloud hay push trong lát này.
- Bổ sung triển khai các invariant đã có: actual quota đầy đủ, retry sau reconcile, Unit phải thuộc pool thực, cleanup object có receipt trước trả storage, context đúng timeline. CONTRACTS §21.13 ghi tiêu chí thực thi; không đổi thuật toán curation/core identity.

## 93. Giao diện Web Deploy v2 (Lát DEP-UI) và cơ chế Placeholder (2026-10-10, người dùng giao làm DEP-UI)

- Mục đích: Cung cấp giao diện web cho bản deploy pilot v2 (xác thực JWT Supabase, Dev Access Token cho dev/test, Quota badge theo dõi CPU/GPU/reset time, màn Phiên mẫu SYSTEM với tính năng Fork tạo phiên riêng, chuyển đổi mượt mà giữa các màn hình).
- Phạm vi: Chỉ thao tác trên frontend (`src/web/*`), không chỉnh sửa bất kỳ file backend nào (`src/rav/*`).
- Cơ chế Placeholder cho các API chưa hoàn thành (DEP5 worker curation):
  1. Khi người dùng ở chế độ Pilot (đã đăng nhập token) hoặc gọi đề xuất/YOLO: hệ thống hiển thị thông báo rõ ràng về trạng thái phát triển của DEP5 thay vì ném lỗi kĩ thuật mơ hồ.
  2. Bắt đầu từ Phiên mẫu (`#templates`), người dùng có thể duyệt, xem chi tiết frame/video, đánh dấu keep/drop/clear, hoàn tác và chốt snapshot manifest hoàn toàn bình thường dựa trên dữ liệu mẫu đã lưu.
  3. Quota Badge hiển thị đúng hạn mức CPU 4h UTC, GPU minutes và thông báo admission theo spec.
- Màn hình Đăng nhập hỗ trợ cả Supabase Email/Password và Dev Access Token trực tiếp cho local dev test.
- Kiểm thử: `npx tsc --noEmit` và `npm run build` vượt qua 100%; toàn bộ 261 ca kiểm thử backend và ruff linting giữ vững màu xanh.

## 94. Chia DEP5 thành 5a → 5b → 5c, đánh số migration và cách làm GPU (2026-10-10, người dùng chốt)

- So plan DEP5 người dùng gửi với DEP5 trên `duy-mvp/v2` và code: cả hai bỏ qua phụ thuộc PROPOSE trên video mới cần đặc trưng từ PREPARE.
  Chốt chia **DEP5a** chuẩn bị đặc trưng → **DEP5b** curation trên worker (PROPOSE/YOLO/METRICS, đóng băng ngữ cảnh, parity, A06)
  → **DEP5c** FE phần còn lại sau DEP-UI (#93: đăng nhập, quota badge, phiên mẫu đã có). Chi tiết `docs/archived/2026-10-13/docs/backlog/DEP5_PLAN.md`.
- Migration: `005_pilot_features.sql` (5a), `006_pilot_curation.sql` (5b), export dời `007_pilot_export.sql` (thay ghi chú 005 ở #92).
- Ngân sách GPU toàn cục: worker tự đọc Kaggle SDK `get_accelerator_quota_statistics` (tổng/đã dùng/đang giữ/lúc reset) ghi snapshot làm bằng chứng;
  snapshot quá `gpu_snapshot_fresh_ms` → job GPU `WAITING_RESOURCE` `gpu_budget_unverified`. CLI operator ghi tay chỉ dự phòng.
- Đối soát session Kaggle: mỗi slot GPU một notebook private cố định; SDK không tìm session theo marker nên đối soát bằng trạng thái session
  của notebook đó (đang chạy → FOUND, đã kết thúc → ABSENT_VERIFIED, lỗi API hoặc đang chạy mà không có handle → UNKNOWN giữ slot).
  Bỏ `KaggleLifecycleAdapter` giả trong RAM (đoán ABSENT khi không biết).
- Đặc trưng nguồn SYSTEM do operator import sẵn (mở rộng `scripts/pilot_import_demo.py`, dry-run + operator-confirm); người dùng không chạy PREPARE trên nguồn SYSTEM.
- DEP5a chỉ test bằng transport Kaggle giả + PostgreSQL thật + S3 `moto`; smoke GPU thật để riêng khi người dùng bảo.
- DEP5 không train model: chỉ inference (DINOv2, CLIP, YOLO) + thuật toán chọn frame hiện có trên worker.

## 95. Chia lại DEP5b theo UI DEP-UI: Studio trên v2 trước, YOLO/METRICS sau (2026-10-10, người dùng đồng ý)

- Rà `src/web` (#93): chỉ đăng nhập/`/me`/quota/danh sách mẫu/fork gọi `/api/v2`; Studio, sidebar, kho video, phân tích gọi `/api/v1` mà app pilot
  không mount (§21.11) → sau fork Studio không tải được. Lệch DTO: fork trả `{session: {id}}` nhưng FE đọc `.id`; số đếm mẫu là `counts.*`;
  `appendDecisions` gửi `decisions` thay vì `changes`.
- **DEP5b** = job PROPOSE cho 4 recipe không cần model + route đọc Studio cần (recipes, proposals, giải thích đợt, job result/retry).
  **DEP5c** = chuyển Studio sang v2 (sửa lệch DTO ở đầu lát, do người dùng làm, không chờ Duy). **DEP5b2** = YOLO + recipe cần model + METRICS + bản đồ.
- API kiểm nhẹ (recipe pilot, cấu trúc config, giới hạn, đặc trưng sẵn sàng → 409 `feature_not_ready`); worker kiểm đầy đủ bằng registry.
- Ngữ cảnh đổi khi đề xuất đang chạy → FAILED `proposal_context_changed` (nghiêm, khớp CONTRACTS); FE khóa thao tác duyệt khi chờ.
- METRICS ở pilot: bấm mới tính, cache theo `decision_seq`, không tự chạy sau mỗi quyết định (DEP5b2).

## 96. DEP5c: Studio pilot trên v2, Supabase SDK, vitest, smoke local (2026-10-10, người dùng chốt)

- Chế độ build `VITE_RAV_MODE=local|pilot`: bản local quay lại không cần đăng nhập (DEP-UI bắt đăng nhập v2 cả ở local nên local không vào được);
  bản pilot chỉ gọi `/api/v2`, hook/component giữ interface `api` chung (CONTRACTS §21.16).
- Thêm `@supabase/supabase-js` (session/refresh do SDK) thay REST tự gọi; Dev Access Token chỉ trong `vite dev`.
- Thêm `vitest` (không jsdom/testing-library) cho gate FE-AUTH/FE-JOB/FE-TEMPLATE bằng HTTP giả + request log.
- Smoke 3 tài khoản trên stack local giả lập: API/worker pilot + Postgres portable + S3 `moto server` + token HS256 dev; không ghi Supabase/S3 thật.
  Thêm `moto[server]` vào requirements (chỉ dev/smoke).
- DTO đọc bổ sung: `SourceDTO.core_source_id`, `SessionDTO.n_kept/n_dropped`.

## 97. DEP5b2: YOLO model SYSTEM, phân tích một job, recipe cần train để Hướng tới (2026-10-10, người dùng chốt)

- Recipe cần dataset train (`reference_diversity`, `model_guided`, `text_model_guided`) để **Hướng tới**: chưa có BDD100K/checkpoint train thật; UI hiện
  recipe nhưng tắt kèm lý do `missing_model_training_provenance` (đúng UI-export spec), API trả 422. Làm khi có dữ liệu thật.
- YOLO: tải checkpoint chính thức YOLO11n COCO (Ultralytics) vào `data/models` cho test/smoke; operator import thành model SYSTEM, provenance ghi train COCO,
  không coi là model train BDD. YOLO chạy trên worker CPU (như M2), không Kaggle.
- Migration mới `007_pilot_analysis.sql`; export dời `008_pilot_export.sql` (không sửa migration đã áp, kể cả DB smoke).
- Phân tích: một job METRICS bấm mới tính, gồm metric mặc định + dữ liệu 3 view, cache theo `decision_seq`/`proposal_count`; GET không tự tính.

## 98. Chuẩn bị luồng cập nhật và deploy frontend trước API (2026-10-10)

- Người dùng yêu cầu setup luồng cập nhật các máy deploy và đưa FE lên Render ngay; xác nhận đã có tài khoản Render và VPS Contabo. API chưa lên host.
- Giữ host đã chốt #80: Render Static Site cho FE, Render Web Service cho FastAPI, Contabo cho worker. Publish FE trước không chứng minh đăng nhập/nghiệp vụ hoặc các gate mở pilot đã đạt.
- Chuẩn bị `render.yaml` cho nhánh tích hợp `duy-mvp/v2`, CI frontend npm/TypeScript/build và Python 3.12 khớp Docker; Render auto-deploy sau CI checks đạt. Client v2 đọc public `VITE_API_URL`, Supabase publishable key; giữ alias anon key local cũ. Không đưa secret backend vào build FE.
- Người dùng đã cho phép commit/push: cấu hình đã push lên `duy-mvp/v2` tại `fd9d809`. TypeScript, Vite build, ruff và YAML parse/diff-check local đạt; chưa deploy hoặc thay host thật, chưa kiểm đăng nhập/nghiệp vụ live.
- Blocker thực: Render GitHub App trên tổ chức `AI20K-Build-Phase-Cohort-4` đang chờ duyệt (màn installation hiển thị Cancel Request); Render không thấy repo private P-026. CI run `38055343858` dừng trước mọi step vì account payment/spending limit, không có log test/build. Chưa tăng hạn mức/thay billing hay cấp quyền cho repo khác.
- Ghim runner `ubuntu-24.04` để tránh `ubuntu-latest` tự đổi hệ điều hành. Cần org admin duyệt app/khôi phục Actions; chưa xác định VPS/service hiện có nên không đoán host hay ghi đè process của teammate.


## 99. Review và tích hợp DEP5 từ thien (2026-10-10)

- Người dùng yêu cầu kiểm nhánh `thien` mới commit DEP5 và merge vào `duy-mvp/v2`; yêu cầu không dùng Astra để review. Review thực hiện trực tiếp trong phiên này, không agent phụ.
- Merge tip `9d30df7`, giữ client/auth Supabase SDK mới và config URL/key Render hiện có. Quyết định deploy FE riêng của nhánh Duy đổi số #94 thành #98 để không đụng quyết định DEP5 của Thien. Render build `VITE_RAV_MODE=pilot`; CI thêm vitest và build cả local/pilot.
- Sửa implementation theo runtime spec §3.2: GPU dispatcher chạy tiến trình con spawn riêng, không cộng CPU job khác vào quota; lưu mốc launch vào process_group JSONB để supervisor tính GPU tới stop verified, đọc lại usage sau cập nhật. Xóa object trao đổi thất bại giữ slot cách ly tới lần dọn tiếp; không sửa SQL migration đã áp.
- Replay phân tích cùng key giữ kết quả nhưng kiểm tra current theo quyết định/đề xuất mới nhất. Response refresh của generation cũ không đăng xuất tài khoản mới. Thêm regression cho các lỗi này và cleanup khi supervisor lỗi.
- Đây là lát tích hợp DEP5F; không đổi host/deploy, không mở pilot, không push trong yêu cầu merge hiện tại. Kiểm chứng cloud/GPU thật vẫn riêng theo runbook.
- Một lượt toàn suite có test local `test_missing_clip_cache_never_runs_image_encoder` fail với thông báo lỗi chung; tái hiện riêng đạt ngay, lượt toàn suite cuối cũng đạt (385 test, 3 bỏ qua). Không sửa code local/test để che lỗi chưa tái hiện.
- Kiểm chứng cuối: PostgreSQL 16.15 thật + S3 moto, pytest 385 đạt/3 bỏ qua; riêng GPU/analysis/entry 21 đạt/2 bỏ qua; vitest 14/14, ruff, tsc, build cả local/pilot, diff-check đạt. Hai test YOLO không chạy do chưa có checkpoint trên máy này; không coi mock provider là kiểm chứng GPU/cloud thật.

## 100. FE/API trên Render từ Docker image build sẵn (2026-10-10)

- Người dùng chọn rõ: FE + BE trên Render từ image build sẵn, worker trên Contabo. FE trở thành image-backed Web Service (SPA + Nginx), không còn Static Site trong luồng release này; Supabase/Auth/DB/S3 giữ topology và quyền theo #84. Cấu hình Static Site cũ chỉ còn đường dự phòng, chưa deploy.
- Một lát DEP7-Docker: generic image FE có public runtime config allowlist; API image nhẹ hỗ trợ PORT + CORS origin explicit; tag commit/digest để update/rollback. Không worker AI trong release FE/API, không mở admission pilot hoặc tự migrate/seed cloud.
- Docker Desktop đã cài nhưng engine chưa chạy và chưa thấy cấu hình data disk; chưa build image khi chưa xác minh storage/cache ở ổ D theo AGENTS. GitHub CLI token hiện có repo/read:org/gist, thiếu write:packages/read:packages; chưa dùng nó để push GHCR hoặc đưa vào Render. Registry credentials/publish và env cloud phải explicit trước thao tác thật.
- Người dùng chọn Docker Hub private và sẽ cấu hình Docker Desktop lưu image/cache ở ổ D. Một repo private `rav26`, tag `api-SHA`/`web-SHA`, không cần hai private repo. Namespace/đăng nhập registry còn chờ; không tạo public package hoặc tự cấp token rộng hơn.
- Cập nhật: người dùng cung cấp username `tonmeu` và cho phép dùng Docker với storage hiện tại vì chưa biết chuyển ổ D. Ngoại lệ chỉ cho storage Docker theo yêu cầu này; Python/npm/temp vẫn dùng project storage. Engine Linux/amd64 Docker Desktop 4.45.0 đã hoạt động; bắt đầu build image local, chưa đăng nhập/publish registry.
- Kiểm chứng đóng gói: hai image Linux/amd64 build đạt; FE chạy tsc + vitest 16/16 + pilot build trong Linux. Container chạy user appuser/nginx, PORT khác mặc định, healthcheck healthy; SPA deep link và public config no-store đạt, biến DB giả không lộ. API import không torch/numpy/av/ultralytics; readiness 503 khi không DB đúng contract. Ruff đạt, pytest bootstrap/CORS 11/11 đạt (lượt sandbox treo đã dừng; chạy ngoài sandbox đạt). Docker Hub CLI đăng nhập tonmeu; đã tạo và GET xác minh tonmeu/rav26 private trước publish. Chưa kiểm cloud/env/Render/worker thật.
- Release commit `3ddcb54f7d48` đã push hai image vào Docker Hub private; API digest `b367d1acd74083538a4a6727ab2050c2821835b153d75e99ffb15e0d865eebb1`, web digest `0ebf2ad254176d7307848fcb7a2e323e528a82a4d21280532b7eea29f2af5af0`. Không push Git trong bước này.
- Người dùng tự thêm credential Docker Hub read-only vào Render và cho phép dùng cấu hình Supabase/S3 từ `.env`. Đã tạo `rav26-api` (`srv-db54lcjbc2fs73eai7u0`) và `rav26-web` (`srv-db54miad0e5s73e6lg4g`) trong project T026-Rav026, Singapore, plan Free; URL `https://rav26-api.onrender.com` và `https://rav26-web.onrender.com`. Hai deploy Live, health 200, SPA deep link 200; runtime config chỉ ba trường public, no-store, trỏ đúng API; CORS preflight 200 cho đúng origin FE. Màn đăng nhập đã hiển thị, chưa kiểm đăng nhập tài khoản thật.
- API readiness vẫn 503: DATABASE_URL hiện có dùng postgres admin nên không chuyển lên API; lần kiểm inventory DB từ máy trả OperationalError, chưa xác minh schema/role cloud. Chưa migrate/seed hoặc mở pilot; worker chưa deploy. Một lượt nhập env API sai hàng làm CORS không hợp lệ và deploy fail, đã sửa/đối chiếu tất cả biến trước lưu, deploy cuối Live. Khi đọc form env, credential AWS xuất hiện trong log công cụ; đã báo người dùng cần thay key, không ghi giá trị vào Git/docs.

## 101. URL public có tên và mã nhóm (2026-10-10)

- Người dùng xác nhận yêu cầu ban tổ chức: URL public phải chứa tên nhóm `IrishCoffee` và mã `T026`, không yêu cầu định dạng hay hostname cụ thể.
- URL FE để nộp: `https://rav26-web.onrender.com/IrishCoffee-T026`. Nginx SPA fallback hiện có phục vụ đường dẫn này; HTTP 200 và trình duyệt hiển thị màn đăng nhập. Không cần rebuild image hoặc đổi origin/CORS/API.
- Tên dịch vụ FE trong Render đổi thành `irishcoffee-t026-web`; Render giữ hostname cũ khi đổi tên. Service ID và image digest không đổi. Giới hạn DB/worker/credential theo #100 vẫn còn.

## 102. Nối PostgreSQL pilot cho API Render (2026-10-10)

- Người dùng chạy migration trên Supabase thành công, output áp thêm `005_pilot_features.sql`, `006_pilot_curation.sql`, `007_pilot_analysis.sql`; tự bật LOGIN và đặt mật khẩu riêng cho `rav_pilot_api` trong SQL Editor.
- Người dùng tự nhập DATABASE_URL vào Environment API Render, dùng Session pooler port 5432, username `rav_pilot_api.hwikrrfnbemcoxngvgbd`; không chuyển postgres admin lên API. Đã Save and deploy theo yêu cầu, không đọc/in giá trị credential.
- Kiểm tra HTTP sau deploy `dep-db551lqjnfac7398vrlg`: `GET https://rav26-api.onrender.com/health/ready` trả 200, `status=ok`, `checks.database=ready`, `checks.schema=ready`. Đây là gate DB/schema; chưa chứng minh profile/quota, đăng nhập end-to-end, S3/worker/AI hoặc admission pilot đã đạt. Việc thay AWS key được nêu ở #100 vẫn chưa xác minh hoàn tất.

## 103. Monitor API bằng UptimeRobot Free (2026-10-10)

- Người dùng yêu cầu setup UptimeRobot thay ping API trên Contabo và đã tự đăng nhập. Đã tạo monitor `804231770`, tên `IrishCoffee T026 - API health`, URL `https://rav26-api.onrender.com/health`, chu kỳ 5 phút, timeout 60 giây, email tài khoản được chọn nhận cảnh báo; không mua gói trả phí hoặc cài cron Contabo.
- Dùng Keyword monitor với GET, incident khi thiếu `"status":"ok"`. HTTP monitor Free mặc định HEAD nhưng API hiện trả 405 với HEAD; Keyword GET tránh báo lỗi giả mà không cần rebuild API.
- Kiểm chứng: GET public trả 200 với status ok; dashboard UptimeRobot hiển thị Up sau lượt kiểm tra đầu tiên. Chỉ xác nhận liveness, không chứng minh DB/schema hoặc worker sẵn sàng.
- Chỉ monitor API, không ping FE liên tục. Render Free giới hạn 750 instance-hours/tháng dùng chung workspace; monitor không tăng quota và không bảo đảm uptime vô hạn.

## 104. Hostname nhóm và entry landing/login/app (2026-10-10)

- Người dùng cho phép tạo FE mới từ Docker image để hostname có `irishcoffee-t026`; đồng thời yêu cầu landing page và route đăng nhập/ứng dụng riêng. Chấp nhận nội dung giới thiệu IrishCoffee · T026, mô tả RAV-26 và CTA đăng nhập/vào ứng dụng.
- Pilot: `/` (alias `/landing`) public, `/login` chỉ auth, `/app` bảo vệ bằng Supabase + `me.active`; điều hướng nghiệp vụ giữ hash hiện có. Alias `/IrishCoffee-T026` giữ liên kết cũ. Redirect đăng nhập chỉ nhận đường `/app` cùng origin, không nhận URL bên ngoài; logout/hết hạn vẫn xóa state bằng tải lại trang. Local không đổi; không thêm thư viện router.
- Dùng lại registry private/credential read-only và cấu hình public hiện có; chỉ rebuild FE cho routes mới. API/DB/worker không đổi image; CORS cho cả origin FE mới và cũ trong lúc chuyển. Chưa xóa service FE cũ.
- Release FE `c75501972cb2`, image `tonmeu/rav26:web-c75501972cb2`, digest `c0d8169f19a495787d7462be51f71d321b133185b9de53bc365998d9daa68e50`. Service `srv-db559q942hec73fp6790`, deploy `dep-db559r142hec73fp68pg`, Free Singapore, URL `https://irishcoffee-t026.onrender.com`, Live. Không push Git.
- Kiểm chứng: tsc, vitest 19/19, build local/pilot, ruff, image Linux build (19 test) và container healthy đạt. Trình duyệt live xác minh landing, login, app chưa auth chuyển login, alias cũ giữ hash Studio; mobile 390px không tràn ngang. API CORS preflight trả 200 cho cả hai origin, DB/schema readiness 200 sau deploy env `dep-db55bso473hc739uku7g`.
- Một lần sửa ô CORS đang che giá trị không thay origin dù tạo deploy; đã mở riêng giá trị CORS, nhập lại và đối chiếu trước Save. Shell HTTP có một lượt Cloudflare challenge 403; dùng browser cho FE và kiểm API lại sau deploy đạt. Chưa đăng nhập tài khoản thật hoặc chứng minh upload S3; `GetBucketCors` bằng credential hiện có trả AccessDenied, chưa sửa CORS bucket hoặc thay quyền.

## 105. Bàn giao deployment và ping cả FE/API (2026-10-10)

- Người dùng yêu cầu viết docs deployment trước, ghi rõ phần chưa làm; trong cùng lát yêu cầu ping cả hai service để hạn chế ngủ. Thay lựa chọn API-only #103 bằng API + FE chính, không monitor FE cũ và không cài cron Contabo.
- `docs/setup/DEPLOYMENT_STATUS.md` tổng hợp URL/service/image/digest, env theo host, cập nhật thủ công/rollback, migration, monitoring/quota, bằng chứng và blocker. Cập nhật index và runbook cũ để tránh nhầm worker DB env/Kaggle token hoặc coi hosting Live là pilot đã hoàn tất. Không thay thiết kế/code hoặc mở admission.
- Đã giải thích ping không tăng 750 instance-hours/workspace/tháng: FE + API luôn chạy cần khoảng 1.440–1.488 giờ/tháng, chưa cộng service khác; chưa đọc quota còn lại thực tế. Không tự nâng plan/thay billing.
- Đã tạo FE monitor `804231926`, tên `IrishCoffee T026 - FE health`, URL `https://irishcoffee-t026.onrender.com/health`; Keyword GET, thiếu `"status":"ok"` → incident, 5 phút, timeout 60 giây, email chủ tài khoản. API monitor `804231770` giữ nguyên. Dashboard xác minh cả hai Up; ảnh `output/docker-release/uptimerobot-fe-api-up.png`.

## 106. Suspend FE cũ để ngừng dùng quota (2026-10-11)

- Người dùng yêu cầu tắt `rav26-web.onrender.com`. Đã suspend service `srv-db54miad0e5s73e6lg4g`, tên hiển thị `irishcoffee-t026-web` do lần đổi tên #101; giữ cấu hình/image, không xóa service. FE chính là service khác `srv-db559q942hec73fp6790`, hostname `irishcoffee-t026.onrender.com`.
- Automatic approval review chặn lần đầu vì nhầm tên hiển thị cũ với FE mới. Đã đối chiếu decision log và UI ID/hostname của cả hai service; thử lại đúng mục tiêu được chấp nhận. Render hiển thị `Suspended by you` và nút Resume Web Service; bằng chứng `output/docker-release/rav26-web-suspended.png`.
- Không thay API/CORS/monitor hoặc FE chính; origin cũ còn trong CORS để tránh redeploy ngoài phạm vi. Không đoán số giờ quota được hoàn lại; suspend chỉ ngừng giờ chạy tiếp theo.

## 107. Gộp FE/API một Docker Web Service (2026-10-11)

- Người dùng chọn lại phương án FE + BE chung một Docker image để giảm quota/service và bàn giao đơn giản, giữ URL `https://irishcoffee-t026.onrender.com`; không tạo repo frontend riêng hoặc Static Site. Thay topology hai service của #100; không nâng gói trả phí.
- FastAPI pilot phục vụ thêm React build và cấu hình public allowlist, cùng origin API. Node chỉ ở build stage, không Nginx hoặc process ML ở runtime. AI/decode/thumbnail/ZIP/PII và Kaggle tiếp tục ở worker Contabo theo §21, không chuyển sang API.
- Deploy vào FE chính hiện có, chỉ suspend API riêng sau khi health/readiness, routing và auth boundary bản gộp đạt; giữ service/image cũ để rollback. Chuyển monitor API sang hostname nhóm, hai monitor cùng service không nhân đôi giờ chạy.
- Worker Contabo, smoke login/S3 và DEP6/DEP8 còn chưa hoàn tất; hosting Live không mở admission pilot.
- Đã chuẩn bị target `combined`, static routes/public config allowlist, runbook và test. Pytest chọn lọc 7 pass/10 skip (PostgreSQL thật chưa cấu hình), ruff, FE tsc/19 test/build pilot đạt. Local HTTP landing/login/app/health 200, API missing 404 JSON, public apiUrl rỗng; RSS Windows smoke nhẹ 55,7 MB, không suy ra capacity Render. Docker engine lỗi 500/starting, restart chưa đạt; chưa build/push/deploy image gộp, chưa suspend API riêng.
- Khi đọc env hiện có phát hiện API cũ dùng username postgres qua pooler, trái hướng dẫn role pilot trong docs. Không sao chép DB admin; đã Save only 8 biến backend khác vào FE chính, giữ API URL cũ. Operator được mở ô DATABASE_URL role rav_pilot_api; chưa xác minh đã lưu. Không in credential vào log hoặc Git.
- Docker engine đã chạy lại; build/push `tonmeu/rav26:combined-b4e1feb166d2`, digest `9519f215c97b86eabee8a5fc3afce61c79bc7a8c4b11a2dd7daecfa4a3748df0`, container Linux healthy/routing smoke và no-heavy-import đạt, khoảng 45 MiB RAM tải nhẹ. Đã dọn container smoke, giữ image.
- Người dùng có DATABASE_URL nhưng dùng postgres admin, chưa nhớ/đặt mật khẩu role pilot; đã mở Supabase SQL Editor để người dùng tự ALTER ROLE/nhập credential. Một hàng DATABASE_URL trùng được thêm khi UI đồng bộ trễ; không tạo thêm credential, không thay password bằng agent. Rollout `dep-db5kcdd9fdbs73d6c11g` do Connect source image kích hoạt ngay đã được hủy (Canceled), source tag đã cập nhật; chờ env đúng mới Manual Deploy, chưa suspend API riêng/chuyển monitor.

- Hoàn tất rollout 2026-10-11: operator tự đặt mật khẩu role/lưu URL, đã kiểm username `rav_pilot_api` Session pooler TLS; agent đặt `VITE_API_URL` rỗng, Save only và Manual Deploy → Deploy latest reference. Deploy `dep-db5kj7vavr4c7394ubig` Live (30,4 giây), runtime config cùng origin, health/readiness 200 DB/schema ready, assets 200, chưa auth `/api/v2/me` 401 JSON, missing API 404 JSON; browser landing/app→login và deep link giữ next đạt.
- UptimeRobot từ chối monitor trùng URL/type; API monitor `804231770` chuyển `/health/ready` trên hostname nhóm để kiểm thêm DB/schema, FE `804231926` giữ `/health`. Cả hai hiển thị Up. Suspend API riêng `srv-db54lcjbc2fs73eai7u0`; FE cũ vẫn suspended. Readiness/FE assets sau suspend vẫn 200. Bằng chứng `output/docker-release/combined-render-live.png`, `combined-api-suspended.png`, `combined-monitors-up.png`. Tick DEP7-Combined; DEP7 tổng/worker/auth thật/S3/DEP6/DEP8 và backup/CI vẫn chưa hoàn tất; không push Git.

## 108. Bàn giao và tích hợp main (2026-10-11)

- Người dùng yêu cầu merge `duy-mvp/v2` vào `main`, commit và push. Sau fetch, `origin/main` là ancestor của nhánh tích hợp; không cần chọn lại code từ nhánh teammate khác.
- README thêm mục deployment public và link tới `COMBINED_WEB_RELEASE`, `DEPLOYMENT_STATUS`, `PILOT_RUNTIME_RUNBOOK`; index docs cập nhật snapshot 2026-10-11. Tài liệu ghi trạng thái hiện tại, env/roles, build/push/deploy/smoke, monitor/quota, rollback và những phần chưa làm.
- Kiểm trước merge: pytest toàn suite **273 pass, 124 skip** (chưa cấu hình PostgreSQL test và test có điều kiện); ruff xanh, TypeScript xanh, frontend **19/19**, build pilot đạt. Lần sandbox có lỗi quyền filesystem; chạy lại với quyền phù hợp đạt. CI org/billing chưa xác minh, không coi local tests là cloud E2E.
- Push Git không tự redeploy image Render. Image Live vẫn `combined-b4e1feb166d2`; worker Contabo, auth tài khoản thật, S3 cloud smoke, DEP6/DEP8, backup/restore và CI release vẫn còn thiếu.

## 109. S3 cloud và chuẩn bị worker CPU/MEDIA Contabo (2026-10-11)

- Tiếp tục main `ef316a2`, lát DEP7-Docker; không Astra/subagent, không push Git, đổi URL public, nâng gói hoặc reset Docker/xóa dữ liệu. GPU/Kaggle chờ CPU/media thật đạt.
- Operator xác nhận key AWS cũ đã thu hồi, key mới cập nhật Render và .env. Credential app không có IAM ListAccessKeys/GetBucketCors nên không coi rotation đã xác minh độc lập.
- Upload browser ban đầu bị CORS 403; sau sửa CORS lại bị IAM 403 vì policy cũ chỉ cho các prefix videos/datasets/models/results/temp. Bucket có CORS rỗng; đã thêm origin `https://irishcoffee-t026.onrender.com`, POST/GET/HEAD, headers Content-Type/Range, expose ETag/Accept-Ranges/Content-Range, max age 300. Block Public Access và ACL owner enforced giữ nguyên.
- Người dùng duyệt lưu thêm GetObject/PutObject/DeleteObject cho `aiia-irish-coffee/pilot-rav26/*`, giữ quyền cũ. Probe disposable 23.660 byte presigned POST trả 204 với đúng allow-origin; HEAD đúng size, GET khớp byte và dọn đúng object probe. Đây chưa phải upload/source/media E2E.
- Người dùng duyệt policy smoke CPU/MEDIA mới, giữ quota/caps: version `cpu-media-smoke-20261011` thêm MEDIA `media-1`, deadline VALIDATE_UPLOAD và RENDER_MEDIA 60.000 ms, giữ PROPOSE 600.000 ms. Hai profile chuyển version; không sửa policy cũ. Chỉ phục vụ lần đo nhỏ, chưa chứng minh video dài/capacity.
- Contabo root@161.97.164.25: operator nhập mật khẩu trong terminal và cài public SSH key deploy; private key nằm output ignored trên D. Host Ubuntu 24.04, 4 CPU/~8 GB RAM; cài Docker/Compose từ repository chính thức, không reset. DB `rav_pilot_worker` có LOGIN, dùng Session pooler TLS và password riêng; scoped worker.env mode 0600, data/output UID 1000 mode 0750 dưới `/opt/rav26-worker`. Không chuyển DB admin hoặc Kaggle token.
- Người dùng yêu cầu kiểm/tắt AWS không dùng vì credit giảm, sau đó xác nhận vẫn tiếp tục Contabo và giữ AWS cần cho dự án. Người dùng báo có thể đã tự xóa Lightsail instance; chưa kiểm chứng console/billing, chưa kết luận credit do Lightsail hoặc đã hết tài nguyên tính phí.
- Worker env/Compose/runbook và process liveness đã chuẩn bị; image và cloud job/media/restart vẫn phải kiểm trước tick DEP7-Docker. Auth tài khoản thật đã thấy username/quota, logout/expiry/A-B vẫn thiếu; DEP6/export/PII, GPU/provider, backup/restore và CI là gate riêng.
- Người dùng yêu cầu dừng sau khi file upload được. File ColdBrew 79.635.620 byte đã qua S3 và complete API: upload `722c237f-5fd8-49c5-8635-5b89d3b74ff4` VALIDATING, job VALIDATE_UPLOAD QUEUED theo policy smoke; source/media chưa READY. Đã dừng đúng tiến trình build worker trên Contabo, xác minh không còn build hoặc container chạy. Chưa bootstrap/bật queue, commit hoặc push; DEP7-Docker giữ chưa tick. Kiểm local 281 pass/124 skip, ruff và diff-check đạt; cloud DB test riêng chưa cấu hình. Docker local lỗi extract EOF, build VPS dừng ở export theo yêu cầu; không coi image release đã đạt.
- Người dùng yêu cầu dọn C/D rồi tiếp tục project. Dọn cache pre-commit và installer/pip temp cũ trên C, pytest temp/pip/npm cache trên D; không xóa dữ liệu/model/DB/bằng chứng hoặc đổi pagefile. C 8,45 → 9,33 GiB, D 16,70 → 18,01 GiB; báo cáo `output/storage-cleanup-20261011.json`.
- Tiếp tục build worker trên VPS: manifest `sha256:57fdfd2a282d5978f1a5a50ec03b86caddf7d373d3c2ae193d4944d1c3e2da9d`, Compose pin digest, bootstrap blocked=[], role `rav_pilot_worker`, CPU/MEDIA slots FREE. Bật worker CPU/MEDIA, container healthy, không token Kaggle. Job ColdBrew cũ quá hạn queue trong lúc dừng được chuyển FAILED/dọn staging đúng policy; không sửa deadline để làm giả thành công. Upload mới `6a10df0d-64fa-43c5-b019-9e264ff8ff4b` đủ 79.635.620 byte trên S3, frontend lỗi mạng ở complete; chưa source READY hoặc media E2E. Người dùng đã đăng nhập tài khoản thật, đang phối hợp upload lại.
- Kiểm mới: worker health/entry 16 pass, regression toàn repo 281 pass/124 skip (PostgreSQL test chưa cấu hình), ruff/diff-check đạt. Lượt sandbox treo TestClient đã dừng, lượt quyền phù hợp đạt; không coi tests bỏ qua là kiểm cloud DB. Chưa push hoặc tick DEP7-Docker.

## 110. Giảm độ trễ DB và CPU bonus cho smoke (2026-10-11)

- Người dùng phản ánh upload 80 MB chậm, duyệt cả pool PostgreSQL và bonus CPU 60 giây một lần cho tài khoản smoke. Tiếp tục trong DEP7-Docker; không đổi policy/tài khoản khác hoặc bật GPU.
- Trước tối ưu: VALIDATE_UPLOAD `68319fcd-caee-49f1-bb83-bbeeff7a0343` chờ queue 6,479 giây, attempt 77,684 giây, CPU receipt 7.446 ms; nguồn `24ee78f5-4869-4812-9c47-3f2cb7092eab` READY, 79.635.620 byte, 120 unit. DB từ Contabo: connect 1,811–1,823 giây, query 0,254–0,259 giây, transaction rỗng 2,322–2,338 giây; chưa tách trace toàn bộ DB/S3.
- Thumbnail cần 4.067 ms CPU trong khi base còn 2.554 ms: WAITING_RESOURCE, slot MEDIA QUARANTINED chờ quota reconcile đúng thiết kế. Bonus có adjustment_key idempotent và operator/reason; không reset usage/slot hoặc tăng base toàn hệ thống.
- Thiết kế pool cập nhật CONTRACTS §21.3 và architecture trước code: Psycopg pool lazy/thread-safe, mỗi PilotDB tối đa 4 kết nối, timeout 10 giây, transaction tường minh và SET LOCAL role/claims, loại kết nối hỏng, không tự retry write; đóng theo lifecycle instance/process. Cần test PostgreSQL thật reuse/RLS A→B→backend/rollback/concurrency trước release và đo lại cloud.
- Người dùng nâng yêu cầu bonus thành vài tiếng cho các tài khoản hiện có vì đều là tài khoản test: cấp 3 giờ CPU (10.800.000 ms) cho `duyla25clc` và `vuthien3002`, adjustment_key `test-cpu-3h-20261011:<user_id>`, reason/operator đầy đủ. Ghi qua operator Session pooler của đúng project; không đổi policy gốc, không cấp GPU hoặc tạo account mới. Đã đọc xác nhận mỗi record đúng 10.800.000 ms.
- Pool implementation/dependency `psycopg_pool` đã kiểm **405 pass/3 skip** với PostgreSQL 16.15 thật; reuse/RLS A→B→backend/rollback/broken connection/concurrency và store/quota/queue riêng **61 pass**, ruff/diff-check đạt. PostgreSQL test chỉ chạy trên D qua alias ASCII tạm R, đã dừng và gỡ alias sau test.
- Worker pool build đầy đủ trên VPS đạt; manifest `sha256:ef09e6a94b7c3b9aff9e7ed23d658ae38903cc4bf35672c60e272f4a5fd8874d`, bootstrap blocked=[], Compose pin digest rồi recreate theo grace 180s. Sau rollout role worker đúng, sources/receipts/media vẫn bền, hai slots FREE. Đo transaction lặp lại bằng cùng probe: 0,780–0,835 giây (trước 2,322–2,338), phần checkout gồm BEGIN 0,260–0,303 giây, query 0,260–0,280; chưa đo lại toàn upload 80 MB hoặc suy toàn bộ I/O là DB.
- Sau bonus, supervisor tự tất toán MEDIA 4.067 ms và trả slot FREE; lượt thumbnail mới SUCCEEDED, CPU receipt 620 ms. UI tài khoản thật có video ColdBrew, thumbnail và ~10.798 giây CPU khả dụng. Bằng chứng `output/contabo-release/ready-video-cpu-bonus.jpg`. API Render Live vẫn image `combined-b4e1feb166d2`, chưa có pool; chưa frame/context cloud smoke, GPU, DEP6/DEP8 hoặc release CI/backup, giữ DEP7-Docker chưa tick; không push.
- Kiểm cuối sau rollout: container healthy, `python -m src.rav.worker.health` exit 0; image size 3.532.184.071 byte. Reload app sau recreate vẫn tải thumbnail 320×185; SHA pilot_db.py/requirements-worker khớp giữa local và build context VPS. Không tạo image/cache mới trên C; Docker local không chạy, API pool image chưa release.

## 111. Log latency và redeploy upload (2026-10-11)

- Người dùng duyệt thêm log theo đề xuất từng chặng và deploy lại. Tiếp tục DEP7-Docker; deploy worker và combined FE/API (kèm pool #110), không push Git hoặc đổi quota/policy.
- CONTRACTS §21.4 và architecture cập nhật trước code: JSON spans correlation request/upload/job/attempt, DB checkout/execute/transaction, S3 và worker; browser Console không gửi telemetry. Không log secret/SQL/params/URL/tên file; lỗi chỉ loại exception. CPU span là chẩn đoán, receipt quota giữ nguyên. Queue lấy timestamp DB; spans lồng nhau không cộng dồn.
- Kiểm cuối **406 backend pass/3 skip** trên PostgreSQL 16 thật; **19 frontend pass**, tsc/build/ruff/diff-check đạt. Regression bắt tên cột queue sai, đã sửa thành job.created_at/attempt.started_at trước release. Lượt FE sandbox EPERM chạy lại với quyền phù hợp đạt. PG test đã dừng, gỡ alias R.
- Build/publish registry private hiện có từ VPS, archive loại env/data/output/cache; credential Docker Hub hiện có chỉ ở RAM VPS và đã dọn sau push. Combined `tonmeu/rav26:combined-timing-20261011`, digest `sha256:af13969836766ba3c5099238cedbcb564ff81b056ce26fb499bb226d3dcab88b`; Render pin digest, deploy `dep-db5pu6e7bikc73dlqvl0` Live 31,6 giây. Không đổi env/URL/plan/readonly registry credential. Smoke image non-root/import không ML/routes/header đạt; readiness live 200 DB/schema ready, Logs có correlation và S3 HEAD.
- Worker digest `sha256:2bbc8db80101f54057ce73a0e25375f5a35d51b1dd1f07d874c6eaeacbe9a691`, bootstrap blocked=[], Compose recreate grace 180s, healthy, CPU/MEDIA FREE sau smoke. Giữ image pool cũ và release.env backup để rollback, giữ volumes/receipts.
- Smoke qua browser tài khoản thật đúng file 79.635.620 byte: upload `1497a6d5-94c8-4ed6-a0db-a62d4e5985de`, job `d2686d49-d170-4833-8c8d-793f43a92bf1` SUCCEEDED, upload READY, CPU receipt 5.406 ms/GPU 0. Browser create 1,539 s, S3 transfer 72,379 s (~1,10 MB/s), complete 3,627 s, READY wait 62,319 s, tổng 140,943 s (gồm refresh). API HEAD 0,745 s, complete 3,166 s; correlation request/upload/job khớp.
- Worker queue 4,923 s, attempt sau claim 55,342 s, handler 30,860 s: S3 download+SHA 27,739 s, probe/hash core 0,575 s, unit grid 0,006 s; publish 16,411 s có 52 execute tổng 15,337 s. Đây là file trùng (dedup=true), không PUT canonical mới; không so với upload nguồn mới trước pool như cùng công việc hoặc kết luận mạng nhà là nguyên nhân duy nhất. Chặng S3 và publish nhiều round trip DB là nơi cần tối ưu tiếp; chưa tách mạng/disk/hash streaming ở download hoặc server lock/RTT ở từng execute.
- Evidence ignored `output/contabo-release/timing-release-evidence.json`, `timing-browser-smoke.json`, `timing-smoke-worker.log`, `timing-api-smoke.txt`, `timing-render-live.png`; hướng dẫn `docs/setup/UPLOAD_LATENCY_LOGS.md`. API pool nay đã release; frame/context, auth expiry/A-B, GPU/DEP6/DEP8/CI/backup còn thiếu, không tick toàn DEP7-Docker.
## 121. Ưu tiên luồng cơ bản trước mở rộng tham chiếu (2026-10-12)

- Người dùng yêu cầu lập plan và thực thi với ưu tiên upload/load/xử lý/tiền xử lý hoạt động tốt trước khi đầy đủ chức năng dataset/model tham chiếu.
- Lát DEP7-Basic-Stability trên nhánh local từ thien: shutdown có thời hạn, lỗi delete chặn heartbeat và SYSTEM import tạo ảnh ngoài kế toán, rồi fault/regression/E2E và số đo local.
- Shutdown được làm rõ trong CONTRACTS/architecture trước code; giữ mọi điều kiện lease/fence/receipt/quota, không suy dừng chỉ từ tín hiệu terminate.
- Sau khi hỏi rõ mục đích khóa cũ và cơ chế thay thế, người dùng xác nhận "ok tôi thấy cơ chế ổn, triển khai đi". Chốt transaction ngắn trước/sau DELETE+HEAD S3 và reservation/receipt bền trước PUT ảnh SYSTEM; cập nhật CONTRACTS §21.20–21 trước code. Không bỏ lease/fence, DELETE_PENDING hoặc điều kiện publish.
- Không merge main, push, deploy hoặc mở tính năng tham chiếu trong lát này. Plan: `docs/archived/2026-10-13/docs/backlog/BASIC_FLOW_STABILITY_PLAN.md`.

## 120. Chốt phạm vi F4 của DEP7-Frames (2026-10-12)

- Trước F4 người dùng chọn: (1) làm dọn cache đĩa worker ngay trong F4 (trần `RAV_PILOT_CACHE_MAX_BYTES`, LRU theo mtime, chừa file vừa dùng/10 phút); (2) nút chuẩn bị ảnh ở cả Kho dữ liệu và ô ảnh `NOT_PREPARED`; (3) chỉ đo local, không deploy cloud để đo; (4) giữ artifact MEDIA thumbnail/frame cũ, ghi Hướng tới.
- Trưởng nhóm dev nhắn qua người dùng: test pilot ở local cho kỹ và đúng luồng. F4 thêm test end-to-end local qua API HTTP thật của app pilot, worker spawn thật, S3 moto server và PostgreSQL thật: upload → ảnh → lưới/frame/clip → nguồn cũ chuẩn bị → SYSTEM CLI → cô lập A/B → crash process con giữa job → xóa nguồn.
- Kết quả F4 (local): 474 backend PostgreSQL 16 thật đạt, 1 skip, lỗi opencv có sẵn; FE 30 test, tsc/build đạt. Thêm test VFR thật (khoảng pts 50/150 ms) cho tách ảnh theo index stream. E2E local đạt 1 lượt, 5 lượt và 1 lượt video thật 354 s; hai lỗi ban đầu khi chạy nhiều lượt là lỗi của test (list S3 tối đa 1000 key; lẫn nguồn giữa các lượt), đã sửa test, không đổi code sản phẩm. Số đo local 5 lượt (video 30 s 640×360): upload→READY p50 6,8 s/p95 7,4 s; thumbnail đầu p50 99 ms; lưới 48 ảnh p50 1,35 s; frame 45 ms; clip mới p50 715 ms, cache 37 ms; nguồn cũ 60 s chuẩn bị ảnh có giết process CPU giữa job 20,7 s và không để ảnh mồ côi. Video thật 354 s: upload→READY 81,0 s qua moto HTTP. Không phải số cloud; không có A/B cùng hạ tầng. Phiên không có công cụ trình duyệt nên chưa bấm thử UI thật; FE kiểm bằng vitest (suất HTTP, ưu tiên, NOT_PREPARED, dừng poll, thế hệ) + build. Release/rollback chuẩn bị ở `docs/setup/FRAMES_RELEASE.md`; số policy cloud trong đó là đề xuất chờ duyệt.

## 119. Chốt thiết kế ảnh lưu sẵn DEP7-Frames (2026-10-12)

- Người dùng giao triển khai plan #117 trên nhánh riêng từ `8d3135a`, hỏi lại chỗ chưa rõ. Sau khi pull main, người dùng chốt cả 10 đề xuất: chia F1 (schema + upload có ảnh + media lookup) → F2 (nguồn cũ/SYSTEM + xóa) → F3 (tách process + cache) → F4 (FE polling + số đo); tách ảnh ngay trong `VALIDATE_UPLOAD`, nguồn chỉ READY khi đủ bộ; bảng `rav_pilot_frame_set` + `rav_pilot_frame` (migration 010) thay vì một artifact mỗi ảnh; key theo `frame_set_id` mỗi attempt; `max_side` map về ảnh có sẵn (≤ 320 thumbnail, còn lại ảnh đầy đủ); nguồn chưa có ảnh trả `NOT_PREPARED`, chuẩn bị explicit bằng `PREPARE_FRAMES`, SYSTEM qua CLI operator chỉ tính GLOBAL; giữ dung lượng theo lô đúng byte thật; khóa policy mới trong `chunk_bounds_by_kind`; process chính chỉ supervisor, CPU/MEDIA mỗi loại một process spawn; cache raw `.part` + `os.replace` không lock; FE giữ suất chỉ trong HTTP call, ưu tiên ảnh/clip đang xem.
- Theo quy ước có sẵn (runbook §5, PROPOSE dùng `chunk_bounds_by_kind`), policy bất biến do operator thêm version mới sau khi đo; migration không chèn số. `SourceDTO.frames` dùng lại kiểu `PrepState` của DINO/CLIP thay cho kiểu trạng thái mới; RLS bảng ảnh theo mẫu artifact (chỉ role backend, API lọc owner/scope).
- Endpoint chuẩn bị nguồn cũ: người dùng đồng ý dùng lại `POST /sources/{id}/preparations` với `feature: "FRAMES"` thay vì endpoint mới. Xử lý process con crash (đầu F3): người dùng hỏi cách đơn giản hơn; đã so sánh (A) mỗi queue một container, (B) process chính tự khởi động lại process con, (C) thread (loại vì `process_time` cộng CPU các thread và lỗi native làm chết cả process). Người dùng chọn B: chờ tăng dần 1 → 60 s, quá 5 lần/10 phút thì process chính thoát mã 3 cho Docker khởi động lại container; GPU cũng đi qua cùng cơ chế (trước đây spawn lại không chờ). Rà cache: `handlers._raw` và `session_context.cached` cùng ghi `raw-cache` với hai lock khác nhau → gom một helper an toàn liên process.
- Kết quả F3 (local): 464 backend PostgreSQL 16 thật đạt, 1 skip, lỗi opencv có sẵn. Smoke spawn thật (PostgreSQL + moto server dùng chung qua `AWS_ENDPOINT_URL_S3`) bắt được lỗi process chính không tạo workdir sau khi tách (trước do `make_handlers` tạo) — đã sửa. Giết process CPU → bằng chứng PID/start token cho thấy đã dừng, chỉ CPU được khởi động lại, MEDIA giữ PID; dừng worker → process con thoát, `process.json` bị xóa. Upload video 120 s 640×360 (tách ảnh, CPU) đang RUNNING thì clip context (MEDIA) xong 0,89 s; attempt CPU/MEDIA có PID khác nhau và khác process chính. Đây là bằng chứng không chặn nhau, chưa phải benchmark cloud p50/p95. Process con rảnh ≈ 63 MB RSS mỗi process; kết nối DB tối đa 12 (16 có GPU) thay vì 8. Thêm `psutil` vào requirements cho test spawn. Chưa có eviction cho cache `raw-cache`/`model-cache`/`feature-cache` (đã thiếu từ trước, plan §7 có nhắc): cần người dùng quyết có làm trong DEP7-Frames hay ghi Hướng tới. Contract: CONTRACTS §21.20; chưa migrate/deploy/push.
- Khi chạy F1, test xóa nguồn lộ ra `DELETE_SOURCE` chưa xóa ảnh (ảnh riêng tư còn trên S3, dung lượng còn bị tính). Người dùng chọn kéo phần xóa ảnh vào F1 thay vì commit F1 với test xfail: inventory gồm ảnh, xóa theo lô 100 key/8 đồng thời, mỗi lô một transaction fenced, publish trừ persisted một lần và đánh frame set DELETED.
- Kết quả F1 (local, chưa cloud): 450 backend trên PostgreSQL 16 thật + S3 moto đạt, 1 skip; `test_cloud_embedding` lỗi thiếu metadata opencv-python-headless có sẵn ở baseline `8d3135a`. Test mới: bộ đủ theo ordered IDs + byte JPEG trùng encode từ `read_exact`, fps thấp tới EOF, dedup/nguồn trùng chưa có ảnh, race publish (bộ thua cleanup, không tính hai lần), PUT lỗi giữa lô, hủy giữa lô, mất lease rồi retry, policy thiếu khóa, vượt byte/dung lượng, xóa nguồn lỗi giữa lô rồi retry. Chưa có test VFR thật (fixture chỉ sinh CFR).
- Số đo local (không phải số cloud, không claim tăng tốc): video 354 s 640×360 → 1.417 unit, 2.834 ảnh, FRAME 46,0 MB + THUMBNAIL 16,2 MB; decode `read_exact` ≈ 5,0 s + encode ≈ 6,1 s, RAM đỉnh 83 MB; upload→READY 28,1 s hai lượt (PUT/HEAD vào moto local 12,9–13,4 s, 23 lô × 2 transaction, publish 0,55 s). Video 152 s 854×480 → 610 unit, 22,3 MB, decode+encode ≈ 11,5 s, RAM đỉnh 90 MB. Video `data/youtube5m` trong AGENTS không có trên máy này. CPU process_time của job đo được 39–42 s cho video 354 s nhưng gồm cả moto chạy cùng process (`mock_aws`), nên chỉ là cận trên; dù vậy decode+encode riêng đã ≈ 11 s CPU, vượt xa ước lượng 1 s của policy test: policy cloud phải nâng `profiles_by_kind.VALIDATE_UPLOAD.cpu_ms` và `run_deadline_ms_by_kind.VALIDATE_UPLOAD`, nếu không job dừng vì vượt ngân sách. Evidence ignored: `output/frames-bench/*.json`.
- F2: `PREPARE_FRAMES` dùng lại `frame_writer`/raw cache; enqueue dedup theo input và ghim nguồn (xóa giữa chừng → `resource_in_use`), publish khóa nguồn, cần còn READY/đúng owner/raw, không tăng `resource_version`. CLI SYSTEM theo mẫu import #90: dry-run + `inventory_hash`, `frame_set_id` uuid5 theo nguồn/nội dung/spec/encoding nên key tất định, PUT trước rồi một transaction/nguồn; chạy lại bỏ qua nguồn đã có bộ, không tính GLOBAL hai lần. CLI không đi qua receipt worker: crash giữa PUT và DB để object ở key tất định, lần chạy sau ghi đè đúng key. Kết quả: 455 backend PostgreSQL 16 thật + S3 moto đạt, 1 skip, lỗi opencv có sẵn. Chưa chạy CLI/backfill trên cloud.

## 118. Đổi hostname nộp dự án theo c4-app-026 (2026-10-12)

- Người dùng yêu cầu đổi và deploy URL `https://c4-app-026.onrender.com/app`, thay hostname IrishCoffee cũ. Giữ service/image/dữ liệu hiện có; cập nhật origin API/S3 và monitor tương ứng, không đổi worker hoặc triển khai plan frame #117 trong lát này.
- Rollout đạt: service mới `srv-db68u7d9fdbs73f8v890`, deploy `dep-db68u7t9fdbs73f8va4g` Live 20,4 s, cùng image `724119b1b640…`. HTTPS routes/assets/runtime config và DB/schema ready 200; auth duyla25clc đọc hai source/session và thumbnail S3 đạt. Hai monitor đã lưu URL mới; service cũ Suspended và CORS bỏ origin cũ. Kiểm HTTPS/readiness/S3 preflight sau cutover đạt. Chưa upload file mới hoặc claim tăng tốc; chi tiết/evidence/rollback: `docs/archived/2026-10-13/docs/setup/HOSTNAME_MIGRATION.md`.
- Dashboard xác minh đổi Name không đổi Render Subdomain cũ. Thực hiện migration cùng image/cấu hình sang service Free mới tên c4-app-026, giữ DB/S3/worker và quyền hiện có; chỉ suspend web service cũ sau khi service mới được kiểm. Không tạo paid plan hoặc đổi credential/quota.
- Người dùng đã xác nhận rõ chuyển 15 biến cấu hình hiện có sang Render mới, cùng workspace/project/Production, giữ role rav_pilot_api và AWS keys scoped; không DB admin/Kaggle hoặc log/Git secret. Đã đăng nhập AWS qua Chrome external. CORS thêm origin mới, giữ origin cũ tạm trong cutover; gỡ origin cũ sau khi service mới đạt.

## 117. Chuyển phục vụ ảnh sang frame lưu sẵn trên S3 (2026-10-12)

- Người dùng yêu cầu tách frame trong bước xử lý video và lưu S3; tạm bỏ decode frame từ video để phục vụ ảnh. Cho phép chọn nhịp lấy mẫu hợp lý thay vì bắt buộc 5 frame/giây; đồng thời tối ưu worker theo phân tích latency.
- Người dùng xác nhận riêng giữ clip ngữ cảnh từ video gốc; chỉ bỏ decode khi phục vụ thumbnail/frame.
- Chi tiết đề xuất ở `docs/archived/2026-10-13/docs/backlog/FRAME_MATERIALIZATION_PLAN.md`: giữ lưới baseline 4 fps, materialize JPEG full + thumbnail, receipts/DB theo lô, reserve dung lượng ảnh trước PUT, publish đủ bộ, backfill explicit và tách CPU/MEDIA/supervisor. Các chi tiết này còn để duyệt, không ghi thành contract đã chốt hoặc claim đã triển khai.
- Lượt này chỉ lập thiết kế để duyệt; chưa thay CONTRACTS/architecture/code, chưa migrate/backfill/release cloud hoặc push.

## 116. Kiểm độ mượt và hoàn thiện latency (2026-10-12)

- Người dùng xác nhận duyla25clc là tài khoản admin, giữ quyền Vận hành #115; yêu cầu làm tiếp chức năng còn thiếu và kiểm tốc độ thật. Chọn lát DEP7-Latency, trước hết sửa viewer chưa hiển thị PENDING/FAILED như UI_DESIGN Media State đã quy định. Không đổi phạm vi export/PII hoặc bỏ gate của DEP6.
- Baseline auth UI trên phiên kiểm thử YOLO #114: thumbnail có cache cấp URL mất 6–9 s, clip context lần đầu 49,334 s, ảnh gốc 35,933 s, hai job RENDER_MEDIA SUCCEEDED. Video 6 s đã có readyState=4; chưa coi mọi media/latency đạt.
- Người dùng đồng ý tối ưu pipeline và kiểm thử/deploy. Cập nhật §21.3/architecture trước code: chỉ gom nhóm ghi tất toán quota/storage và đóng attempt, không đọc kết quả giữa chừng; giữ transaction, thứ tự khóa/SQL, quota, rollback và không retry ghi. Đồng bộ lỗi trước caller tiếp tục; tracing db.pipeline gồm sync, db.enqueue không phải thời gian execute.
- Probe chỉ đọc từ Contabo tới Supabase: 3 lượt × 20 SELECT 1, sequential median 5.207,6 ms, pipeline median 259,3 ms (libpq 18.0.6). Đây là đo overhead round trip, không phải benchmark publication hoặc cam kết tốc độ job tăng 20 lần.
- Smoke viewer thật phát hiện clip hết file đã tua về đầu nhưng video bị pause bởi sự kiện ended; sửa resume ở ended theo hành vi phát lặp đã có trong thiết kế, rồi kiểm lại trên browser. Không đổi cửa sổ clip hoặc enqueue thêm inference.
- Kết quả cuối: 434 backend pass/3 skip (loại hook lỗi sẵn), 24 frontend pass, ruff/tsc/build/diff-check đạt. Test mới kiểm lỗi deferred rollback quota/storage, pool reuse và replay. Worker `350d40c7d481…` healthy; FE/API cuối `724119b1b640…`, Render `dep-db5t295ckfvc73bbsap0` Live 20,3 s, readiness DB/schema 200; không migration, không push Git.
- Smoke thật: context mới 41,533 s, publish 14,918 s; upload tổng hợp 1395 byte READY tổng 32,340 s/S3 1,095 s/READY wait 24,985 s, publish 13,164 s. Context, validation và thumbnail đều SUCCEEDED, CLOSED/FINAL/cleanup true, slots FREE. Mở lại clip cache 1264 ms; sau reload + 16 ảnh vẫn chờ URL 8739 ms. Clip lặp qua nhiều vòng, dừng và tua về t0 đúng. Các mẫu trước/sau khác anchor/video, không là A/B hoặc p95 và không claim video 80 MB nhanh hơn; media lần đầu chưa đủ mượt. Release/rollback/evidence tại `docs/setup/LATENCY_QA.md`; ưu tiên tiếp round trip đọc/cấp URL và decode, export/PII/CI/DR/gate pilot còn riêng.

## 115. Màn vận hành và tracing upload/job (2026-10-11–12)

- Người dùng đồng ý đề xuất màn vận hành chỉ đọc + tracing upload/job, hỏi đủ điều kiện deploy chưa. Thực hiện một lát DEP7-Ops theo §21.19; chưa làm cấp quota/hủy job/khóa tài khoản. Quyền đọc operator chỉ allowlist server, tài khoản duyla25clc; không public logs hoặc dùng credential admin trong API.
- Hạ tầng release đã có Render/Contabo/Supabase/S3/Kaggle; phải kiểm truy cập, backup trước migration telemetry, regression/isolation và cloud smoke. Chưa coi toàn DEP7/DEP8 sẵn sàng (media/DB ổn định, export/PII, backup drill/CI/multi-account còn thiếu).
- Ngày 2026-10-12 automatic approval review chặn backup vì thiếu ủy quyền export metadata. Người dùng đã đồng ý rõ dump các bảng rav_pilot_* qua Contabo, lưu output/operations-release/pilot-before-ops.dump; không video, không Supabase Auth. Không coi backup pilot này là diễn tập DR toàn hệ thống.
- Người dùng xác nhận riêng bật quyền Vận hành chỉ đọc cho duyla25clc khi deploy: đọc metadata hai account test, không file video/secret hoặc điều khiển. Backup đã restore được trong PostgreSQL 17 networkless; migration 009 additive đã apply, giữ checksum migration cũ. Release digest và rollback ghi tại `docs/setup/OPERATIONS_RUNBOOK.md`.
- Kiểm backend PostgreSQL thật 432 pass/3 skip (loại test hook lỗi sẵn AGENTS), frontend 22 pass, tsc/ruff/diff-check đạt. Worker CPU/MEDIA + GPU collector healthy, heartbeat fresh không drop/flush failure; FE/API Render `dep-db5sckcs728c738sosrg` Live 20,4 s, readiness DB/schema 200. Không push Git.
- Smoke auth UI upload 1556 byte READY, validation SUCCEEDED, attempt CLOSED/usage FINAL/cleanup true; màn Vận hành đọc được hai account và 138 events đúng correlation browser/API/worker. Tổng 37,992 s, S3 browser 1,121 s, worker publish 16,408 s với nhiều DB round trip ~0,26 s; cần lát tối ưu latency tiếp, không claim upload nhanh hơn hoặc toàn DEP7/DEP8 hoàn tất. Log lưu 7 ngày từ lúc bật, không dựng timeline job cũ. Bằng chứng và release/rollback tại runbook Vận hành.

## 114. Hoàn thiện tính năng cloud trong lúc người dùng thử CLIP (2026-10-11)

- Người dùng yêu cầu làm tính năng/DEP còn thiếu. Chọn lát DEP7-YOLO: import model SYSTEM và kiểm YOLO_BATCH thật theo §21.17/#97, dùng checkpoint nano COCO hiện có từ #71; không đổi thuật toán hoặc training provenance, không gán COCO thành BDD.
- Kiểm mẫu SYSTEM trước: hai legacy session trong inventory #90 không có trong SQLite hiện tại và bản checkout review; hai video gốc #90 cũng chưa có trong data/videos. Không apply plan dry-run cũ với nguồn demo khác. Đã hỏi đường dẫn dữ liệu gốc, tiếp tục lát không phụ thuộc mẫu.
- Đã dry-run và apply CLI import hiện có: model SYSTEM `28e3a1b2-50f4-5933-9a2b-324ac9d5157d`, YOLO11n COCO (Ultralytics), 80 lớp, 5.613.764 byte; SHA `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`, validation INFERENCE_VERIFIED, engine 8.3.228. S3 key content-addressed trong `pilot-rav26/system/models/yolo/`; không cần đổi image đang chạy hoặc push Git.
- Smoke qua auth UI thật account duyla25clc trong tab nền: phiên `9af53a87-0f69-4e00-a11d-0388ee542234`, nguồn 0909; PROPOSE `2cf9046e-1a4b-47fb-92d0-284e23798899` SUCCEEDED. YOLO `a03c5814-822d-4dea-8ede-7ef21f1ffa07` SUCCEEDED: 16 ảnh đúng thứ tự picks, 6 detections có box/score hữu hạn hợp lệ, model SHA khớp; session/proposal state_version vẫn 1, một proposal, không decision; usage FINAL/cleanup_done=true. Giao diện hiển thị tổng và số box từng ảnh; reload/chọn lại model đọc evidence đã lưu, không tự enqueue YOLO.
- YOLO lần đầu từ enqueue tới SUCCEEDED 167,254 s, CPU process meter 250.046 ms, GPU 0. Đây là smoke đúng chức năng, chưa đạt gate tốc độ; chưa có timing tách tải/cache/decoder/model/inference nên không quy toàn bộ độ trễ cho inference. Code decoder exact có reset từ đầu khi anchor đi lùi theo thứ tự rank; cần đo riêng và tối ưu ở lát sau, giữ đúng frame_idx và thứ tự evidence. Thumbnail một số ảnh chưa tải được; reload evidence gặp một lần dependency_unavailable PostgreSQL, chọn lại model thì đọc được đủ 16 ảnh/6 đối tượng đã lưu. Media/frame/context và độ ổn định API E2E vẫn chưa đạt, không tick DEP7/DEP8 tổng.
- Trong lúc smoke đã xác minh CLIP của người dùng `96f8fdaa-3154-49ee-961b-20881e2c4705` SUCCEEDED, STOPPED, usage FINAL, cleanup_done/session_stop_verified=true. Không hủy hoặc thay job test người dùng.
- Evidence ignored: `output/pilot-import/yolo-cloud-dry-run.log`, `yolo-cloud-apply.log`, `yolo-cloud-evidence.json`, `verify_yolo_cloud.py`, `yolo-cloud-ready.png`; proof có thể chứa ảnh video của account test, không commit dữ liệu này.
- Kiểm cuối: assertion cloud evidence đạt, 11 regression `tests/test_batch_yolo.py` đạt, ruff toàn src/tests và diff-check đạt. Lượt pytest trong sandbox bị treo, đã dừng và chạy lại ngoài sandbox với thư viện native thành công; không đổi app code, không chạy lại toàn suite hoặc coi cloud E2E tổng đã đạt.

## 113. Setup Kaggle GPU theo yêu cầu người dùng (2026-10-11)

- Người dùng yêu cầu cấu hình Kaggle ngay vì DINO chờ lâu. Contabo chưa có token hoặc slot GPU: job chờ, chưa inference. Token hiện có xác minh account `meowluvmatcha`, quota SDK 30 giờ tổng/0 dùng/0 reserved tại thời điểm probe; không suy queue thành đang tính.
- Kết quả cuối: worker pin `sha256:ae54f3b033e116e25d788d93a5dcb5f8df6545aa03bf072ea2928e1895900ee9`, healthy, rollback `output/release-pre-gpu.env`. Token chỉ worker.env 0600; cache Torch Hub/checkpoint 88.283.115 byte trên volume Contabo. Policy mới/bonus 3 giờ GPU mỗi account test đã áp dụng idempotent. Không đổi Render hoặc push Git.
- DINO 0909 job `0932000d-4bc2-4056-8487-e371ec92cef7`, session T4 `357399124`: remote 539/539, job SUCCEEDED, artifact `ba7c0874-e49d-4152-94b5-3d2b3b9698f2` READY 901.976 byte, validation IDs/vector/pHash/runtime đạt, GPU 121.795 ms, CPU 45.459 ms, STOPPED/usage FINAL/cleanup_done=true, CPU/GPU/MEDIA FREE. Không dùng status notebook để xác minh stop.
- Kiểm cuối 28 regression đạt trên PostgreSQL thật (GPU lifecycle/lookup/schema/worker entry/OpenCV assets), ruff/diff-check đạt; PG đã dừng/gỡ alias. Smoke session trước boot 29,73 s rồi 177,36 s chứng minh độ trễ khởi động provider biến thiên, không phải benchmark inference. Job CLIP cũ `9fc94b9f…` FAILED lease_lost trước launch; không hủy job đó (auto-review từ chối) và chưa smoke CLIP thật. Evidence ignored: `gpu-regression-verified.log`, `kaggle-lifecycle-fixed.log`, `gpu-final-monitor.log`.
- Tiếp tục lát DEP7-GPU, dùng lifecycle/slot/quota có sẵn §21.14; credential chỉ worker, cache DINO từ ổ D sang volume Contabo. Phải kiểm notebook private của slot, trạng thái session thật và stop verified trước mở queue; UNKNOWN giữ nguyên gate.
- Smoke T4 session `357392663` cuda=true, boot 29,73 s; status notebook COMPLETE trong lúc GPU vẫn chạy: gate cũ sai. Người dùng duyệt sửa đối soát theo session ID. SDK logs stream mô tả SSE khi chạy / JSON log lưu khi kết thúc; probe session sau cancel trả HTTP 200 application/json rỗng. Không coi cờ stop_verified của adapter cũ là bằng chứng. Sửa CONTRACTS/architecture trước code; lấy ID từ attempt gần nhất/evidence bootstrap, thiếu handle sau intent giữ UNKNOWN.
- Worker cài `opencv-python`, helper assets chỉ đọc metadata `opencv-python-headless` nên DINO sẽ lỗi trước launch. Sửa lấy version wheel OpenCV hiện có (headless ưu tiên, regular fallback), vẫn pin đúng version đó trên Kaggle và giữ kiểm pixel/runtime parity; không nới validation.
- Rollout thật phát hiện target multiprocessing spawn nằm trong `__main__` khi chạy `python -m`, child không unpickle được. Dùng lại chính hàm từ module importable trước tạo process; không đổi tách CPU/GPU hoặc tính quota. Test entry và chạy child thật trên Contabo là gate.
- Theo yêu cầu trước về vài giờ cho hai account test, bổ sung 3 giờ GPU mỗi account bằng bonus idempotent, giữ guard ngân sách Kaggle toàn cục. Policy mới dự kiến `cpu-media-gpu-test-20261011`: slot GPU `gpu-1`, estimate GPU 600.000 ms/CPU 5.000 ms/output 8 MiB, deadline GPU và URL 1.800.000 ms. Đây là cap smoke bảo thủ, chưa phải benchmark. Chỉ áp dụng sau lifecycle gate; job policy cũ sẽ hủy/retry qua service, không sửa snapshot.

## 112. Retry source delete có kiểm tra (2026-10-11)

- Người dùng đồng ý retry khi job xóa trước thất bại và quota/slot đã đối soát; nguồn giữ DELETE_PENDING tới khi S3 xóa xong. Key cũ replay; key mới cần CAS, terminal job, usage FINAL, cleanup_done, không slot/hold/pin treo. Không khôi phục READY sau partial delete.
- DEP6a triển khai handler raw/artifact đúng owner, kiểm lease/fence/version và RESTRICT, DELETE + HEAD; publication nguyên tử trừ persisted USER/GLOBAL một lần và tombstone audit. UI theo dõi receipt, cho thử lại khi lỗi. DEP6 tổng/export/PII vẫn chưa hoàn tất.
- Kiểm code: **410 backend đạt, 3 bỏ qua** trên PostgreSQL 16 thật + S3 moto; **20 frontend đạt**, ruff/tsc/build/diff-check đạt. Test gồm partial delete rồi retry, cleanup chưa đối soát, lease hết hạn không DELETE, media + raw, owner khác và API hủy job chờ. API không được mở thêm quyền: queued cancel/receipt worker cũ chiếu trạng thái terminal từ job. Image worker/combined build trên VPS; smoke non-root/routes/import nhẹ đạt. Cloud rollout và kiểm S3 thật ghi tiếp sau release.
- Code commit `a04f5da`. Worker Contabo pin `sha256:d8dfc32b16eff2135ce848913cc548fae9d8cc25f4095e6390e58481a51ffdba`, bootstrap blocked=[], recreate grace 180s, healthy. Combined `tonmeu/rav26:combined-delete-20261011`, digest `sha256:0c547501c7758c28e86614963e59c9d997e9054034e6cc6720f279f585139a66`, Render deploy `dep-db5qg3flot8c73frqcl0` Live 20,6 giây; readiness 200 DB/schema ready. Registry credential RAM đã dọn, giữ release/image #111 để rollback. Không đổi env/plan/quyền DB.
- Cloud smoke qua services operator (không phải auth HTTP E2E): video tổng hợp mới 1 giây/1.556 byte trên account test `vuthien3002`, source `52b0fc66-8782-4b7e-b803-ffc93f47972c`; upload READY rồi job delete `bc01fdb8-9095-46c3-9e2b-8fc29dd96324` SUCCEEDED, HEAD S3 absent, source DELETED, receipt released_bytes=1556, usage FINAL/cleanup_done=true, CPU/MEDIA FREE. Chỉ nguồn synthetic mới này bị xóa; ColdBrew/0909 của người dùng giữ nguyên. FE thật reload thấy nút Thử lại xóa cho ColdBrew. Evidence ignored `output/contabo-release/delete-cloud-evidence.json`, `delete-cloud-smoke.log`, `delete-render-live.png`. PG local đã dừng/gỡ alias R; không push Git. Tick riêng DEP6a; export/PII/GPU/auth isolation/DEP8/backup/CI còn thiếu.

## 122. Sửa và kiểm lại DEP7-Basic-Stability sau review AGY (2026-10-13)

- Người dùng yêu cầu sửa các findings và kiểm tra lại. Giữ lát hiện tại, không push/merge/deploy.
- Làm rõ recovery #121: operator-confirm bao gồm exact inventory receipt; cleanup-only đối soát khi source/raw/policy đã đổi, vẫn kiểm writer dừng/advisory/READY. Không mở quyền hoặc bỏ preflight import mới.
- Giới hạn temp kiểm trước tải raw và trước ghi bytes encode. PLAN chưa tick toàn lát tới khi có bằng chứng các gate; browser và đo A/B riêng, không suy test HTTP thành UI mượt hoặc local thành cloud nhanh.

## 123. Ghi backlog va release ban hien tai de thu luong du lieu (2026-10-13)

- Người dùng yêu cầu ghi phần cần làm tiếp vào plan, rồi deploy bản hiện tại để thử độ mượt các luồng dữ liệu. N1–N6 là backlog chưa implement: latency/tracing, quota estimate, reference catalog/binding, model-guided selection, evidence/report, quality/frame đại diện và vòng learning ngoài hệ thống.
- Cho phép release bản đã kiểm f0d278e cùng prerequisite migration/policy cần thiết và smoke nguồn mới; giữ rollback, không tự backfill mọi nguồn user/SYSTEM. Không suy authorization deploy thành push Git/merge main.
- Rollout thực tế phải chặn admission/dừng worker rồi chuyển combined trước worker vì API cũ enqueue upload thiếu payload.frames; kiểm không active job trước cửa sổ, không đổi snapshot của job. Không thay thiết kế pipeline/quyền/quota. Source image b195c61, Render Live/worker healthy và smoke thật được ghi ở [FRAMES_CLOUD_QA](../../setup/FRAMES_CLOUD_QA.md). Số cloud còn chậm; ưu tiên N1 trước mở tính năng tham chiếu.

## 124. Lựa chọn tạm và tối ưu proposal trước tính năng mới (2026-10-13)

- Người dùng chốt chọn/bỏ chọn chỉ giữ status tạm, không cần giữ khi reload/rời phiên trước Đề xuất; yêu cầu viết plan cả submit theo lô và cache VPS. Không dùng cmind theo yêu cầu trực tiếp.
- [Plan N1-Proposal](../../backlog/PROPOSAL_LATENCY_PLAN.md) P0–P5: một request lưu lô quyết định + enqueue, polling đúng job, giảm publish round trips, cache metadata/embedding khi đo cần, local/cloud gates. Đây là plan chưa implement; API/signature/locking/cache design chi tiết phải cập nhật CONTRACTS/architecture/UI trước code, không giả contract đã đổi.
- Không lưu draft qua localStorage/autosave; giữ quota/fence/CAS/idempotency/source guards và local v1 hiện tại. Cache disk đã có, không mirror mọi JPEG hoặc chỉ cache 16 embedding vì scorer có thể cần toàn pool. Cache RAM là lựa chọn theo số đo, không mặc nhiên triển khai.

## 125. Rà kiến trúc và ước lượng deployment toàn hệ thống (2026-10-13)

- Người dùng yêu cầu rà rộng hơn một luồng proposal, gồm hướng tối ưu và nền tảng cho tính năng còn thiếu. Cho phép đọc code/docs/config và measurement nhẹ read-only; không suy thành duyệt migrate host/DB, đổi queue/sampling/contract hoặc deploy code.
- [Audit/lộ trình A0–A5](../../archived/2026-10-13/docs/backlog/ARCHITECTURE_DEPLOYMENT_REVIEW.md) nối các plan N1–N6: A0 inventory/capacity; A1 DB read/write/batch toàn luồng; A2 scheduling/resources; A3 artifact/cache; A4 estimator/release; A5 reference/model/report/PII foundations. Các lựa chọn thiết kế ở report là đề xuất chờ chốt trước code.
- Worker live warm SELECT 1 median 260,539 ms/5 lượt, cache raw/feature/model đã có; số liệu không tách pure RTT hay chứng minh query count mỗi job. Ưu tiên giảm DB round trips/khóa trước tăng service/concurrency hoặc mua/chuyển provider. Không dùng cmind, không chạy lại full suite hoặc đổi live trong lượt này.

## 126. Tổng hợp solution không áp giới hạn skill (2026-10-13)

- Người dùng yêu cầu bỏ các skill đang giới hạn cách suy nghĩ, rà lại và tổng hợp giải pháp từng vấn đề. Lượt này không áp skill và không thực hiện code/deploy.
- [Architecture solutions](../../backlog/ARCHITECTURE_SOLUTIONS.md) đề xuất business transaction functions DB, read/batch commands, events/SSE, resource-separated lanes, immutable bundles/cache, readiness/IO batch, GPU reuse, telemetry separation, placement/estimator và versioned reference/workflow/release. Đây là lựa chọn đề xuất, không phải người dùng đã duyệt các thay đổi thiết kế hay mục tiêu SLA.
- Không giữ lệnh cấm Redis/broker/FAISS/DB self-host từ plan như giới hạn kiến trúc; công nghệ chọn theo volume/mục tiêu/operations. CONTRACTS hiện hành vẫn áp dụng tới khi chốt thiết kế trước implementation.

## 127. Triển khai compute runtime và đánh giá encoder (2026-10-13)

- Người dùng duyệt triển khai runtime CPU song song/model owners, cập nhật docs và thử SigLIP2/Gemma4; cho phép test Contabo trực tiếp. Không dùng skill theo yêu cầu trước. Giữ test candidate cô lập, không suy thành rollout live hay push Git.
- CPU lanes opt-in giữ DB slot/lease/quota authority, model owner là process sống lâu với thread/RSS budget; native là tùy chọn khi đo/cách vận hành cần, không bỏ Docker chỉ vì tưởng container tạo VM. Hardware thật 4 vCPU/~8 GiB.
- Lát Compute-Runtime hoàn thiện nền tảng chạy các handler đã có; PII handler/model/quality gate §21.8 vẫn Hướng tới đã ghi từ trước. Không tạo PII fake để báo ba AI model pass. Encoder mới benchmark theo paired space/version, chưa duyệt đổi model mặc định hoặc threshold/artifact cũ.
- Candidate Contabo: 95 tests đạt trên image source cuối, real PostgreSQL/YOLO và supervisor lanes; [QA](../../setup/COMPUTE_RUNTIME_QA.md). CLIP text-only giữ embedding space (parity kiểm thật); SigLIP2 base CPU nặng/chậm hơn trong mẫu tổng hợp nên chưa thay mặc định, Gemma4 chỉ research. Chưa nghiệm thu mixed-load/PII/native hoặc rollout production.

## 128. Docs deployment hiện hành và archive lịch sử (2026-10-13)

- Người dùng yêu cầu sau test rà toàn bộ docs/plan/design, archive tài liệu cũ, active tập trung deployment và làm rõ kiến trúc high-level/detail.
- Archive 41 file local/research/plan đã thay thế, giữ 16 snapshots trước rewrite (PLAN/architecture/UI/spec/status/README/guide/alignment/local boundary).
  [Manifest và replacement](../../archived/2026-10-13/README.md). Giữ core contracts đang dùng, decision history và yêu cầu chưa làm.
- Active design/spec/index/PLAN/runbooks/status tách live source b195c61 và compute candidate966ab44; capacity/PII/reference/cloud gates vẫn mở.
  Không đổi runtime/interface/schema/sampling/policy hoặc rollout/push trong lát docs-only; không claim test app mới từ kiểm link.

## 129. N1-DB — giảm round trips và khóa đường đọc, test local trước release (2026-10-13)

- Người dùng duyệt implement đề xuất sau phản biện: read job/media nhẹ + direct poll, draft/submit decisions và enqueue nguyên tử,
  batch URL ảnh, gom queries/pipeline trước stored function. Chỉ local test/báo cáo trước quyết định deploy.
- CONTRACTS §21.23 và UI/architecture sửa trước code; giữ authority Postgres, quota/fence/receipts/recovery; không migration/policy số mới,
  không nâng cache P4 hoặc bật lanes #127/PII/reference. Read auth theo committed snapshot trước disable, TTL capabilities vẫn giữ.
- [QA local](../../setup/PROPOSAL_LOCAL_QA.md): PG16.15 thật, S3 moto, real API/worker và Chrome headless;
  526 backend/5 skip/hook lỗi có sẵn loại riêng, 34 FE, tsc/build/Ruff/diff-check; browser forced mất response sau job SUCCEEDED,
  retry cùng job/16 decisions và batch mới, snapshot draft bị chặn. Browser phát hiện terminal-response bug và regression đã sửa/đạt.
- Fixed branch SQL spans publish 34 sync → 22 sync, 5 samples/bản p50 local52,590→45,653 ms; không packet count/cloud SLA/50% gate claim.
  Có cơ sở candidate/canary có rollback sau image/compatibility gates, chưa có Supabase/AWS/GPU cloud mới hoặc push/deploy ở lát này.

## 130. Rollout N1 và provision CLIP cache trên Contabo (2026-10-14)

- Người dùng yêu cầu deploy bản mới nhất. Source bbd7d7a build combined/worker, Render + Contabo đã rollout;
  không push Git/main, không migration/policy/slots/caps mới hoặc bật lanes trước capacity gate.
- Tái hiện CLIP thiếu cache/Permission denied; tạo HF/Xet cache riêng quyền UID 1000 và provision đúng revision
  hiện có, text inference offline trong live container đạt. Không đổi embedding space/model.
- Backup/idle/drain/config rollback copies; health/ready và operator cloud smoke upload/batch ảnh/clip/delete,
  owner isolation và settlement đạt. Latency upload/clip còn cao; chưa authenticated browser text proposal/cloud SLA.
  [QA release](../../setup/N1_RELEASE_QA.md) ghi identity, số đo, lỗi script rollout đã phục hồi và gate còn thiếu.

## 131. PROPOSE normal-path publish RPC và mô phỏng có hiệu chỉnh (2026-10-14)

- Người dùng chọn tối ưu mục 1 trước, rồi yêu cầu mô phỏng DB/mạng/model để ước tính luồng.
  Candidate một SELECT cho publish chuẩn, giữ đầy đủ fence/context/quota/receipts/refund/slot; ngoại lệ dùng legacy.
- 011 additive worker-only invoker function; DB chưa migrate vẫn ready/fallback, không auto apply cloud.
  Không triển khai session tạm, bỏ CPU quota hay warm-up startup trong lát này.
- [QA](../../setup/PROPOSE_PUBLISH_RPC_QA.md): 540 backend/5 skip, hook Unicode loại riêng; 18 simulation pass;
  normal 22 sync → 1, delay 260 ms publish7.36→0.819 s. VPS SELECT1 p50 263 ms,
  CLIP warm p50 63 ms/query, cold process probe20.7 s. Không pure RTT/cloud SLA hoặc batch4 s claim.
- Chưa deploy/push; local pilot cloud-connected vẫn worker live cũ. Cache phiên, claim/điều phối, deficit SQL,
  warm-up/lane capacity và cloud acceptance là bước tiếp, không tự mở rộng từ số mô phỏng.

## 132. Claim RPC và CPU lanes (2026-10-14)

- Người dùng yêu cầu mục 2 và bật lanes. Giữ proof stopped, quota/recovery; additive 012 CPU/MEDIA claim.
- Local/Linux/capacity đạt; live TEXT/YOLO/BULK/MEDIA/GPU coordinator healthy, hard cap4CPU/6GiB, policy lanes-20261014.
- [QA](../../setup/CLAIM_LANES_QA.md) phân biệt isolated model load với cloud health; cloud smoke chưa hoàn tất trước steering #133. Không push.

## 133. Đề xuất interactive và bỏ CPU accounting (2026-10-14)

- User duyệt kế hoạch HTTP Render→Contabo, cache/context RAM, rate/concurrency limit, explicit Save và phục hồi checkpoint; chấp nhận mất proposal tạm.
- User yêu cầu bỏ hạn mức CPU và tất toán DB, giới hạn tại Contabo + retry. Giữ GPU/storage và trạng thái/cleanup job dài.
- Cache auth/source/session tối đa15s; scope single-owner process có bounded RAM/request replay, không thêm Redis hay message broker.
- Code trọn lát rồi focused; full suite một lần trước release. Kiểm phiên/model/source version và owner vẫn giữ, không nhận history client làm dữ liệu authoritative.

- Điều chỉnh #133 theo user: tự lưu batch hiện tại khi bấm Đề xuất, một checkpoint DB RPC; bỏ nút Save, giữ cache xuyên commit. Deploy được yêu cầu sau kiểm phù hợp; ưu tiên dùng lại tests.

- #133 đã deploy Render/Contabo, policy interactive-20261014 và migration014; [QA](../../setup/INTERACTIVE_CPU_QA.md) phân biệt cloud operator với browser E2E, upload/clip vẫn chậm. Không push Git.


## 134. Upload vài GiB và video dài (2026-10-14)

User cung cấp video241MiB/12phút và536MiB/24phút720p60fps. Live #133 chặn500MiB/15phút/10 tỷpixels,
USER1GiB/GLOBAL4GiB và PROPOSE4000unit. User duyệt multipart/retry/resume và đồng bộ policy/streaming preprocess,
test hai video thật local/Contabo trước deploy. Giữ4fps/exact anchors, RAM bounded, GPU/storage/owner/recovery.

## Kiểm chứng audit và dọn template (2026-10-14)

- Người dùng yêu cầu đối chiếu audit, dọn phần đúng để chuẩn bị phát triển thêm. Lát docs-only, không đổi thiết kế.
- Xóa 49 file template khóa học không có runtime caller; sửa hai link JOURNAL/presentation, giữ archive sản phẩm và QA checkpoint.
- Giữ local/SQLite, C1 và supervisor legacy vì contract/caller còn tồn tại; giữ benchmark/capacity có assertion và gate.
- SQL C1 giữ đường dẫn đang được code hướng dẫn, thêm nhãn legacy; proposal plan ghi rõ phần #133 thay thế và gate còn mở.
- [Kiểm chứng](../../setup/AUDIT_CLEANUP_QA.md). Không gom thay đổi upload #134 vào commit dọn dẹp; không push/deploy.

## Dev/CI cho agent chỉ có repo (2026-10-14)

- Theo yêu cầu tiếp tục sau khi chốt hướng: chuẩn hóa lệnh kiểm dev/CI, hướng dẫn môi trường test riêng và gate image.
- Giữ contract §21.11/18 và architecture §9: không đổi runtime/auth/storage/policy; PostgreSQL thật cho regression pilot,
  mock S3 không thay cloud acceptance, image smoke không thay browser/model/capacity.
- CI chạy runner chung và build/smoke combined/worker; không publish/migrate/deploy hoặc cần production secrets.
- [Hướng dẫn](../../setup/DEV_CI_GUIDE.md). Lát riêng, không gom thay đổi upload #134; không push.

## Chuẩn hóa trạng thái tài liệu và lộ trình tính năng (2026-10-15)

- Đối chiếu và chuẩn hóa tài liệu theo hiện trạng thực tế mã nguồn:
  - Phân định rõ giữa **Code candidate hoàn tất** (đã test local, 566 backend pass) và **Cloud Live release** (cần gate browser interrupt/resume, migration DB live và deploy). Lát #134 hiện ở mức candidate local đạt.
  - Đồng bộ quyết định #133 và CONTRACTS §21.26 vào backlog N2: **Policy mới tắt reserve/consume/receipt CPU cho mọi kind** (không áp hạn mức CPU), nhưng vẫn giữ CPU telemetry (`process_cpu_ms`) và tất toán các job cũ theo snapshot policy cũ; quota chỉ áp dụng cho GPU (Kaggle session time) và Storage.
  - Làm rõ phạm vi pilot và đề xuất mở rộng:
    - Phạm vi export pilot chuẩn theo CONTRACTS §21.8: **Xuất file ZIP chứa các ảnh từ snapshot đã che PII (YuNet + ONNX) + báo cáo HTML/JSON tự chứa, sau khi chủ sở hữu xác nhận qua Owner Confirmation Gate**.
    - Các yêu cầu như "xuất video đã che PII" hoặc "format nhãn chuẩn YOLO/COCO" là **đề xuất mở rộng tương lai (future backlog)**, không thuộc hợp đồng pilot hiện hành.
    - Phần pilot còn thiếu: Binding 3 bên Dataset–Model–Session (N3/N4) và quy trình che PII (detect mặt/biển số + blur) trong pipeline export §21.8.

## Tiến độ xử lý chi tiết frame/phase & ETA thực tế (#135, N2)

- Chuẩn hóa chuỗi pha xử lý worker cho video: `DOWNLOADING` → `PROBING` → `SAVING_CANONICAL` → `EXTRACTING_FRAMES` → `FINALIZING`.
- Thêm trường `progress_detail` vào bảng `rav_pilot_job` qua migration additive `016_pilot_progress.sql`.
- Tính fps tức thời và ETA dựa trên số đo thực tế trong pha trích xuất frame: `fps = done_units / elapsed_s`, `eta_s = (total_units - done_units) / max(fps, 0.1)`. Không hiển thị đếm ngược tĩnh khi chưa đủ dữ liệu đo.
- Mở rộng `JobDTO.progress` và `UploadDTO.progress` để API trả về đầy đủ tiến độ, giúp frontend poll cập nhật giao diện mượt mà và trực quan.
- Giữ nguyên quyết định §21.26: Không phục hồi quota CPU, chỉ đo thời gian và fps để phục vụ trải nghiệm người dùng và quan sát tải.

## Quy trình làm mờ PII & Xuất gói Snapshot (#136, CONTRACTS §21.8)

- Triển khai trọn vẹn hợp đồng xuất dữ liệu §21.8:
  - Bắt buộc xuất phát từ một `snapshot` bất biến đã chốt.
  - Vòng đời export: `REQUESTED` → `PROCESSING` → `NEEDS_CONFIRMATION` → `FINALIZING` → `READY`.
  - Worker handler `EXPORT_BUILD`: Giải mã frame theo snapshot, chạy module PII (YuNet phát hiện khuôn mặt + ONNX phát hiện biển số xe) và thực hiện làm mờ `redact` (pixelate16 + GaussianBlur) trên ảnh độ phân giải gốc; tạo báo cáo tự chứa `report.html` và `manifest.json`; tính `payload_hash` (chuẩn canonical SHA-256) và chuyển sang `NEEDS_CONFIRMATION`.
  - Cơ chế Owner Confirmation Gate: Chủ sở hữu kiểm tra trước ảnh đã che PII và báo cáo, sau đó gửi xác nhận kèm đúng `payload_hash` để chuyển sang `FINALIZING`.
  - Worker handler `EXPORT_FINALIZE`: Đóng gói file ZIP lên S3 storage, chuyển trạng thái export sang `READY` và cấp link tải có hạn (Presigned GET URL).
  - Thêm schema migration `017_pilot_export.sql` bao gồm `rav_pilot_export`, `rav_pilot_export_review_receipt`, `rav_pilot_export_confirmation` có RLS và phân quyền an toàn.

## Kho Dataset/Model tham chiếu và Binding phiên pilot (#137, N3 / CONTRACTS §21.17)

- Khai thác trọn vẹn các bảng `rav_pilot_dataset`, `rav_pilot_dataset_version`, `rav_pilot_model_asset`, `rav_pilot_model_dataset_declaration`, `rav_pilot_session_reference`.
- Cung cấp Catalog API REST v2:
  - `GET /api/v2/datasets` và `GET /api/v2/datasets/{id}`: Tra cứu danh sách và chi tiết các dataset tham chiếu cùng các phiên bản bất biến của chúng.
  - `GET /api/v2/models/{id}`: Tra cứu chi tiết model asset kèm quan hệ provenance (khai báo TRAIN/FINETUNE trên dataset version nào).
- Cơ chế Binding phiên pilot:
  - `GET /api/v2/sessions/{id}/reference`: Tra cứu reference model và dataset hiện đang được gắn vào phiên.
  - `PATCH /api/v2/sessions/{id}/reference`: Cập nhật hoặc gỡ bỏ binding qua CAS `expected_state_version`. Kiểm tra hợp lệ quan hệ model-dataset (trả về 422 nếu model không khớp quan hệ TRAIN/FINETUNE), giải phóng pins cũ trên nguồn dataset và thiết lập pin mới an toàn.
  - Migration additive `018_pilot_session_reference_grant.sql`: Cấp quyền `DELETE` trên bảng `rav_pilot_session_reference` cho role `rav_pilot_api`, cập nhật trigger guard cho phép release pin `consumer_kind = 'REFERENCE'`, và chuyển ràng buộc unique của pin sang partial index active.

## Bỏ chặn Storage Quota và Kế toán Dung lượng Độc lập (#138, Lát 1 / CONTRACTS §21.29)

- Thống nhất mở rộng kiến trúc upload lớn và chuẩn bị AI độc lập:
  - Bổ sung cờ `storage_accounting_enabled: boolean` vào policy config (mặc định `true` để tương thích ngược).
  - Khi `storage_accounting_enabled=false`:
    - Cho phép `storage_user_bytes: 0` và `storage_global_bytes: 0`.
    - Không chặn hạn mức dung lượng USER/GLOBAL (không phát sinh mã lỗi 429 `storage_exceeded`).
    - Không cập nhật `reserved_bytes` hay `persisted_bytes` trên `rav_pilot_storage_account`.
    - Vẫn lưu `rav_pilot_storage_hold` (`reserved_bytes`, `receipt_key`, `upload_id`/`job_id`) có gắn cờ `storage_accounting_enabled=false` để đảm bảo tính idempotent và phục vụ dọn dẹp.
    - Settle và release hold chuyển trạng thái sang `FINAL` bình thường mà không tác động tới tài khoản quota.
    - Khi xóa nguồn (`DELETE_SOURCE`), không phát sinh lỗi 409 `storage_reconcile_required` đối với nguồn không tính quota.
  - Migration additive `019_pilot_storage_optional.sql`: Thêm cột `storage_accounting_enabled` vào `rav_pilot_job` và `rav_pilot_storage_hold` kèm trigger bất biến snapshot.

### Sửa accounting khi xóa (2026-10-15, người dùng yêu cầu sửa lỗi review)

- Bỏ quy tắc trừ `min(account.persisted_bytes, size)`: file không tính quota không được làm giảm byte của file/account khác.
- Raw tra hold của upload tạo nguồn; artifact/frame set tra snapshot job tạo chúng. Không dùng policy hiện tại hoặc job xóa; không có producer receipt thì giữ semantics legacy đã tính quota.
- Gom một truy vấn tính tổng byte đã tính; bằng 0 thì không khóa USER/GLOBAL. Giữ kiểm ledger khi tổng dương, kể cả policy xóa đang tắt quota.
- Giữ `released_bytes` vật lý, thêm `accounted_released_bytes` vào receipt xóa. Dùng schema 019 có sẵn, không sửa migration đã áp. Storage chuyển sang §21.29, ETA giữ §21.28.

## Tuyển chọn theo Độ thiếu ổn định của Model Perception (#140 / CONTRACTS §21.31)

- Bổ sung recipe `perception_instability` cho phép tuyển chọn khung hình mà mô hình detection dự đoán thiếu ổn định giữa ảnh gốc và ảnh lật ngang (TTA horizontal flip), kết hợp đa dạng hóa thị giác DINOv2:
  - **Không yêu cầu dataset train tham chiếu**: Hoạt động độc lập chỉ với video READY và model detection đã xác minh (`INFERENCE_VERIFIED`). Không nới lỏng hay sửa đổi các recipe đang dùng dataset train (`reference_diversity`, `model_guided`).
  - **Uncertainty proxy, không phải xác suất lỗi**: Điểm thiếu ổn định là tín hiệu bất định suy luận hình học, không cần ground truth. Khung hình cả 2 lượt không có detection được đánh dấu rõ là "Chưa có tín hiệu" (`observed=False`), không kết luận model tự tin hay ảnh dễ.
  - **Inference qua Job nền, không ép vào HTTP đồng bộ**: Toàn bộ quá trình chọn ứng viên (DINOv2/uniform) và suy luận TTA chạy trong Job nền `PROPOSE` (queue CPU) có cập nhật tiến trình `INFERRING_UNCERTAINTY` (done/total).
  - **Quản lý ứng viên & trần tài nguyên**: Khởi điểm từ 32–48 frames cho batch 16, điều chỉnh theo `batch_size` với trần tài nguyên tối đa (tối đa 96 candidates). Nếu số frame có tín hiệu ít hơn `batch_size`, trả rõ số lượng thực tế, không tự bù frame random.
  - **Cache key đa thành phần**: Cache theo model hash, danh tính exact pixel anchor, tham số inference/preprocessing, transform (`horizontal_flip`) và phiên bản consistency (`v1`).
  - **Model input độc lập**: Cho phép truyền `model_id` trực tiếp trong yêu cầu đề xuất, server xác minh và đóng băng metadata model vào job payload.
  - **Tách biệt với Interactive**: API không chặn `interactive_required` với recipe background (`perception_instability`); frontend chuyển thẳng vào job nền và poll tiến trình cho đến khi hoàn tất.
  - **Bảo toàn Evidence qua DTO**: `ProposalV2DTO` và DTO response giữ nguyên `units` evidence và `uncertainty_summary` để UI hiển thị badge, tooltip và lightbox đối chiếu TTA.
  - **Persistent Disk Caching**: Cache inference YOLO consistency lưu tại `inference-cache/yolo-consistency` theo SHA đa thành phần, tồn tại qua các job/lượt đề xuất tiếp theo trên worker.
  - **Loại bỏ frame chưa có tín hiệu**: Candidate có `observed=False` được gắn `mask=False` loại hoàn toàn khỏi tập chọn của `GreedyNMS`; nếu thiếu frame có tín hiệu so với `batch_size`, trả batch nhỏ hơn kèm `uncertainty_summary`.
  - **Bật tắt tính năng**: Chỉ đặt `available=true` khi toàn bộ chuỗi API → Job → Worker → UI đã hoạt động trơn tru và qua kiểm thử.


## Gộp Lát 2–3: VPS Upload → Ingest S3 → READY (#139 / CONTRACTS §21.30)

- Thống nhất gộp Lát 2 (VPS disk upload) và Lát 3 (Ingest S3 -> READY) thành một lát hoàn chỉnh:
  - Transport mới `VPS_DISK` cho tệp lớn: upload trực tiếp từ trình duyệt tới VPS disk qua capability token HMAC bảo mật ngắn hạn, không cấp S3/DB credential cho trình duyệt.
  - Ghi tuần tự trực tiếp vào duy nhất tệp `.part` (chunk 16 MiB, buffer stream 1 MiB), fsync đĩa và ghi metadata `.meta` atomic.
  - Kiểm tra offset nghiêm ngặt chống ghi trùng/lệch (409 `offset_mismatch`); tự động cắt phần dư thừa (truncate) sau sự cố restart/crash để client resume từ byte bền vững.
  - Client gọi `complete` sau khi gửi đủ bytes: API enqueue `VALIDATE_UPLOAD` với `staging_source="VPS_DISK"`.
  - Worker ingest stream trực tiếp từ file `.part` lên S3 canonical (S3 multipart chunk 16 MiB), trích xuất frames 4fps, publish `READY` trong cùng transaction DB, sau đó xóa file `.part` và `.meta` trên VPS disk để giải phóng hoàn toàn dung lượng đĩa.
  - Cơ chế retry khi gặp lỗi S3 tạm thời giữ nguyên file `.part` trên VPS; dọn dẹp sạch khi job fail vĩnh viễn hoặc abort/sweep staging quá hạn.
  - Giữ nguyên 100% hai transport cũ (`DIRECT_S3` presigned POST và `MULTIPART` S3) cho phép rollback tức thì bằng cấu hình flag `vps_upload_enabled`.
  - Chưa deploy live cho tới khi kiểm thử toàn diện cả luồng resume, restart, S3 retry/failure và cleanup. PII Kaggle chuyển sang đợt tiếp theo.




### VPS upload release 2026-10-15 (#139, operational clarification)

User yêu cầu review/test rồi deploy; đã xác nhận cụ thể publish image vào `tonmeu/rav26` và deploy `c4-app-026`.
Render seal/cleanup qua HTTPS, không đọc đĩa VPS; chunk/control purpose tách, secret explicit,
cleanup sau commit, ingress TLS/CORS trên Contabo. Policy mới `vps-upload-20261015` giữ CPU off,
storage accounting off; một upload active, giới hạn cấu hình 5 GiB/60 phút. Cloud smoke 35.80 MiB đạt,
chưa claim benchmark 5 GiB hoặc browser/GPU/PII acceptance. Xem `docs/setup/VPS_UPLOAD_RELEASE_QA.md`.

## Strategy sở hữu thuật toán chọn mẫu (#141, 2026-10-15)

Người dùng xác nhận recipe mong muốn là Strategy hoàn chỉnh, được tự điều phối nhiều bước và tái dùng pool,
dataset tham chiếu, model khi cần; không bắt mọi thuật toán theo objective/combiner/selector. Duyệt thực hiện
trong tối nay theo lát chuyển kiến trúc + sidebar, giữ adapter thuật toán cũ và thêm CLIP/DINO/uncertainty.
Auth/resources/cache/queue/publication ở hạ tầng chung. Phạm vi và gates tại CONTRACTS §21.32.
Ngày 2026-10-16 người dùng chọn commit #141 với regression đầy đủ (gồm PostgreSQL thật) và **hoãn browser acceptance**
(máy chưa có CLIP checkpoint/Playwright); ghi Hướng tới trong §21.32, làm trước khi deploy strategy mới.

## DEP6 — model che PII thật, fail-closed (2026-10-16)

Audit phát hiện export #136 có thể xuất ảnh gốc mang nhãn "đã che" (thiếu weights → 0 box; biển số là stub; worker
không mount thư mục model). Người dùng chọn: sửa trong lát DEP6, deploy sau; model YuNet 2023mar + YOLOv9-t-640
plate end2end (MIT), ghim SHA-256; thiếu/sai model → export FAILED `pii_model_unavailable`. Không claim recall khi
chưa có tập nhãn PII. Chi tiết CONTRACTS §21.8 DEP6, QA `docs/setup/DEP6_PII_MODELS_QA.md`.

## #141 browser acceptance và relax ứng viên (2026-10-16)

Người dùng cho phép tiếp tục làm việc đã duyệt khi vắng mặt. Browser acceptance local của #141 đạt; phát hiện `context_uncertainty`
bỏ đói batch trên frame dashcam gần trùng (`relax=False`). Chi tiết thuật toán trong một component → mặc định như candidate stage cũ:
relax khoảng cách, giữ mask, ghi `relaxed`. Không deploy #141 khi người dùng vắng mặt.
# 2026-10-16 — Review boundary Strategy #141

Sau Claude commit 94dd6335, giữ schema công khai do Strategy khai báo; API dùng JSON Schema validation nhẹ
thay bộ kiểm riêng chỉ hỗ trợ int/array của strategy mẫu. Giữ byte limit, finite JSON, query validation và worker Params.
Native Strategy không được bị ép thành legacy config khi bind tài nguyên. Identity dùng FieldKey đầy đủ có thứ tự;
discovery atomic theo process để chịu các request khởi tạo đồng thời. Không thay policy/credentials/deployment.

# 2026-10-16 — Viewer ưu tiên kiểm tra ảnh

Theo người dùng: mở frame đã chốt hiển thị ảnh phóng to trước; Play mới tải/phát clip. Trong lúc clip chưa phát được
hoặc bị lỗi vẫn giữ ảnh để kiểm tra. Áp dụng viewer dùng chung, giữ quyết định và kiểm quyền media hiện tại.

# 2026-10-16 — Response chỉ theo batch

Người dùng yêu cầu không gửi điểm/dữ liệu mọi ứng viên lên trình duyệt khi chỉ cần batch hoặc mẫu đã giữ.
Giới hạn public scores/evidence theo picks, bỏ candidate IDs khỏi public trace/execution và không lặp scores ở checkpoint;
snapshot đầy đủ vẫn ở backend cho audit/retry. Không claim pipeline hiện tại đã hỗ trợ hàng triệu unit.

# 2026-10-16 — Sửa identity ảnh snapshot/export

Theo báo lỗi người dùng: xuất đúng unit đã chốt, ưu tiên FRAME đã lưu và kiểm checksum; raw fallback phải resolve
exact anchor, không dùng ordinal hay ảnh giả. Lịch sử xuất đọc từ DB theo phiên có phân trang, reload giữ tiến trình/review;
snapshot mới cập nhật ngay lựa chọn xuất. Không sửa dữ liệu/ảnh của export cũ; phải tạo lần xuất mới sau bản sửa.
Không thay thiết kế PII/confirmation gate. Bằng chứng và giới hạn release: docs/setup/SNAPSHOT_EXPORT_IDENTITY_QA.md.

# 2026-10-16 — Rà soát casing media

Theo người dùng, rà lại lỗi frame/FRAME: giữ DTO API lowercase, DB/manifest uppercase, S3 path lowercase.
Chuẩn hóa và kiểm loại ảnh trong stored_variant thay vì dựa vào caller; variant lạ không ngầm trở thành FRAME.
Không nới API hoặc manifest producer. Kết quả audit: docs/setup/MEDIA_VARIANT_AUDIT_QA.md.

# 2026-10-16 — PII resize, cache kết quả và gói dataset

Người dùng chọn resize trước inference rồi scale box về ảnh gốc; lưu/cache kết quả PII bền để xuất lại không chạy detector.
Cache PRIVATE theo owner/input/model/config dùng artifact DB và PNG S3; đổi mode/model làm cache miss.
Giữ owner confirmation trước bundle ZIP; UI phân biệt preview đã lưu và dataset ZIP READY. Kết quả cũ sai frame không
được tái dùng hay xác nhận như đã đúng; phải xuất mới từ snapshot. Chi tiết và giới hạn: docs/setup/PII_RESIZE_CACHE_QA.md.

# 2026-10-16 — Admin upload model tham chiếu (#142)

Người dùng chọn: chỉ admin upload checkpoint qua UI; người dùng chọn từ danh sách model SYSTEM; demo cần phiên/model mẫu cho người mới.
Admin nhận diện bằng biến môi trường (dùng lại `RAV_PILOT_OPERATOR_IDS`), không thêm cột vai trò. Nhánh mới từ c0a68d34 (bản live), không từ main.
Upload model riêng của người dùng là Hướng tới vì `.pt` là pickle chạy code trên worker dùng chung. Thứ tự: model/phiên mẫu → admin upload → dataset mẫu.

# 2026-10-17 — Gộp admin model và PII

Người dùng yêu cầu ghép công việc Claude vào nhánh hiện tại. Giữ migration 021 model verify, đổi migration cache PII chưa release thành 022; giữ nguyên nội dung SQL và các gate hiện có. Kiểm thử chung trước commit, không push/deploy trong lát này.

# 2026-10-17 — Deploy bản gộp model + PII

Người dùng yêu cầu deploy sau khi merge 49a3ba8b. Cho phép áp migration 021–022, tạo version policy kế thừa chỉ bổ sung MODEL_VERIFY, gán kind vào lane YOLO và rollout worker/FE/API. Không đổi giới hạn/accounting, không sửa decisions hay export cũ; kiểm bằng tài khoản QA riêng.

# 2026-10-17 — Nhật ký trên trang quản trị từ review PR Nam

Người dùng yêu cầu push bản hiện tại, đọc PR Nam và tích hợp log vào admin nếu phù hợp. PR #2 head 1abafe9f có dashboard/stack observability của sandbox eval local; không ghép nguyên Compose anonymous Admin/Loki unauth hoặc backend eval vào production. Áp dụng bảng log/filter vào Vận hành với collector rav_timing hiện có theo §21.19, read-only operator, cửa sổ và keyset pagination giới hạn, không thêm I/O DB vào inference. Không dựng Prometheus/Loki/Grafana/Jaeger mới trong lát này, không đổi credentials/policy/live deployment.

# 2026-10-17 — Tải cao và giá trị log trước release

Người dùng yêu cầu test tải cao, chỉ deploy nếu ổn, đồng thời kiểm luồng log có giá trị thực. Bài cô lập 140k events/8 client/CPU synthetic đạt; fault collector không làm fail API. Sửa các span export.* bị allowlist bỏ qua thành worker.export.*, thêm cache_hit và nhãn UI; worker cần rollout cùng FE/API để nhận span mới, không đổi policy/schema/inference.

# 2026-10-17 — Kế hoạch 5 lát: Đăng ký tự phục vụ, Kho dữ liệu mẫu, Admin mẫu & Log

Người dùng duyệt kế hoạch 5 lát (cập nhật 6 điểm rà soát thiết kế) và yêu cầu triển khai tuần tự:
1. **Lát 1 — Đăng ký và khởi tạo tài khoản (CONTRACTS §21.34)**:
   - Mở đăng ký tự phục vụ trên `/login` bằng Supabase Auth (`signUp` với email, mật khẩu, tên hiển thị tùy chọn trong `user_metadata.display_name`); hiển thị rõ trạng thái tạo tài khoản, lỗi hoặc yêu cầu xác nhận email theo cấu hình auth thực tế.
   - Sau đăng nhập lần đầu (tài khoản chưa có phiên và chưa có video riêng), hiển thị 2 hướng rõ ràng: **Dùng dữ liệu mẫu** và **Upload dữ liệu của bạn**.
   - **Bootstrap một đường duy nhất trong auth (`extract_actor_context` → `PilotDB.ensure_profile`)**: Khi JWT hợp lệ nhưng chưa có dòng trong `rav_pilot_profile`, tự động tạo `rav_pilot_profile` (`active=true`, `authz_version=1`) và `rav_pilot_storage_account` (`USER:<uid>`, `GLOBAL`) trong cùng một transaction (`ON CONFLICT DO NOTHING`, idempotent khi nhiều request đầu tiên đến đồng thời).
   - **Chính sách đăng ký tường minh**: Không tự chọn `enabled=true ORDER BY created_at DESC`. Chỉ dùng policy được operator cấu hình rõ cho đăng ký mới (`RAV_PILOT_SIGNUP_POLICY_VERSION` nếu đặt, hoặc cột `rav_pilot_quota_policy.signup_default = true` kèm `enabled = true` và `policy_problem(config) is None`). Thiếu hoặc không hợp lệ → `503 policy_unconfigured`.
   - Không sửa profile đã tồn tại, không mở lại tài khoản bị khóa (`active=false` → `401 account_disabled`). Tên hiển thị từ JWT metadata chỉ dùng hiển thị, không cấp quyền. Quyền admin giữ nguyên qua `RAV_PILOT_OPERATOR_IDS`.
2. **Lát 2 — Kho dữ liệu, dữ liệu mẫu và giữ tiến trình upload cấp ứng dụng (CONTRACTS §21.35)**:
   - Giao diện Kho dữ liệu thống nhất **Dữ liệu của tôi / Dữ liệu mẫu** (hiển thị cả Video và Model với cùng thiết kế thẻ, bộ lọc, trạng thái Ảnh/DINO/CLIP/Xác minh, nút **Tạo phiên từ dữ liệu này** trên video mẫu đã sẵn sàng, và khu vực upload đồng nhất).
   - Bổ sung `description` và `published` độc lập với trạng thái xử lý trên `rav_pilot_source_asset` và `rav_pilot_model_asset`.
   - Tách rõ "xuất hiện trong kho / chọn tạo phiên mới" (`published=true`) và "được đọc trong phiên/snapshot đã tạo" (kể cả khi sau đó bị ẩn) ở cả RLS (`p_source_read`, `p_model_read`) và mọi đường `backend()` (`get_source`, `get_model`, `lock_sources`, `PilotMedia`, `PilotFrames`, `PilotFeatures`, `PilotCuration`, `PilotAnalysis`). Người dùng chưa từng có phiên/snapshot tham chiếu không thể đoán UUID để đọc tài nguyên ẩn (`404 not_found`).
   - Tái dùng frame/DINO/CLIP của nguồn `SYSTEM` khi nhiều tài khoản tạo phiên riêng, không chạy lại prepare.
   - Đưa bộ quản lý tiến trình upload lên cấp ứng dụng (`App.tsx`) kèm chỉ báo upload nhỏ luôn hiển thị để chuyển qua lại Kho dữ liệu ↔ Phiên không mất trạng thái; bổ sung `GET /api/v2/uploads` để sau khi reload trang (F5) tự động nối lại theo dõi các upload đang `VALIDATING` trên worker và hiện **"Chọn lại file để tiếp tục"** cho các upload `CREATED` chưa truyền đủ file.
3. **Lát 3 (gộp Lát 3 & 5, bỏ Lát 4) — Admin quản lý tài nguyên mẫu, theo dõi/log & Seed dữ liệu mẫu thật (CONTRACTS §21.36)**:
   - Theo yêu cầu người dùng (2026-10-17): bỏ hạng mục "Admin tạo/cấu hình phiên mẫu (`CONFIGURED`)" trên UI (đánh dấu **Hướng tới** trong CONTRACTS) vì người dùng đã có nút **"Tạo phiên từ dữ liệu này"** trực tiếp trên video mẫu sẵn sàng ở Kho dữ liệu; giữ nguyên cơ chế phiên mẫu lịch sử `IMPORTED_HISTORY` hiện có. Gộp Lát 3 và Lát 5 thành một lát cắt hoàn chỉnh:
   - **Luồng upload & prepare nguồn `SYSTEM` xuyên suốt**:
     - `target_scope = 'SYSTEM'` trên `POST /api/v2/uploads` (chỉ admin `operations_read`, người dùng thường → `403 forbidden`), lưu bền vào `rav_pilot_upload.target_scope` và payload job `VALIDATE_UPLOAD`.
     - Storage hold của upload/job `SYSTEM` chỉ tính vào tài khoản `GLOBAL` (`account_scope = 'GLOBAL_ONLY'`), không trừ hạn mức `USER:<admin_uid>`; `owner_id` của upload/job vẫn là admin để theo dõi tiến trình, audit và hủy/thử lại.
     - Worker lưu canonical raw vào `<prefix>/system/uploads/sources/<core_source_id>/raw/<sha256>/video.<ext>`, trích frame vào `<prefix>/system/uploads/frames/<core_source_id>/<frame_set_id>/...`, publish `rav_pilot_source_asset` (`scope='SYSTEM', owner_id=NULL, protected=true, published=false`) và `rav_pilot_frame_set` (`scope='SYSTEM', owner_id=NULL`).
     - Admin gọi được `POST /api/v2/sources/{id}/preparations` (`FRAMES`, `DINO`, `CLIP`) trên nguồn `SYSTEM` (người dùng thường vẫn nhận `409 system_feature_missing` / `system_frames_missing`); artifact `DINO`/`CLIP` publish ở `scope='SYSTEM', owner_id=NULL`.
   - **API & UI Quản lý danh mục dữ liệu mẫu của Admin**:
     - `GET /api/v2/admin/sources` và `GET /api/v2/admin/models` (phân trang cursor, kèm `publishable`, `unpublishable_reasons`, `dependent_template_count`, `dependent_templates`).
     - `PATCH /api/v2/admin/sources/{id}` và `PATCH /api/v2/admin/models/{id}` sửa `name`, `description`, `published` (kiểm tra đủ `READY` + `FRAMES` cho video, `INFERENCE_VERIFIED` cho model trước khi cho phép `published = true`; khi ẩn nguồn `SYSTEM` thì chuyển các phiên mẫu `IMPORTED_HISTORY` phụ thuộc sang `BLOCKED` `source_unpublished` và chặn `fork` nguồn đang ẩn).
   - **Trang Admin Vận hành (`#operations`) & Nhật ký**:
     - Tổ chức thành các khu vực rõ ràng (**Tổng quan / Dữ liệu mẫu / Tác vụ / Nhật ký**), bổ sung `job_counts` tổng hợp trực tiếp từ DB (`queued`, `waiting_resource`, `running`, `cancel_requested`, `failed_24h`, `succeeded_24h`), hiển thị tách biệt `attempt_n`, `queue_wait_ms` (từ `job.created_at` đến `attempt_1.started_at`), `run_ms` (thời gian chạy của attempt hiện tại), chống chồng request khi auto-refresh, và lọc Nhật ký (`GET /api/v2/operations/logs`) theo `upload_id` / `job_id` / `request_id`.
   - **Seed dữ liệu mẫu thật**:
     - Cung cấp script idempotent `scripts/pilot_seed_sample_catalog.py` nạp video mẫu thật (`data/videos/commaai-sample.mp4`), hoàn tất `FRAMES` / `DINO` / `CLIP` ở `scope = 'SYSTEM'` và bật `published = true` để hai tài khoản mới tạo phiên độc lập và dùng lại ngay không cần chạy lại prepare.



### Review release kho mau 2026-10-17

- Nguoi dung xac nhan bo Lat 4: khong trien khai them admin tao template/fork; nguoi dung tu tao phien tu du lieu SYSTEM. Giu compatibility template cu.
- Seed du lieu mau phai dung Context/unit ID/exact anchor va component DINO/CLIP/pHash that; cam fallback vector ngau nhien mang danh tinh feature that. Test gia lap chi bang dependency mocking trong test, khong co co synthetic tren CLI production. Model thieu thi seed that phai that bai, khong publish READY.

- Hotfix signup email: Supabase Site URL tu localhost3000 sang https://c4-app-026.onrender.com/login; signup explicit same-origin redirect va SDK nhan callback chi o /login. Khong tat email verification, khong doi credentials.

- User xac nhan50 phut GPU moi chu ky4h cho signup moi, CPU khong gioi han quota. Default policy phien ban moi khong doi cac gioi han khac. Admin editor de xuat nhung chua implement. Live trigger dashboard cap nham policy LIMIT1 da duoc retire bang026; API bootstrap la authority. Sua rieng tai khoan moi cua session user bao keo dai hang doi; giu frozen job, usage va holds.


## ODD coverage PoC approved 2026-10-17

User approves implementation of separate native strategy: ODD×object query unions plus independent scenarios, CLIP cosine+contrast-margin gate (no pool min-max), dynamic coverage/diversity and optional bounded model instability. Existing recipes unchanged; thresholds explicitly heuristic until labeled evaluation. Scope CONTRACTS21.37; no model migration/quantization, no deploy or push requested for this slice.
# Deploy ODD coverage (2026-10-17)

- Người dùng yêu cầu deploy source065ff890: worker + FE/API; giữ schema/policy/quota/credentials,
  không push Git. Release QA tại `docs/setup/ODD_COVERAGE_RELEASE_QA.md`; không suy cloud smoke nhỏ
  thành chứng nhận semantic quality hoặc latency/capacity.
# Admin copy prepared samples (2026-10-17)

- User approved an independent SYSTEM copy from the admin's own private prepared video; preserve original
  source/sessions, copy existing raw/FRAMES/DINO/CLIP, no repeated AI. Hidden first, explicit publication.
  CONTRACTS §21.38 uses PREPARE_FRAMES copy mode with existing fenced receipt/accounting lifecycle.
  This slice does not authorize publishing the admin's real data, Git push or deployment.

# Read-only sample references replace new physical copies (2026-10-17)

- User approved §21.39: SYSTEM samples reference the admin's prepared immutable files, not per-account copies.
  Keep PRIVATE ownership and existing sessions; sample access is read-only and publication stays explicit.
  Pin backing source while any sample references it (including hidden samples); prevent deletion rather than
  implement general object garbage collection in this slice. Metadata aliases preserve existing auth paths.
- Cancel the explicitly reported live copy job `07ca489e-3d6a-4e67-883f-3f16229289f8` via owner/CAS API.
  Confirmed CANCELLED at2176/13423; existing supervisor handles attempt-key cleanup. No force kill/state edits.
- Implement/test locally first. No production migration, Git push or deploy authorized by this request.
- Subsequent user request "bạn deploy luôn đi" authorizes migration027 and synchronized API/worker/FE
  rollout after verification. Keep publication explicit; no Git push requested in this deployment instruction.

# ODD proposal preparation/cache and visible timings (2026-10-17)

- User agreed raw-video/cancellation findings and requested reusable CLIP prompt vectors plus visible timings
  inside algorithms. §21.40 removes raw fetch/probe only for ODD without a detector and valid frozen metadata;
  existing model/legacy paths retain their pixel input. No scoring changes or inferred uncertainty from CLIP.
- Persist exact prompt/model/revision/normalization vectors in bounded worker disk cache. Keep pinned text-model
  RAM cache per long-lived queue process; cached model files do not imply an already loaded model in RAM.
- Record bounded measured stages in existing progress JSON and sanitized telemetry, with public UI labels.
  No prompt/object key/token logging, new migration or service. Cancellation wording describes waiting for safe stop.
- No Git push/deployment in this slice. Local/browser QA is separate from latency acceptance on the VPS.
- Subsequent user request authorizes deploying §21.40 to VPS and the compatible API, then opening local FE
  against cloud services for testing. Keep schema/policy unchanged and do not push Git without a push request.
  Landing-page screenshots will be reviewed with the user after this rollout; no placeholder replacement yet.
- User requested a newly created ordinary account for demo screenshots, explicitly excluding admin accounts.
  Create `DriveLens Demo`, normal signup policy, no operator/model-write grants; use published SYSTEM data and
  a new owned session. Separate localhost origin preserves the existing admin127.0.0.1 browser session.
  User explicitly approved one temporary local token file for sign-in, to be deleted immediately afterwards.

# Shared public demo login (2026-10-17)

- User deferred SMTP work and explicitly requested shared account `demo123@aiia.com` / `demo@123` plus FE autofill.
  This pair is intentionally public; no service/admin credentials in FE. Fill only, normal sign-in remains.
- Ordinary account with existing signup policy (GPU50min/4h, CPU accounting off), no special bonus or admin grants.
  All visitors share that account's quota and sessions. No automatic Git push/deployment authorized by this request.

# Product name DriveLens (2026-10-18)

- User decided the product name is **DriveLens**, matching the landing page. Rename user-visible text only: FE sidebar/login,
  API titles, export report/ZIP name, README and docs titles. RAV-26 remains the problem/course code.
- Technical identifiers (`src/rav`, `RAV_*` env, `rav_pilot_*` tables, S3 prefix, `rav26` images) unchanged to avoid breaking deploy/data.
- Logo (2026-10-18, user request): Irish coffee glass with a camera lens inside (team IrishCoffee + DriveLens), one file
  `src/web/src/assets/logo.svg` used for landing header/footer, favicon and the architecture diagram.

# Landing dùng ảnh thật và sơ đồ kiến trúc mới (2026-10-18)

- User provided a real export bundle and a Studio screenshot to replace landing placeholders built from the product story.
  Landing now shows static bundled images only: Studio screenshot (readable plates blurred), 14 exported PII-redacted frames
  with metadata from the export manifest, and a hand-written SVG architecture diagram matching the deployed topology.
- User accepted known PII false positives/misses of the lightweight detectors; landing states this explicitly.
- Source credit: Dashcam Owners Australia (watermark kept), non-commercial illustration. No API call or private data on landing.

# METRICS theo khối cho min_dist / nd_rate_at_k (2026-10-18)

- Sự cố production (demo account, phiên ~12.5k frame): METRICS bị cgroup OOM kill 3 lần (process BULK anon-rss ~4,8 GB),
  lần cuối `resource_limit`. Nguyên nhân: `min_dist` và `nd_rate_at_k` dựng ma trận N×N trên tập đã giữ (RAM O(N²)).
- User duyệt sửa cài đặt: tính theo khối 512 hàng, RAM O(512·N), thời gian vẫn O(N²); kết quả giống hệt (test so với công thức cũ).
  Không đổi ý nghĩa metric, version component, CONTRACTS hay trần RSS lane. Deploy worker là bước riêng.
- Sau deploy `f49a4905`, METRICS vẫn ~5,2 GB (đo VmHWM process BULK): thủ phạm chính là view `duplicate_pairs` mặc định trên **cả pool**
  (ma trận Hamming N×N + `triu_indices`). Sửa: hai lượt theo khối (histogram, rồi tìm cặp theo hạng triu đã chọn) — cùng seed cho cùng
  `hist`/`pairs` như bản cũ (test so khớp), RAM O(512·N). `hamming_matrix` giữ cho N nhỏ.
