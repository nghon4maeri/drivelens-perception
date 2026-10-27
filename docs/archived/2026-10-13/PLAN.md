# PLAN — RAV-26 MVP (cập nhật 2026-09-29)

> ⚠️ **MVP là bản 1 người dùng, chạy local.** Trước khi deploy/host cho nhiều người (kể cả Live URL Demo Day), đọc `docs/design/LOCAL_SINGLE_USER_LIMITS.md`.

Nguồn sự thật và luật làm việc: `AGENTS.md`. Thiết kế code phải theo: `docs/design/CONTRACTS.md` + `docs/architecture_diagram.md`.
Hướng dẫn thực thi hiện hành: `docs/IMPLEMENT_GUIDE_V2.md`; kế hoạch khởi tạo lại MVP nằm trong archive.

## Hiện trạng

- 2026-09-29: làm lại MVP từ mốc docs `0fd6c91` trên nhánh `duy-mvp/v2` (nhánh `duy-mvp/init` chỉ để tham khảo).
- Phase 0 + 1 (nhánh mới, cập nhật docs theo quyết định #24–#29) xong.
- Đã xoá code/script cũ và `AGENT.md` (guide bước 0.4, 1.4).
- L1 (core) và L2 (P1: source, decoder `pyav` serve frame theo yêu cầu, unit, 3 feature, execution + field_store, command video) xong.
- L3 (P2: reference, novelty, product_strength, greedy_nms/random/uniform, recipe qua registry, `propose`) xong; combiner nhận `unit_ids` (#40).
- L4 (P3: metric `nd_rate_at_k`, `evaluate`, command + CLI `benchmark`) xong; số youtube5m **sơ bộ** trong `eval/results/report.md` §6 (#41, #42).
- L5a (vòng duyệt: `store/db.py` sqlite3, feedback `keep_drop`, service phiên / review_state / proposal, command phiên / state / propose / `list_proposals`) xong (#47).
- L5b (exporter `csv_images` + `export`, `list_recipes` / `describe_component`, `list_units`, `return_scores`, `n_sessions`) xong (#48).
- L6 (`api/`: route sinh từ bảng → command, upload theo khúc, media `/media/videos/{video_id}` có Range, lỗi chuẩn; khoá decoder mỗi video) xong (#49).
- L7a (views `embedding_map` `timeline` `duplicate_pairs`, metric `keep_rate_by_recipe`, `get_view` / `get_metrics` + route, CLI `spotcheck`) xong (#50). pHash luôn 32 bit 1 → Hamming chẵn.
- L7b: spot-check → ngưỡng pHash chính thức **8** (#51); benchmark chính thức `eval/results/report.md` §7: diversity < random rõ, nhưng K=200 không hơn uniform.
- L8 (`src/web/`: Vite + React, không thêm thư viện; màn video/phiên, vùng duyệt, chi tiết frame, lịch sử, chỉ số + export, 3 renderer view) xong (#52). Sửa 2 lỗi BE phát hiện khi chạy thử: upload lại file trùng, metric sai field → 500 (#52).
- L8b: giao diện mới của nhóm (commit teammate `51b5e1e`) thay giao diện L8, nối API; số BE chưa có hiện `-/-` đỏ; chủ đề + tiến độ P1 để sau (#53).
- L8c: export giữ mọi lần, `list_exports` / `get_export_file`, màn "Dữ liệu đã xuất" + tải .zip (#54). Chạy thử YouTube 7 phút (1.710 unit): P1 5 phút 11 giây, đề xuất 0,6 giây.

## Mục tiêu MVP (4 ngày)

Kỹ sư Data/ML nạp **1 video** → hệ thống đề xuất k frame mỗi vòng, ít trùng lặp → kỹ sư keep/drop →
vòng sau tránh frame giống tập đã keep → export frame đã keep để gán nhãn.

- **Metric chính:** Near-Duplicate Rate@K so với Random / Uniform Temporal, cùng K, nhiều seed.
- **Metric online:** keep rate theo recipe trong phiên.
- **Không claim:** mAP tăng, rare-event coverage (chưa retrain/đánh giá).

## Phạm vi

| Làm | Chừa chỗ (contract có, chưa code) | Không làm |
|---|---|---|
| Unit = frame + ngữ cảnh ±vài giây | Unit = span / sequence | Train/retrain detector |
| Feature: DINOv2-S, pHash | YOLO11s/m vs 11x disagreement | Annotation editor (dùng CVAT/Label Studio) |
| Objective novelty; selector greedy + temporal NMS; baseline random, uniform (selector) | Re-roll, preview A/B, fork phiên | Agent tự chọn/tune recipe |
| Feedback keep/drop; đổi recipe giữa các vòng | More/less-like-this | |
| Upload video (kiểm tra định dạng, dung lượng) | | |
| SQLite + file; export CSV + ảnh | Agent chatbot (client của command layer) | |
| Web React + FastAPI qua command layer | | |
| View: embedding map (PCA), timeline, duplicate pairs (lưới đề xuất vẽ từ proposal) | UMAP/t-SNE, similarity heatmap, so recipe | |
| **1 video** thật, pool ≥ 1.000 frame (chưa chọn; `data/` hiện chỉ là mock) | Nhiều video, tập con BDD100K | |

## Các lát cắt

Mỗi lát một phiên: đọc `AGENTS.md` + file này + guide + các mục CONTRACTS của lát → code → test → commit → tick → dừng.

- [x] **P0+1** Nhánh `duy-mvp/v2`, docs theo quyết định #24–#29, `AGENTS.md`, `docs/components/`
- [x] Dọn repo (guide bước 0.4: xoá `src/*.py` cũ, `tests/test_selectors.py`, script không dùng, `AGENT.md`)
- [x] Khung thư mục `src/rav/` + file rỗng có docstring (architecture §9)
- [x] **L1** `core/`: types, Component + Params, registry (bắt buộc docstring, auto-import), Context — CONTRACTS §1–4
- [x] **L2** P1: source (thư mục + upload), decoder pyav 4 fps (serve frame theo yêu cầu, #37), unit per_frame, features `emb.dinov2_s` `hash.phash` `proj.pca2`,
  `core/execution.py` (lazy + disk, cache theo `per_sample`, key `spec_hash` / `pool_hash`), `store/field_store` theo `FieldKey` — CONTRACTS §3 (`Feature.per_sample`), §5 (1–5, 13), §7, §13; arch §4.2–4.3
- [x] **L3** P2 (quyết định #39): reference `session_kept`, objective `novelty`, combiner `product_strength`, selector `greedy_nms` `random` `uniform`,
  `RecipeConfig` ở `core/types.py` + recipe đăng ký qua registry, `get_score_candidates` + `propose`, `Context.set_reference`,
  `recipes.py` (diversity/random/uniform), `pipeline/propose.py` thuần — CONTRACTS §5 (6–9), §6, §7, §9, §13
- [x] **L4** P3 + CLI benchmark: metric `nd_rate_at_k` (pHash, độc lập), recipe × seed × K; số **sơ bộ** (spot-check ngưỡng pHash để sau L7, #41) — CONTRACTS §5 (11), §9.10, §12.5; arch §4
- [x] **L5a** vòng duyệt chạy từ đầu đến cuối (#46): `store/db.py` (sqlite3, 3 bảng), feedback `keep_drop`, service phiên / review_state (replay log) /
  proposal (snapshot input gồm `config`, `seed`, `fields_used`; ctx nạp `proposals` cũ), command `create_session` `list_sessions` `get_session`
  `get_state` `update_state` `propose` + lịch sử proposal — CONTRACTS §2, §9.7, §10, §11.1–11.2, §12.3; arch §5–6
- [x] **L5b** phần bao quanh (#46): exporter `csv_images` + command `export`, `list_recipes` / `describe_component`, `list_units`,
  `return_scores` (dựng lại ctx từ snapshot), `list_videos.n_sessions` đếm từ DB — CONTRACTS §5 (12), §7, §11.2; arch §5
- [x] **L6** `api/`: route gọi command, upload, media, lỗi chuẩn — arch §3, §5
- [x] **L7a** views `embedding_map` `timeline` `duplicate_pairs` (`build(ctx)`) + metric `keep_rate_by_recipe`, command `get_view(session_id, …)` / `get_metrics(session_id, metrics?, on?)` + route, CLI `spotcheck` (#50) — CONTRACTS §5 (11, 14), §11.2; arch §5, §7
- [x] **L7b** spot-check ngưỡng pHash bằng lưới `spotcheck` (người chốt) → chạy lại benchmark, ghi số chính thức (#41, #50) — CONTRACTS §9.10
- [x] **L8** `src/web/`: React, renderers theo `view.type` — arch §3, §5–7
- [x] **L8b** thay giao diện L8 bằng UI mới của nhóm, nối API; số BE chưa có hiện `-/-` đỏ (#53) — arch §3, §5–7
- [x] **L8c** export giữ mọi lần + `list_exports` / `get_export_file` (tải .zip) + màn "Đã xuất" (#54) — CONTRACTS §5 (12), §11.2; arch §5
- [x] **L8d** UI gọn: card ít chữ + tooltip, sidebar một panel, bớt viền, ẩn số backend chưa có (#55) — arch §3
- [x] **L8e** P1 chạy nền + tiến độ trên UI, nút Thêm video, thêm video vào phiên (#56) — arch §5; CONTRACTS §7, §11.2
- [x] **L8f** UI_DESIGN.md + sidebar kiểu web chat + màn Kho video + refactor FE (ui/, useSessionData, useHotkeys, bớt tải lại) (#57)
- [x] **L8g** màn Phiên mới (`#new`, tự đề xuất đợt đầu), chọn nhiều ở Kho video, nút thêm video trên thanh phiên (#58)
- [x] **L8h** đủ chỉ số UI: metric review_time / min_dist / temporal_coverage / cluster_coverage, thông tin video, hợp đồng + UI bằng chứng (det / tag / telemetry) (#59)
- [x] **L8i** Studio ưu tiên quyết định: lưu ngay + trạng thái lưu / thử lại, giải thích cấp đợt (`explain_proposal`), Phân tích theo focus, nhất quán phím / focus / cột (#61)
- [ ] **L9** README "Chạy MVP local", `MVP_IMPLEMENTATION.md`, rà `RAV26_ALIGNMENT.md` §6

### Giai đoạn 1 — dataset tham chiếu và snapshot (#64)

Kế hoạch 4 giai đoạn: `docs/archived/planning/MVP_GAP_MAPPING.md` §4. Thiết kế, test (T*) và chi tiết slice: `docs/archived/2026-10-13/docs/design/PHASE1_DATASET_SNAPSHOT.md` §9.
Lưu trữ theo tham chiếu: dataset / snapshot chỉ lưu manifest tới raw, ảnh chỉ đọc / decode khi tải về (CONTRACTS §14.1).

- [x] **S1** Migration SQLite theo `user_version` + backup (không xoá DB); schema v2 = `snapshot`, `snapshot_member` bất biến (trigger) — CONTRACTS §5 dòng 13, §14.2
- [x] **S2** Snapshot: `create_snapshot` / `list_snapshots` / `get_snapshot`, `members_hash` + `context_hash`, provenance tham chiếu raw, không sinh ảnh — CONTRACTS §11.2, §14.2; arch §5.2–5.3 (BE + API; UI ở S7a)
- [x] **S3** Kiểu nguồn ảnh: `ImageRef`, `Unit` (`kind="image"`, `source_id`, `order_key`), execution nhóm theo `source_id`, `Context.pixels` đọc ảnh; giữ id + cache video
- [x] **S4** Import dataset ảnh (schema v3, manifest, dedup có kiểm xung đột nhãn, YOLO tối thiểu, lớp đóng băng; #65)
- [x] **S5** Chuẩn bị feature cho dataset version + `verify` (ảnh + nhãn)
- [x] **S6** Chọn dataset version tham chiếu cho phiên (Q1 đã chốt: pool chỉ video, #65)
- [x] **S7a** UI snapshot + ẩn baseline, recipe mặc định theo tên · **S7b** UI dataset (import, xem, chọn tham chiếu)
- [x] **S8** Export theo `snapshot_id`: kiểm nguồn → đọc / decode đúng frame → ZIP tạm có giới hạn → dọn
- [x] **S9** Feature CLIP (image) + text encode local, pin checkpoint + revision
- [x] **S10** Adapter tính embedding trên Kaggle (SDK chính thức; `kaggle_extend_api` chỉ tham khảo, #68) nối vào prepare; mock + live nếu có credentials
- [ ] **S11** Smoke subset BDD100K ≤ 200 ảnh theo seed (cần dữ liệu BDD100K trên máy — hiện chưa có)

Việc khác:

- [x] **R1** Làm rõ ngữ nghĩa dataset train/finetune và YOLO tham chiếu cần cải thiện trên pool mới chưa gán nhãn (#69); chỉ sửa tài liệu. Nạp/chọn checkpoint, liên hệ model–dataset và inference/tiêu chí chọn chưa triển khai, cần thiết kế lát kế tiếp.
- [x] **M1** Nạp/list/verify checkpoint .pt + chọn model/dataset train trong phiên + FE (#70), migration v4; CONTRACTS §16. Inference và scoring YOLO Hướng tới, chưa thuộc lát upload.
- [x] **M2** Chạy YOLO CPU riêng picks của proposal đang xem, explicit button/model, detection overlay/chi tiết và cache (#71), CONTRACTS §17. Không chạy toàn dataset, không đổi scoring/reference.
- [x] **V2** Context reference + binding feature, requested cache, proposal job hai bước + evidence primitives; reference_diversity/model_guided/text_model_guided (#72), CONTRACTS §18. Kiểm regression và smoke, không claim uplift.
- [x] **V2E** Kiểm thuật toán thật theo tiêu chí tự đặt: giao thức `docs/archived/2026-10-13/docs/evaluation/V2_CRITERIA.md`, kiểm ảnh độc lập và điều kiện CONTRACTS §15/18; không tune sau khi xem kết quả, không claim mAP. **Đã đánh giá, ngữ nghĩa chưa đạt**.
- [x] **D1** Kiểm dung lượng C, chuyển cache Hugging Face hub/Torch/npm/pip sang ổ repo và giữ đường cũ qua junction; terminal dự án dùng data/output cho cache/temp (#74). Không đổi pagefile Windows hoặc thuật toán.
- [x] **C1** Upload raw S3, CLIP Kaggle tải raw/decode strict/cleanup và đẩy result/progress S3; job store Supabase/local durable + phục hồi session, SQL/RLS/lease, test + live smoke (#75), CONTRACTS §19. Không migrate session/feedback/catalog SQLite trong lát này.

- [x] **C2** Upload video chỉ lưu; DINO+pHash và CLIP chạy Kaggle explicit, status riêng, FE nút/progress; giữ cache cũ, kiểm regression và GPU thật (#76), CONTRACTS §20.

- [x] **PL1** Lập plan nháp MVP → curation platform (2026-10-09): rà baseline, auth/ownership/phân quyền, workflow, storage/job, bảo mật, roadmap và gate kiểm thử. Chỉ tài liệu; chưa chốt R0 hoặc triển khai auth.

- [x] **PL2** Lập [plan nháp multi-user/runtime/hosting](../planning/MULTI_USER_RUNTIME_HOSTING_PLAN.md) (2026-10-09): đối chiếu đề RAV-26 và các nhánh, PII/worker `v5_khanh`, React/FastAPI/Contabo, dữ liệu riêng từng người, AI trong curation và gate benchmark. Chỉ tài liệu; T0–T7 chưa duyệt hoặc triển khai, không đổi contract/code.

- [x] **PL3** Bổ sung [bandwidth cloud web và quota CPU/GPU](../planning/BANDWIDTH_AND_QUOTA_PLAN.md) (2026-10-09, #78): PII mặt/biển số không OCR/NER, web/API cloud và AI tách riêng, ảnh đi storage private, giá/quota provider, top-up DB hoặc reset 3–5 giờ và global Kaggle budget. Chỉ tài liệu; reset 4 giờ/provider/credit ban đầu còn là đề xuất, chưa triển khai.

- [x] **PL4** Viết [spec deployment pilot 2–3 người](../../design/DEPLOYMENT_PILOT_SPEC.md) (2026-10-09, #79): tài khoản operator cấp, video upload private, BDD ảnh/video và local demo system chỉ đọc, PII lúc export kèm report, Render API/Contabo worker/Kaggle/S3, quota 4 giờ và budget ngoài VPS ~2 USD. Đã rà contract/ref/provider, checklist yêu cầu/liên kết/diff; chỉ tài liệu để duyệt. D0–D8 chưa triển khai, không gỡ local limits.

- [x] **PL5** Xác nhận cấu hình host (#80): frontend Render Static Site, Web API Render Free Web Service, ứng dụng AI/worker trên Contabo dự kiến 3 vCPU/8 GB/200 Mbit/s và điều phối Kaggle; evaluator phát triển riêng chạy local. Đồng bộ spec/architecture/CONTRACTS và quyết định; chỉ tài liệu, chưa deploy hay benchmark capacity.

- [x] **PL6** Rà/hoàn thiện [docs thực thi deployment và task subagent](../../backlog/DEPLOYMENT_EXECUTION_PLAN.md) (#81–#82): identity/data, runtime/storage, UI/export/ops, dependency/file ownership/fixtures/gates; auth/confirmation/RESTRICT và phiên mẫu readonly đã chốt; archive 68 file cũ/template, sửa read-order/links và rà chéo schema/DTO/transaction. Chỉ tài liệu; interface/schema/version vẫn draft D0, chưa code/deploy.

- [x] **PL7** Chốt phiên trống từ mẫu và dùng tài nguyên hiện có (#83): đồng bộ SoT/spec/task/DTO/tiêu chí test; không sao chép lịch sử mẫu, credential bootstrap từ `.env` repo qua cấu hình explicit. Kiểm 333 links/11 JSON/24 bảng và diff đạt. Chỉ tài liệu; không duyệt thay các interface kỹ thuật D0, chưa code/deploy.

### Deployment — các lát đề xuất từ PL6 (chưa duyệt D0/implement)

Chi tiết task/prerequisite/test ở [execution plan](../../backlog/DEPLOYMENT_EXECUTION_PLAN.md) §4. Tên DEP1 tránh trùng D1 chuyển cache #74. Một phiên một lát; song song bằng subagent bên trong lát, parent tích hợp rồi dừng.

- [x] **DEP0** Duyệt interface/schema/DTO/runtime/ownership và đồng bộ SoT; không code bằng draft (#84). Đã khóa CONTRACTS §21, architecture §11, UI_DESIGN và tạo shared contract fixtures.
- [x] **DEP1** Bootstrap hạ tầng operator-only + harness đo thật/cost/model policy; chưa mở đủ pilot (#85). Đã tách requirements-api, profile Dockerfile, health/readiness probe, đo cold start/RSS/leak và PILOT_RUNTIME_RUNBOOK.
- [x] **DEP2** Auth closed + ownership/RLS/Postgres/session/state/snapshot; local regression (#87). Đã tạo migration 001_pilot_identity, auth JWT adapter, pilot_db store adapter với RLS và optimistic locking, routes /api/v2/* và 22 tests đạt xanh.
- [x] **DEP3** Durable queue/worker/quota/global Kaggle/recovery/cancel + source pins/storage holds; một authority và transaction (#88). Đã tạo migration 002_pilot_runtime, pilot_runtime store, worker dispatcher/supervisor/kaggle_adapter, routes /api/v2/jobs & /quota, cùng 35 pilot tests đạt xanh.
- [x] **DEP2F** Sửa nền DEP2/DEP3 cho khớp CONTRACTS §21 + spec identity/runtime (#89): schema source/profile/session/decision/snapshot đúng cột, RESTRICT không CASCADE, store chỉ PostgreSQL (không fallback SQLite, không tự migrate khi boot → `scripts/pilot_migrate.py` + kiểm schema version), đọc qua RLS `authenticated` + claims, ghi qua backend role có khóa theo thứ tự §21.3, decision log append-only + snapshot server tự tính, policy thiếu → 503 (không seed số), claim `SKIP LOCKED` + round-robin, lease hết hạn → slot QUARANTINED, worker không publish giả, auth JWKS RS/ES + audience, app pilot riêng chỉ v2 (local không mount v2). Test trên **PostgreSQL thật** (portable `data/tools/pgsql`, `RAV_PILOT_TEST_DATABASE_URL`). Phần DEP3 chưa có (storage hold → DEP4; GPU budget/admission/cost → DEP5/DEP7) ghi rõ, không giả là xong.
- [x] **DEP4** Direct upload/private media/cache + seed allowlist/phiên mẫu readonly/tạo phiên riêng trống cùng nguồn và cấu hình; tích hợp runtime DEP3 và migration parity. Chi tiết (#91, CONTRACTS §21.12): migration `003_pilot_storage.sql`, storage hold, worker `VALIDATE_UPLOAD`/`RENDER_MEDIA`, import mẫu theo #90 (dry-run mặc định); test PG thật + S3 `moto`.
- [x] **DEP4F** Tích hợp `thien` vào `duy-mvp/v2`, sửa quota actual/extend hold, recovery trước reconcile, Unit validation, object cleanup và context timestamp; nối entrypoint worker CPU/MEDIA + supervisor. Không frontend hoặc DEP5–DEP8; không ghi cloud/push. Người dùng xác nhận phạm vi 2026-10-10.
- [x] **DEP-UI** Giao diện Web Deploy v2: Auth/Login Supabase & Dev Token, Quota badge, Màn Phiên mẫu SYSTEM + fork phiên, Studio v2 lưu quyết định vào PostgreSQL thật, placeholder cho propose/yolo (#93). Không đụng backend; FE độc lập theo DEPLOYMENT_EXECUTION_PLAN §3.
- [x] **DEP5a** Chuẩn bị đặc trưng DINO/CLIP trên worker/Kaggle: artifact FEATURE + readiness, vòng đời Kaggle thật (1 notebook riêng/slot GPU, UNKNOWN không đoán ABSENT), ngân sách GPU toàn cục do worker đọc Kaggle SDK; đặc trưng nguồn SYSTEM do operator import; migration `005_pilot_features.sql`; gate A05 — [DEP5_PLAN](docs/backlog/DEP5_PLAN.md) §2 (#94).
- [x] **DEP5b** Studio chạy trên v2 với đề xuất trên worker: PROPOSE 4 recipe không cần model (random/uniform/diversity/text_diversity), đóng băng ngữ cảnh + `proposal_context_changed`, route recipes/proposals/result/retry, migration `006_pilot_curation.sql`; parity + A06 — [DEP5_PLAN](docs/backlog/DEP5_PLAN.md) §3 (#95).
- [x] **DEP5c** Chuyển Studio sang v2: sửa lệch DTO DEP-UI, lớp v1/v2 theo chế độ build, đề xuất qua job, media request, upload; ẩn YOLO/Phân tích ở pilot; gate FE-AUTH/FE-JOB/FE-TEMPLATE — DEP5_PLAN §4.
- [x] **DEP5b2** YOLO_BATCH với model SYSTEM do operator import + METRICS/bản đồ/timeline/cặp gần trùng một job bấm mới tính; recipe cần dataset train để Hướng tới (UI tắt có lý do); migration `007_pilot_analysis.sql` — DEP5_PLAN §4b (#97).
- [x] **DEP5F** Review và tích hợp `origin/thien` (`9d30df7`) vào `duy-mvp/v2` (#99): giải conflict auth/client/decision log, giữ config deploy FE; sửa đo CPU/GPU, recovery cleanup và trạng thái kết quả phân tích. Chỉ merge local theo yêu cầu người dùng; không deploy/push.
- [ ] **DEP6** PII/report/owner confirm/READY download + source delete RESTRICT; quality gate/không raw bypass.
- [x] **DEP6a** Worker DELETE_SOURCE, tombstone/artifact/storage accounting + retry có kiểm tra (#112), FE theo dõi receipt. 410 backend/20 FE đạt; worker Contabo và FE/API Render Live, smoke video tổng hợp xóa S3 thật SUCCEEDED. Đây là phần xóa của DEP6; export/PII và gate tổng vẫn chưa xong.
- [ ] **DEP7** Build/release/env/backup/restore/rollback/ping/usage/cost; runbook đã thử.
  - [x] **DEP7-Hostname** Đổi URL sang `https://c4-app-026.onrender.com/app` (#118), commit `c6d18e0`; HTTPS/readiness/assets, auth/catalog/thumbnail và S3 preflight đạt; hai monitor lưu URL mới, service cũ Suspended. [Release/rollback](docs/setup/HOSTNAME_MIGRATION.md). Chưa upload file mới; không gộp frame #117 hoặc claim tăng tốc.
  - [x] **DEP7-Latency** Pipeline nhóm ghi tất toán quota/storage và đóng attempt + viewer chờ/lỗi/phát lặp (#116), commit `e23c894`. 434 backend PostgreSQL thật/24 FE đạt; worker healthy, FE/API Render Live, smoke receipt FINAL/cleanup/slots FREE. Clip cache 1,264 s nhưng clip mới 41,533 s, upload tổng hợp 32,340 s: chưa đủ mượt, không tick gate tốc độ hoặc DEP7 tổng. Release/rollback và số đo: [Latency QA](../../setup/LATENCY_QA.md).
  - [ ] **DEP7-Frames** Lưu sẵn thumbnail/frame trên S3 khi xử lý video, bỏ decode ảnh lúc phục vụ; giữ clip từ raw và tối ưu CPU/MEDIA/supervisor + FE polling (#117). [Thiết kế để duyệt](docs/backlog/FRAME_MATERIALIZATION_PLAN.md): đề xuất giữ lưới 4 fps/ID/cache hiện có, ghi theo lô, storage/receipt/cleanup/backfill nguồn cũ đầy đủ. Thiết kế đã chốt (#119, CONTRACTS §21.20), chia lát con; release cloud theo cả nhóm:
    - [x] **DEP7-Basic-Stability** Sửa review AGY: SYSTEM cleanup-only không phụ thuộc admission mới, temp trước download/ghi ảnh, guard receipt thay đổi; browser RED/GREEN thật, A/B 5 lượt + video 21 s/5 phút; 496 local regression/5 skip có lý do, 31 FE, tsc/build/ruff; Contabo cô lập 19 Linux + 45 integration đạt. [QA và giới hạn hiệu năng](../../setup/BASIC_FLOW_STABILITY_QA.md). Lát ổn định đạt; giảm latency preprocessing/DB và release cloud thật còn riêng. Không merge/push/deploy.
    - [x] **F1** Migration 010 frame set/frame + `VALIDATE_UPLOAD` tách ảnh theo lô (hold/receipt/publish đủ bộ) + media lookup READY, `max_side`, `NOT_PREPARED`, `SourceDTO.frames` + `DELETE_SOURCE` xóa ảnh theo lô (kéo từ F2 theo người dùng). 450 backend PostgreSQL 16 thật đạt/1 skip (1 lỗi môi trường opencv có sẵn trên baseline), 24 FE, ruff/tsc/build đạt. Đo local video 354 s 640×360: 1.417 unit/2.834 ảnh/62,2 MB, upload→READY 28,1 s (S3 moto local, không phải số cloud), RAM đỉnh 83 MB. Chưa release: policy cloud cần khóa frame + nâng CPU/deadline `VALIDATE_UPLOAD`.
    - [x] **F2** `PREPARE_FRAMES` nguồn cũ qua `POST /sources/{id}/preparations {feature: FRAMES}` + CLI SYSTEM (`scripts/pilot_prepare_system_frames.py`, dry-run/hash, chỉ tính GLOBAL). 455 backend PostgreSQL thật đạt/1 skip (lỗi opencv có sẵn); runbook §13. Chưa chạy CLI/backfill trên cloud.
    - [x] **F3** Process chính = supervisor; CPU/MEDIA/GPU process spawn riêng, process con chết → khởi động lại (1→60 s, >5 lần/10 phút → thoát mã 3); cache file theo SHA dùng chung an toàn liên process; identity/boot/health theo từng process. 464 backend PostgreSQL thật đạt/1 skip (lỗi opencv có sẵn); smoke spawn thật: giết CPU → chỉ CPU khởi động lại, clip MEDIA xong 0,89 s khi job CPU tách ảnh còn RUNNING; process con rảnh ≈ 63 MB. Chưa deploy worker Contabo.
    - [x] **F4** FE suất HTTP/ưu tiên/polling + `NOT_PREPARED` UI (Kho dữ liệu + ô ảnh) + dọn cache đĩa worker theo trần; test end-to-end local đúng luồng (API HTTP + worker spawn + moto server + PostgreSQL thật); số đo local cold/warm p50/p95; runbook release/rollback. 474 backend PostgreSQL thật đạt/1 skip (lỗi opencv có sẵn), 30 FE, ruff/tsc/build; E2E local đạt (gồm giết process CPU giữa job); thumbnail local p50 ≈ 0,1 s, clip mới p50 0,7 s. [Release/rollback](../../setup/FRAMES_RELEASE.md). DEP7-Frames tổng chưa tick: chưa migrate/deploy/đo cloud, chưa thử UI bằng trình duyệt.
  - [x] **DEP7-Ops** Màn Vận hành chỉ đọc + tracing upload/job tập trung (#115, §21.19): quyền server allowlist, timeline browser/API/worker, trạng thái heartbeat/quota/slot; regression isolation, release + cloud smoke. Không có thao tác điều khiển admin trong lát này. Release và bằng chứng: `docs/setup/OPERATIONS_RUNBOOK.md`; commit `7ac1901`.
  - [x] **DEP7-YOLO** Provision checkpoint SYSTEM YOLO11n COCO theo §21.17 (#114), import SHA/inference verified; auth UI tạo phiên 0909 → PROPOSE → YOLO_BATCH SUCCEEDED 16 ảnh/6 đối tượng, evidence đúng anchors/model, FINAL/cleanup. Lần đầu 167,254 s, tốc độ chưa đạt; mẫu SYSTEM chờ đúng SQLite/video #90, không dựng mẫu khác thay thế.
  - [x] **DEP7-GPU** Kaggle T4 thật (#113), đối soát session ID, quota/bonus/cache worker; DINO 0909 539/539, artifact READY, job SUCCEEDED, STOPPED/FINAL/cleanup_done và slot FREE. 28 regression PostgreSQL thật đạt, ruff đạt; CLIP thật và gate DEP7 tổng còn riêng.
  - [x] **DEP7-Combined** FE/API chung image, giữ hostname nhóm; container/Render Live/DB, routes/auth boundary và monitor đạt, API riêng suspended; docs release/rollback (#107). Worker nặng vẫn Contabo chưa deploy, chưa mở pilot; auth tài khoản thật/cloud E2E còn thiếu.
  - Bàn giao hiện trạng và checklist còn thiếu: `docs/setup/DEPLOYMENT_STATUS.md` (#105). Không tick DEP7 khi worker/CI/cloud smoke/backup-restore chưa đạt.
- [ ] **DEP7-Docker** Đóng gói FE/API thành image riêng cho Render, worker Contabo theo lựa chọn người dùng (#100); validate build/container trước publish; cần registry, Docker engine, env và cloud smoke thật.
  - Đang tiếp tục 2026-10-11 từ main ef316a2: S3 CORS/upload cloud và worker CPU/MEDIA Contabo; bàn giao `docs/setup/CONTABO_WORKER_RELEASE.md`. Không tick khi cloud smoke hoặc scoped role/policy còn thiếu.
  - #110: worker CPU/MEDIA đã rollout pool (`ef09e6a94b7c…`), ColdBrew READY/thumbnail, bonus 3 giờ CPU cho 2 account test, 405 test đạt/3 bỏ qua (PostgreSQL thật). Transaction probe ~2,33 → ~0,81 giây; API image pool và frame/context cloud smoke còn thiếu, chưa tick DEP7-Docker.
  - #111: log latency browser/API/DB/worker đã deploy; API pool nay Live, Render digest `af1396983676…`, worker `2bbc8db80101…` healthy; 406 backend/19 frontend đạt. [Cách đo](../../setup/UPLOAD_LATENCY_LOGS.md). Frame/context và các gate pilot khác vẫn chưa đủ để tick lát.
  - FE/API đã build, smoke, push Docker Hub private và deploy Live trên Render Free Singapore (#100); DB/schema readiness đạt (#102), API monitor Up (#103). Còn thay AWS key đã xuất hiện trong log và worker/cloud nghiệp vụ. Chưa tick hoàn tất lát.
- [x] **DEP7-Site** Hostname FE chứa IrishCoffee/T026; landing public `/`, đăng nhập `/login`, ứng dụng `/app` giữ hash route; build/publish image FE và nối CORS Render (#104). Live `https://irishcoffee-t026.onrender.com`; 19 test FE, tsc, build local/pilot, ruff và browser smoke đạt. Đăng nhập tài khoản thật/S3 upload thuộc smoke cloud còn chờ.
- [ ] **DEP7-FE** Chuẩn bị CI/cấu hình cập nhật và publish FE lên Render trước API (#98, người dùng giao 2026-10-10). Không mở pilot; cần xác nhận deploy thực trên Render. Contabo/API release tiếp tục sau khi xác định host và cấu hình.
- [ ] **DEP8** Bản thử A/B/C end-to-end; đủ BDD/model provenance và A01–A11 trước mở pilot.

### Việc sau deployment

- [x] **Compute-Runtime** (#127): CPU lanes/model owners opt-in, thread/RSS budget và warm CLIP text/YOLO, docs phù hợp VPS 4 vCPU/8 GiB; local 518 passed/5 skipped (hook lỗi có sẵn được loại rõ), Contabo candidate 95 passed, Ruff/diff-check đạt. Nghiên cứu SigLIP2/Gemma4 và Docker/native; [QA](../../setup/COMPUTE_RUNTIME_QA.md). Không rollout live/push, không claim PII/export hoặc mixed-load capacity đã nghiệm thu.
- [ ] **Architecture-A0–A5** Rà toàn luồng và thay giả định deployment bằng bằng chứng: [audit + lộ trình tổng](docs/backlog/ARCHITECTURE_DEPLOYMENT_REVIEW.md) (#125), [solution cụ thể từng vấn đề](../../backlog/ARCHITECTURE_SOLUTIONS.md) (#126). Đề xuất chưa đổi contract/topology/live; A0 trước chốt hạ tầng/tính năng.
  - [ ] **A0** Region/DB round trips/cache/capacity/cost + latency toàn luồng và mixed workload 1/3 account.
  - [ ] **A1** Read path không khóa ghi, batch command/media/job và publish/reconcile/telemetry chung; N1-Proposal là nhánh con.
  - [ ] **A2** Scheduling tương tác/bulk và CPU/RAM/thread/connection/fairness budget theo số đo.
  - [ ] **A3** Manifest unit/features/model bất biến + cache xuyên prepare/propose/analysis; exact input/version.
  - [ ] **A4** Estimator/quota UX + compatibility manifest/cutover/rollback/restore; đo rồi chốt numeric policy.
  - [ ] **A5** Binding dataset/model/provenance trước bounded recipe/report; PII/export và closed learning loop theo gate.
- [ ] **N1-Proposal** Ưu tiên trước tính năng tham chiếu: [lựa chọn tạm, submit cả lô, polling/publish DB và cache VPS](../../backlog/PROPOSAL_LATENCY_PLAN.md) (#124). Chưa implement/deploy; local v1 giữ nguyên.
  - [ ] **P0** Baseline trace click→batch + contract chi tiết trước code.
  - [ ] **P1** Draft UI không autosave; một POST lưu decisions + enqueue PROPOSE nguyên tử/idempotent.
  - [ ] **P2** Poll đúng job; đường đọc kiểm quyền không giữ khóa độc quyền profile.
  - [ ] **P3** Giảm round-trip publish/quota/close, giữ fence/receipt/refund/recovery.
  - [ ] **P4** Cache metadata/grid; cache embedding RAM có giới hạn chỉ khi số đo chứng minh cần.
  - [ ] **P5** Browser/local/PostgreSQL thật → Contabo cô lập → A/B → release theo yêu cầu riêng, cloud auth smoke.
- [ ] **N1–N6** Ưu tiên latency/tracing cloud → quota giữ nguyên kèm estimate → dataset/model version + binding phiên → recipe tham chiếu/model-guided → inference/report trên proposal → quality/frame đại diện và vòng gán nhãn/retrain ngoài hệ thống. Scope và test từng lát: [plan tiếp theo](../../backlog/POST_BASIC_FLOW_DEVELOPMENT_PLAN.md). Chưa implement, không gộp các tính năng này vào release hiện tại.
- [x] **DEP7-Frames-Release** Source `b195c61` (gồm `f0d278e`) đã Live Render, worker mới healthy, migration 010/policy mới và backup đầy đủ. Chặn admission ngắn, chuyển API trước worker vì upload payload cũ thiếu frames. Smoke Supabase/S3 thật: upload 64 ảnh, media/clip/quyền/xóa đạt; upload 52,719 s, clip mới 37,157 s nên chưa đạt độ mượt. [Evidence/giới hạn](../../setup/FRAMES_CLOUD_QA.md). Không push Git/merge main, không auto backfill; browser/GPU/backfill cloud và gate DEP7-Frames/DEP7 tổng vẫn chưa tick.

- [ ] (Nếu còn thời gian) agent chatbot trên command layer (CONTRACTS §11.4)
- [ ] (Sau MVP, bắt buộc) quản lý quyền kiểu Google Drive: project, vai trò, nhiều người cùng phiên (#55, arch §10)
  - Plan tham khảo: [MVP → curation platform](../planning/CURATION_PLATFORM_ROADMAP.md) (2026-10-09, **nháp chưa duyệt**; chưa đổi contract/triển khai auth).
- [ ] (Sau MVP) mở rộng sang nhiều video / tập con BDD100K
- [ ] (Giai đoạn thuật toán) backlog B1–B5 từ rà nhánh: objective disagreement, kiểm chứng uncertainty, thí nghiệm downstream thật, bộ chủ đề ADAS — `docs/archived/2026-10-13/docs/backlog/branch_review_2026-10-03.md`. **Không gộp nhánh; không trích số mAP 0.38 → 0.45 của `nam-init` (giả)**
- [ ] (Sau deployment) tối ưu trích xuất thumbnail Codec I-frame cho preview UI và proposal-only frame cho curation (#86)
- [ ] Trước mỗi mốc: rà `docs/design/RAV26_ALIGNMENT.md`
- [ ] Trước khi làm Live URL: rà `docs/design/LOCAL_SINGLE_USER_LIMITS.md`

S5–S7 (2026-10-07, #66): 113 test đạt, 1 bỏ qua symlink Windows; ruff/tsc/build đạt. Smoke trình duyệt với DINO thật: import 27 ảnh YOLO, pagination/filter, verify raw đổi, job lỗi/retry, import dedup/version mới, reference cố định v1, snapshot rỗng/dedup/current và export kept. Sửa lỗi request đầu cùng migrate DB bằng khởi tạo DB trước route. Dữ liệu thử riêng trong output/ui-smoke-s57.

S8 (2026-10-07, #67): 122 test đạt, 1 bỏ qua symlink Windows; ruff/tsc/build đạt. T21–T25: manifest bất biến, pixel đúng index (kể cả VFR có số vẽ trên ảnh), SHA đầy đủ dù size/mtime giữ nguyên, nguồn/nhãn thiếu hoặc đổi, giới hạn ZIP, cleanup khi lỗi/hủy gửi/boot, tương thích export kept. Smoke FE tải ZIP #2 có 2 mẫu dù kept còn 1, CRC hợp lệ và tmp không còn ZIP; lỗi nguồn hiển thị số mẫu. Backend chính 8000 và FE 3000 đã chạy.

- [x] **S9b** Nối text_match + text_diversity và FE, cache ảnh prepare nền độc lập; không tự đổi goal/LLM structure — CONTRACTS §15 (#68). Theo yêu cầu dùng thuật toán CLIP trên FE, thực hiện cùng S9–S10.

S9–S10 + S9b (2026-10-07, #68): 145 test đạt, 1 bỏ qua symlink Windows; ruff/tsc/build đạt. CLIP B/32 image + text thật với checkpoint/revision pin; GPU smoke 2 frame cosine với local ≈1.0, đủ shape/IDs/L2. Kaggle bằng đúng token .env repo, tạo notebook private riêng; job GPU dừng và temp dọn. Cache GPU đủ 86 mẫu commaai-sample và 120 mẫu video YouTube 30 s đã tích hợp, job API 120 mẫu state=done. Smoke FE: tạo phiên CLIP/cars on a road, hai đợt 5 mẫu với giữ 1, explain câu đóng băng + novelty DINO đúng field, negative giống positive trả đợt rỗng không lấp; khôi phục negative rỗng và mở lại đợt hữu dụng. Query explicit, chưa claim LLM goal structure/coverage/hiệu quả nghiệp vụ; S11 vẫn cần dữ liệu BDD100K.

M1 (2026-10-07, #70): 155 test đạt, 1 bỏ qua symlink Windows; ruff/tsc/build đạt. Kiểm multipart .pt, sai định dạng/rỗng/giới hạn raw và giải nén, dedup giữ metadata, rollback file khi DB lỗi, checksum đầy đủ kể cả size/mtime giữ nguyên, migration v3→v4, reference model/dataset train đóng băng trong snapshot, tương thích phiên cũ. Smoke FE môi trường riêng: lỗi .txt rõ ràng, upload torch.save fixture (không phải YOLO), verify SHA, chặn tạo phiên khi model thiếu dataset train, tạo phiên với dataset v2, hiện model/chưa inference. FE chính 3000 + BE 8000 đã chạy, GET /models qua proxy 200; không thêm checkpoint giả vào kho chính. Chưa chạy inference YOLO.

M2 (2026-10-07, #71): 166 test đạt, 1 bỏ qua symlink Windows; ruff/tsc/build đạt. Kiểm đúng ordered anchors, [] không box, cache qua restart và tách model/đợt, không thay state/proposal, lock busy, lỗi checkpoint/task/version/box, nguồn/model đổi không lưu partial, VFR frame endpoint đúng read_exact. Smoke FE/BE chính bằng YOLO11 nano COCO pretrained chính thức: đợt 1 phiên 0ea18449c8c24f8c98d9083beee6f7b9 chỉ 16 picks, trả 57 box; đối chiếu IDs/params/box range qua API và artifact. FE chọn model, explicit run, xoá box khi bỏ chọn, đọc lại cache, M off/on, Space → ảnh gốc nhãn/confidence/t0 đúng; giữ nguyên quyết định. Bằng chứng output/ui-smoke-m2. Checkpoint nano dùng thử, không tự liên kết với dataset train; kết quả chưa tham gia scoring.

V2 (2026-10-07, #72–73): 176 test đạt, 1 bỏ qua symlink Windows; ruff/tsc/build đạt. Reference ngoài pool, old hash, bounded original/flip, runtime/cache isolation, checksum checkpoint/raw, context đổi không publication, restart/retry, replay scores chỉ đọc cache, finite weighted mean, seeded exploration và không nới ngưỡng. Smoke riêng output/ui-smoke-v2 dùng DINO/CLIP/YOLO nano thật: model_guided và text_model_guided đều 16/86 candidates, 16 picks, 15/14 box ảnh gốc; FE tự hiện overlay, nearest train/scalar/method, reload giữ evidence. Dataset train trong smoke là khai báo mô phỏng, không là dữ liệu train nano, không đưa vào kho chính, không claim uplift. Theo yêu cầu dọn C: CLIP revision pin chuyển sang data/model-cache (SHA đầy đủ khớp), bỏ bản conversion trùng và cache tải pip; C từ 0,94 lên 3,33 GiB trống. Nạp CLIP offline từ D khớp GPU embedding cosine ≈1,0. FE chính 3000 + BE 8000 đang chạy.

V2E (2026-10-07): 38 regression đạt, ruff/diff-check đạt; 9 cấu hình × 2 recipe trên 206 frame với DINO/CLIP/nano thật, hai lượt fresh/cache và nhãn thị giác gắn ordered IDs. Gate/NMS/seed/replay/review/bounded inference đúng; boundary yêu cầu 8 trả 2, không nới, 4 lượt YOLO. Ngữ nghĩa chưa đạt: cả sáu câu đều 206 eligible; cao tốc đúng 4/8 text_diversity và 1/8 guided, so Uniform 3/8; đèn/đêm/tuyết/chó 0/8 dù vẫn trả 8. Negative cao tốc giúp 7/8 và 8/8; positive=negative/ngưỡng 1 trả rỗng. Không tune/thay thuật toán sau khi xem kết quả, không claim uplift; fixture train vẫn mô phỏng, không đổi phiên chính. Artifact output/v2-criteria-eval và docs/evaluation/V2_CRITERIA.md. C cuối lượt ~1,2 GiB do có pagefile Windows ~19,6 GiB, cache model vẫn D; chưa quy toàn bộ biến động cho test, không tự sửa bộ nhớ ảo.

D1 (2026-10-07): C từ 1,76 lên 3,28 GiB trống; HF hub 557,8 MiB/39 file, Torch 88,1 MiB/230 file, npm 902,8 MiB/2.342 file chuyển sang data/system-cache trên D sau kiểm SHA-256 từng file, pip rỗng cũng dùng junction. Log xet đang mở giữ nguyên trên C, không tắt backend. Script xác minh 8 cache/temp path trên D, Python tạo/dọn file tạm trên D, npm đọc đúng cache mới; API session/model/frame qua FE 200, 8 test core và ruff/diff-check đạt. Pagefile vẫn 19,59 GiB trên C; không coi dọn cache là giải quyết mọi nguyên nhân thiếu dung lượng.

C1 (2026-10-08, #75): 201 test đạt, 1 bỏ qua symlink Windows; ruff/diff-check đạt. Smoke S3/GPU riêng 86 frame khớp cache cũ cosine trung bình 1,0. FE chính chạy CLIP 0908.mp4 qua S3 + Supabase: 689/689, float32 (689,512), ordered IDs/finite/L2 đúng; kill/restart API khi GPU đang chạy, lease owner mới thu hồi cùng session, không launch GPU thứ hai. Result NPZ lưu S3 và cache FieldStore, raw S3 còn, cleanup_done/session_stopped=true; public key bị từ chối bảng/RPC, lease khác bị chặn. Artifact output/cloud-live-20261008; BE 8000/FE 3000 chạy cấu hình cloud, secret chỉ backend, cache/temp D. Core SQLite/bản làm việc raw vẫn local theo phạm vi C1.

C2 (2026-10-08, #76): 205 test đạt, 1 bỏ qua; ruff/tsc/build/diff-check đạt. Upload FE c2-gpu-smoke.mp4 chỉ lưu S3, 0 job; bấm DINO → 8/8, CLIP vẫn chưa chạy; bấm CLIP riêng → 8/8. DINO float32 (8,384) khớp bản local cosine min/mean=1,0, pHash/ordered IDs đúng; CLIP float32 (8,512), hai cache bằng artifact S3. Hai session dừng, cleanup_done=true, raw/model S3 còn; restart BE phục hồi đúng status từ Supabase, yêu cầu khi đủ cache không tạo job mới. FE nút/progress riêng và Phiên mới chọn được video có DINO. Artifact output/c2-live; BE 8000/FE 3000 chạy bản cuối, cache/temp D; kiểm GPU này là smoke 8 frame, không benchmark video dài hay ngữ nghĩa curation.

DEP2F (2026-10-10, #89): 272 test đạt (69 test pilot chạy trên **PostgreSQL 16 thật** — cluster portable `data/pg-test`), 1 bỏ qua symlink Windows;
2 lỗi có sẵn từ trước ở `test_cloud_embedding` (thiếu metadata `opencv-python-headless` trong `.venv`, không liên quan pilot); ruff đạt.
Test thật bắt được 2 lỗi mà SQLite che: `SELECT … FOR UPDATE` cần quyền UPDATE cột (đã cấp `UPDATE(display_name)` cho role API) và `publish` quên nhả phần dư hold/pin.
Đã kiểm: RLS A/B, cột `raw_key` không lộ cho `authenticated`, trigger bất biến, không CASCADE/SET NULL, idempotency, CAS, target_k cả lô, snapshot server tính
(khớp `service/snapshot.py`), race hai luồng enqueue/claim, lease hết hạn → slot QUARANTINED → reconcile, round-robin, dispatcher không publish giả,
xóa nguồn RESTRICT/tombstone rollback khi thiếu policy, app pilot import nhẹ (process mới không có torch/numpy; RSS 50 MB, cold start 0,8 s).
Checksum migration chuẩn hóa CRLF/LF (operator Windows và container Linux cùng checksum). Còn lại (ghi rõ, không giả là xong): storage account/hold → DEP4;
GPU budget/admission/cost → DEP5/DEP7; `POST /sessions/{id}/sources` chưa có route; readiness DINO/CLIP luôn `false` tới khi worker publish artifact (DEP5);
entry worker `python -m src.rav.worker --mode pilot` chưa có (Dockerfile target worker còn CMD cũ) — làm cùng handler ở DEP5.

DEP4 (2026-10-10, #90–#91): 296 test đạt (test pilot chạy trên PostgreSQL 16 thật + S3 `moto`), 1 bỏ qua; 2 lỗi có sẵn `test_cloud_embedding` (môi trường); ruff đạt.
Migration `003_pilot_storage.sql` (export dời 004). Upload trực tiếp: presigned POST staging → `complete` HEAD + enqueue cùng transaction → worker tải có giới hạn,
SHA-256, `_probe` + decoder pyav (core ID/`n_units` khớp local), kiểm duration/pixel, PUT canonical kèm ChecksumSHA256 → nguồn PRIVATE READY; cùng bytes → dùng lại nguồn;
staging overwrite sau xác minh không đổi canonical; dung lượng giữ 2 × declared, sweep staging sau hạn + grace. Media: unit phải nằm trên lưới decoder, cache theo owner,
thumbnail/frame JPEG (pixel khớp decoder core), context mp4 H.264 ±3 s; nguồn chờ xóa không cấp URL; giới hạn `max_media_requests` riêng. Phiên mẫu: list/get projection lọc,
tạo phiên trống (state_version=1, 0 đề xuất/quyết định/snapshot/job, pin SESSION), idempotent, mẫu BLOCKED → 409; import theo inventory #90 dry-run trên dữ liệu thật:
2 nguồn, 2 mẫu, 110 sự kiện, hash tất định, không path máy. Test thật bắt thêm: `FOR SHARE` cần quyền UPDATE cột; unique nguồn chặn upload lại sau xóa (đã loại DELETED khỏi index).
Chưa làm (ghi rõ): ghi S3/Supabase thật + `--apply` import (người dùng chọn chưa ghi cloud); màn FE deploy (đăng nhập, Dữ liệu của tôi, Phiên mẫu) — chưa có lát; feature DINO/CLIP
cho nguồn SYSTEM/upload (job PREPARE) thuộc DEP5; `moto` không kiểm `content-length-range` (worker tự kiểm byte thật).

DEP4F (2026-10-10, #92): fast-forward `thien` (`f3aa54a`) vào `duy-mvp/v2`; commit code `426e1b4`.
Regression toàn repo: 355 đạt, 1 bỏ qua symlink Windows. Sau chỉnh cuối layout canonical/cleanup: 26 test upload/media/object và 66 test schema/store/quota/queue/object đạt trên PostgreSQL 16.15 thật + S3 moto; ruff/diff-check đạt.
Actual quota được giữ thêm và tất toán đầy đủ; thiếu credit giữ usage/hold/pin/slot chờ reconcile. Lease mất chờ process dừng trước retry; cancel/abort giữ nguyên tài nguyên khi usage UNKNOWN.
Receipt object commit trước PUT, publication nhận đúng object, supervisor dọn key chưa nhận trước trả storage/pin/slot; crash, delete failure, dedup race, retry cùng canonical và hai supervisor được kiểm tra.
Unit decision/media kiểm chung metadata/lưới/phạm vi cả lô; clip CFR60/VFR giữ timeline tương đối và cửa sổ ±3 s. Worker CPU/MEDIA có entry, signal/deadline watchdog độc lập DB và heartbeat theo policy; Docker sửa dependency đọc được bởi non-root.
Giới hạn kiểm chứng: Docker daemon không chạy nên chưa build image; chưa smoke S3/Supabase/host thật. FE, DEP5–DEP8 và hosting tiếp tục ở lát sau; chưa push.

DEP-UI (2026-10-10, #93): Giao diện Web Deploy v2 hoàn thành độc lập (không chạm backend `src/rav/*`).
Xác thực Supabase JWT / Dev Token, Quota badge (CPU 4h UTC / GPU minutes / admission warnings), màn Phiên mẫu SYSTEM (`#templates`) với fork phiên độc lập vào PostgreSQL thật.
Studio v2 tiếp tục hỗ trợ đầy đủ phím tắt, đổi trạng thái keep/drop/clear, hoàn tác và chốt snapshot manifest.
Cơ chế Placeholder thông minh: các nút gọi API worker curation chưa có (đề xuất tự động `POST /api/v2/sessions/{id}/proposals` và detector `POST /api/v2/proposals/{id}/detections` thuộc DEP5) hiển thị thông báo tiến độ phát triển rõ ràng, không gây crash hay ném lỗi kĩ thuật.
Kiểm thử: `npx tsc --noEmit` và `npm run build` vượt qua 100%; `ruff check src tests` đạt; 261 test backend xanh (1 bỏ qua symlink, 1 hook path có dấu đã biết). Chưa push.

DEP5a (2026-10-10, #94, CONTRACTS §21.14): 366 test đạt (test pilot trên PostgreSQL 16 thật + S3 `moto` + Kaggle giả chạy `s3_clip_worker` thật,
embedding giả), 1 bỏ qua; 2 lỗi có sẵn `test_cloud_embedding` (thiếu metadata `opencv-python-headless` trong `.venv`); ruff đạt.
Migration `005_pilot_features.sql` (snapshot GPU bất biến, `gpu_hold`, một FEATURE READY/nguồn/feature). Route `POST /sources/{id}/preparations`
(200 sẵn sàng / 202 job / 409 `system_feature_missing`), `SourceDTO.dino/clip` từ artifact. Claim GPU kiểm snapshot tươi + ngân sách toàn cục,
thiếu → `WAITING_RESOURCE` `gpu_budget_unverified|exhausted`, claim xét lại. Worker: notebook private cố định mỗi slot, INTENT → ATTACHED,
lỗi tạo → LAUNCH_UNCERTAIN + tách attempt, dừng phải xác minh (không → STOP_PENDING + tách); supervisor chỉ trả slot khi notebook hết session,
FOUND có handle → hủy, UNKNOWN/không handle → giữ. NPZ artifact có receipt trước PUT; object trao đổi `jobs/<job>/<attempt>/` xóa theo tên.
Đã kiểm: CLIP/DINO đầu-cuối (IDs khớp decoder, L2, pHash uint64), hết ngân sách chờ rồi chạy lại, scope A/B, slot bận không mở session thứ hai,
launch không chắc + provider lỗi giữ slot, dừng chưa xác minh, kết quả hỏng không publish nhưng vẫn tính GPU, hủy giữa chừng dừng session,
snapshot/hold chỉ worker ghi. Import đặc trưng SYSTEM: dry-run dữ liệu thật 2 video mẫu đủ DINO+CLIP, hash `bb0a090b…`.
Chưa làm (ghi rõ): smoke GPU Kaggle thật (người dùng chọn để riêng) — cần xác minh `get_kernel_session_status` phản ánh session tạo bởi
`create_kernel_session` và đơn vị quota trước khi mở pilot; `--apply` import lên cloud; tiến độ done/total của job GPU trên heartbeat (FE chỉ hiện % khi total>0).

DEP5b (2026-10-10, #95, CONTRACTS §21.15): 378 test đạt (PostgreSQL 16 thật + S3 `moto` + worker PROPOSE thật), 1 bỏ qua;
2 lỗi có sẵn `test_cloud_embedding` (môi trường); ruff đạt. Một lượt chạy toàn bộ giữa chừng có `test_base_then_bonus_fifo_and_refund_to_origin`
fail, chạy lại riêng và toàn bộ đều đạt — chưa tái hiện được, ghi lại để theo dõi.
Migration `006_pilot_curation.sql` (proposal.job_id, worker INSERT proposal, khóa phiên khi publish). Route `GET /recipes` (catalog tĩnh
`pilot_recipes.json`, test giữ khớp registry), `POST/GET /sessions/{id}/proposals[/{n}]`, `GET /jobs/{id}/result`, `POST /jobs/{id}/retry`.
Worker PROPOSE trên queue CPU: raw/artifact theo SHA (cache), field chỉ từ artifact (cache-only), replay quyết định + đề xuất cũ đã đóng băng,
gọi `pipeline/propose.py`; giải thích đợt và bản ghi đề xuất dùng hàm thuần tách từ `service/explain.py`, `service/proposal.py` (local không đổi hành vi).
Đã kiểm: parity với đường core local cho 4 recipe (picks/ID/thứ tự, điểm ≤ 1e-6) và vòng hai sau keep/drop/clear; ngữ cảnh đổi → FAILED
`proposal_context_changed` không ghi, vẫn tính CPU; thử lại đóng băng lại theo state mới (`retry_of`); một job/phiên; key trùng → cùng job;
recipe ngoài pilot/config sai loại → 422; tham số sai → worker FAILED `invalid_config`; `feature_not_ready` kèm nguồn/feature; A/B 404;
A06 3 tài khoản: claim xoay vòng, keep/drop phiên khác < 1 s khi worker đang chạy. Test hàng đợi DEP3 đổi kind giả sang METRICS (PROPOSE có hook thật).
Chưa làm (ghi rõ): FE (DEP5c); YOLO/recipe cần model/METRICS/bản đồ (DEP5b2); `text_diversity` cần CLIP text model trên host worker (test dùng encoder giả).

DEP5c (2026-10-10, #96, CONTRACTS §21.16): FE hai chế độ build (`VITE_RAV_MODE=local|pilot`); bản local quay lại không cần đăng nhập
(DEP-UI bắt đăng nhập v2 cả ở local nên local không vào được). Pilot: `@supabase/supabase-js` (session/refresh), `auth_generation` bỏ response cũ,
401 refresh một lần, đăng xuất/hết hạn tải lại trang xóa state; `pilotApi` cùng interface `api` (hook/component giữ nguyên) chỉ gọi `/api/v2`;
Studio đề xuất qua job (lý do chờ, Hủy, Thử lại, khóa giữ/bỏ khi chạy), `feature_not_ready` → nút chuẩn bị; ảnh/clip qua media request (lười, poll GET, xin URL mới khi hết hạn);
Kho dữ liệu pilot (upload presigned POST có tiến độ, DINO/CLIP, xóa có `resource_in_use`); Phiên mới pilot; Phiên mẫu sửa lệch DTO (counts, fork `{session}`) + lịch sử chỉ đọc;
ẩn YOLO/Phân tích/xuất ZIP/dataset/model ở pilot. Backend: `SourceDTO.core_source_id`, `SessionDTO.n_kept/n_dropped`.
Kiểm: vitest 10/10 (FE-AUTH/FE-JOB/FE-TEMPLATE, media, unit), `tsc` + build cả hai chế độ, pytest pilot + toàn bộ, ruff.
**Smoke 3 tài khoản 62/62** trên stack local giả lập (API/worker pilot + Postgres portable + `moto server` + token HS256, qua proxy vite): mẫu + lịch sử,
fork idempotent không tự đề xuất, 3 đề xuất song song trên worker thật (đặc trưng import), media JPEG, giữ/bỏ + CAS 409, snapshot, sidebar counts,
cách ly A/B, ngữ cảnh đổi → thử lại, upload trực tiếp → READY, chuẩn bị DINO chờ GPU → hủy.
Chưa làm (ghi rõ): click-through trình duyệt thật (không có công cụ điều khiển trình duyệt — smoke ở mức HTTP qua đúng proxy FE; hướng dẫn tự mở UI ở runbook §11);
Supabase/S3 thật; YOLO/METRICS/bản đồ (DEP5b2); xuất PII (DEP6).

DEP5b2 (2026-10-10, #97, CONTRACTS §21.17): 382 test đạt (PostgreSQL 16 thật + S3 `moto`; YOLO11n COCO thật trên CPU), 1 bỏ qua;
2 lỗi có sẵn `test_cloud_embedding` (môi trường); ruff, `tsc`, vitest 13/13, build đạt. Migration `007_pilot_analysis.sql` (evidence YOLO + kết quả phân tích,
bất biến, RLS owner); export dời `008_pilot_export.sql`. `scripts/pilot_import_model.py` (dry-run → chạy thử detect → `INFERENCE_VERIFIED`, provenance COCO,
không coi là model train BDD). Route `GET /models`, `POST/GET …/proposals/{n}/detections`, `POST/GET …/analysis`; worker `YOLO_BATCH`
(`pipeline/batch_yolo`, pixel đúng anchor) + `METRICS` (`service/metrics.metrics_record` tách thuần, view `embedding_map`/`timeline`/`duplicate_pairs`).
Dựng Context chung PROPOSE/METRICS/YOLO ở `worker/session_context.py`. Recipe cần dataset train: catalog `available=false` + 422
`missing_model_training_provenance` (Hướng tới, #97). FE pilot: panel YOLO (model SYSTEM, chạy = job), tab Phân tích + nút "Tính phân tích", ghi chú kết quả cũ,
panel phải đọc kết quả gần nhất, recipe chưa dùng được hiện mờ kèm lý do.
Đã kiểm: YOLO evidence đúng thứ tự picks, lần 2 dùng lại không job, model chưa xác minh 422, A/B; phân tích parity với `metrics_record` local (trừ thời gian duyệt),
GET không tự tính, cùng ngữ cảnh trả luôn, quyết định mới → `current=false`, metric tự chọn/`on` đợt, metric sai → FAILED `metric_not_found`, CAS.
Smoke stack local: DEP5b2 16/16 (YOLO11n thật trên worker 6 ảnh ≈7 s, phân tích đủ 6 metric + 3 view) và smoke DEP5c chạy lại 62/62.
Test hàng đợi DEP3 đổi kind giả sang `EXPORT_BUILD`/`EXPORT_FINALIZE` (METRICS đã có hook thật).
Chưa làm (ghi rõ): 3 recipe cần dataset train (chờ BDD + checkpoint train thật); click-through trình duyệt thật; Supabase/S3/GPU thật.

DEP5F (2026-10-10, #99): tích hợp `origin/thien` tip `9d30df7` vào `duy-mvp/v2`; giải conflict client/auth/decision log, giữ URL API và publishable/anon key; Render chọn pilot, CI thêm vitest và build local/pilot. Sửa quota CPU tính lẫn giữa CPU/GPU bằng tiến trình GPU riêng, ghi mốc launch và tính GPU tới stop verified khi supervisor đối soát; cleanup exchange lỗi không trả slot sớm; replay phân tích current đúng và refresh phiên cũ không đăng xuất tài khoản mới.
Kiểm chứng cuối: pytest toàn repo **385 đạt, 3 bỏ qua** (PostgreSQL 16.15 thật, S3 moto; 2 test YOLO thiếu `data/models/yolo11n.pt`, 1 gate khác theo điều kiện test); regression riêng GPU/analysis/entry **21 đạt, 2 bỏ qua**; vitest **14/14**, ruff, tsc, build local/pilot và diff-check đạt. Một lượt trước CLIP-cache lỗi, chạy riêng + toàn suite lại đạt, ghi #99 để theo dõi. Chưa kiểm Supabase/S3/Kaggle/GPU thật, Docker image hoặc hosting; chỉ merge local, chưa push.
