# PLAN — deployment RAV-26

Cập nhật 2026-10-14, #130. Một phiên một lát; [AGENTS](AGENTS.md), [guide](docs/IMPLEMENT_GUIDE_V2.md),
[CONTRACTS](docs/design/CONTRACTS.md), [kiến trúc](docs/architecture_diagram.md).
Lịch sử MVP/DEP0–DEP8 và các số đo tại từng mốc giữ trong [plan archived](docs/archived/2026-10-13/PLAN.md).

## Trạng thái có bằng chứng

| Phạm vi | Trạng thái | Bằng chứng |
|---|---|---|
| FE/API gộp Render + worker Contabo | Live #133 policy interactive-20261014/migration014, lanes 4 CPU/6 GiB | [Interactive QA](docs/setup/INTERACTIVE_CPU_QA.md) |
| Auth/owner, queue/quota, upload, ảnh S3, clip, xóa | Có code; cloud smoke cơ bản, latency còn cao | [Frames cloud QA](docs/setup/FRAMES_CLOUD_QA.md) |
| Shutdown/delete/import recovery | Đã sửa và test, nằm trong bản frames đã release | [Basic QA](docs/setup/BASIC_FLOW_STABILITY_QA.md) |
| Compute-Runtime #127 | CPU lanes đã bật #132, 4 CPU/6 GiB | [518 local + 95 Contabo tests](docs/setup/COMPUTE_RUNTIME_QA.md) |
| Large-Upload #134 | Candidate local pass; chưa rollout cloud | [Large upload QA](docs/setup/LARGE_UPLOAD_QA.md) |
| Progress & ETA #135 (N2) | Candidate local pass; 572 backend + 34 frontend pass | [Progress ETA QA](docs/setup/PROGRESS_ETA_QA.md) |
| PII-Export #136 | Candidate local pass; unit + pipeline pass (7/7); tsc + 34 Vitest pass | [PII Export QA](docs/setup/PII_EXPORT_QA.md) |
| Dataset/model binding #137 (N3) | Candidate local pass; 583 backend + 34 frontend pass | [Dataset Model Binding QA](docs/setup/DATASET_MODEL_BINDING_QA.md) |
| Storage Unblocked #138 (Lát 1) | Sau fix mixed-policy: 98 regression pass trên PostgreSQL thật; Ruff pass | [Storage Unblocked QA](docs/setup/STORAGE_UNBLOCKED_QA.md) |
| Perception-Instability #140 | Live source 86c10ab7; job/model thật, cache/feedback/switch smoke pass; chưa benchmark video dài/nhiều người | [Release QA](docs/setup/PERCEPTION_INSTABILITY_RELEASE_QA.md) |

| VPS-Upload-Ingest #139 (Lát 2-3) | Live 2026-10-15; authenticated cloud upload 35.80 MiB → READY 165.14 s; resume/abort/TLS/S3 hash pass; [release QA](docs/setup/VPS_UPLOAD_RELEASE_QA.md) | [VPS Upload Ingest QA](docs/setup/VPS_UPLOAD_INGEST_QA.md) |

Test xanh không thay acceptance cloud, semantic quality hoặc capacity. Không tick pilot tổng vì có URL Live.

## Lát hiện tại

- [x] **Tài khoản demo dùng chung (§21.41)** Nút nạp email/mật khẩu demo trên login, giữ xác thực Supabase;
  tạo tài khoản thường với signup policy hiện có, không operator/model-write hoặc quota bonus. Test FE và login/quota thật.

- [x] **ODD context/cache và thời gian từng bước (§21.40)** Bỏ raw download/probe cho ODD không model
  từ metadata đã đóng băng; cache vector CLIP theo từng prompt/model/revision trên đĩa; hủy trước I/O;
  phase timings có cấu trúc qua progress_detail và UI, không sinh log từng frame. Giữ owner/CAS/lease và thuật toán.
  78 regression PostgreSQL/S3 cô lập,65 frontend tests, Ruff/tsc/pilot build và browser component đạt.
  [QA](docs/setup/ODD_PHASE_CACHE_QA.md). Đã deploy sourcefc53ed88 worker/API/FE,2 cloud jobs đạt;
  cache warm bỏ nạp/mã hóa CLIP, metadata-only không raw. Stage sums cold50.149s/warm22.766s còn overheadDB.
  FE local3000 nối cloud đã mở; không push Git. Screenshot demo dùng tài khoản thường riêng theo user.

- [x] **Mẫu chỉ đọc tham chiếu artifact admin (§21.39)** Theo quyết định mới: hủy job copy đang chạy,
  nguồn SYSTEM dùng chung raw/frame/embedding bất biến; không copy S3 hay nhân bản theo tài khoản.
  Pin nguồn gốc, chặn xóa/cleanup làm mất file dùng chung; giữ quyền xuất bản/đọc trong phiên cũ.
  Migration additive, endpoint/FE mới, giữ handler legacy cho dừng và cleanup job cũ. Test PostgreSQL/S3 cô lập.
  85 hồi quy +8 focused cuối, Ruff/tsc/63 FE/pilot build đạt; [QA](docs/setup/ADMIN_SAMPLE_REFERENCE_QA.md).
  Đã deploy source dd13f663/migration027 + worker/FE/API; 9 focused cuối +10 interactive, cloud tham chiếu
  6710 frame trong5.72s, không job/copy/charge mới. Mẫu mới ẩn, nguồn riêng giữ nguyên. [Release QA](docs/setup/ADMIN_SAMPLE_REFERENCE_QA.md).

- [x] **UI chọn video mẫu (§21.38)** Nút/tab có trạng thái rõ; thẻ video có thumbnail tải lazy qua media
  hiện có, tên giới hạn dòng kèm tooltip, metadata và nút tạo bản sao nổi bật. Không đổi API hay luồng sao chép.
  TypeScript/63 frontend tests/pilot build/browser component warm-dark đạt; [QA](docs/setup/ADMIN_SAMPLE_COPY_QA.md).
  Chưa push/deploy phần tinh chỉnh này.

- [x] **Release admin sao chép dữ liệu mẫu (§21.38, 2026-10-17)** Người dùng yêu cầu push/deploy;
  source `c32b7240`, cập nhật worker trước FE/API, giữ schema/policy. Kiểm tra candidate, S3 COPY
  bằng object QA riêng, health/auth/catalog sau rollout đạt. [Release QA và giới hạn](docs/setup/ADMIN_SAMPLE_COPY_RELEASE_QA.md);
  chưa chạy lại full admin copy job trên cloud, không dùng nguồn riêng thật của người dùng để thử.

- [x] **Admin chọn video từ kho riêng làm mẫu (§21.38)** Sửa nút file upload; tạo bản sao SYSTEM nền từ
  raw/FRAMES/DINO/CLIP có sẵn, giữ nguồn gốc và phiên cũ; readiness/owner/receipt/accounting/cancel.
  PostgreSQL/S3 cô lập: 78 hồi quy +10 focused cuối; frontend63/tsc/pilot build/Ruff/browser component đạt.
  [QA và giới hạn](docs/setup/ADMIN_SAMPLE_COPY_QA.md). Đã push/deploy theo lát release phía trên.

- [x] **Deploy ODD coverage (2026-10-17)** Theo yêu cầu người dùng: build/pin source `065ff890`,
  kiểm compatibility và image candidate, idle/drain rồi rollout worker + FE/API với rollback images;
  cloud smoke bằng phiên QA riêng, catalog/UI/query/feedback/reload và detector thật đạt; readiness200,
  worker healthy, Render `dep-db9m0a61egvs73fpar3g`. Không đổi schema/policy, không push Git.
  [Release QA và giới hạn latency](docs/setup/ODD_COVERAGE_RELEASE_QA.md).

- [x] **ODD coverage PoC (§21.37)** Native strategy CLIP contrast gate per Object×ODD/scenario union,
  bounded shortlist, dynamic coverage/DINO selection and optional requested-only instability; FE query
  inputs/frozen trace. Preserve legacy algorithms and resource gates. 72 backend/61 frontend tests,
  Ruff/tsc/pilot build/browser component QA and real CLIP/DINO smoke passed. Deploy riêng theo lát trên;
  không push Git. [QA and limits](docs/setup/ODD_COVERAGE_QA.md).

- [x] **Lát 1 — Đăng ký và khởi tạo tài khoản (CONTRACTS §21.34)** Mở Đăng nhập / Đăng ký trên `/login` (email, mật khẩu, tên hiển thị tùy chọn, trạng thái xác nhận email rõ ràng) và gợi ý 2 hướng sau đăng nhập lần đầu (**Dùng dữ liệu mẫu** / **Upload dữ liệu của bạn**). Backend bootstrap một đường duy nhất trong `extract_actor_context` → `PilotDB.ensure_profile`: tạo `rav_pilot_profile` + `rav_pilot_storage_account` trong cùng transaction (`ON CONFLICT DO NOTHING`, idempotent khi đăng nhập đồng thời) gắn với policy đăng ký tường minh (`signup_default=true` hoặc `RAV_PILOT_SIGNUP_POLICY_VERSION`; thiếu/sai → `503 policy_unconfigured`). Không sửa profile đã có, không mở lại tài khoản `active=false`, giữ nguyên cơ chế quyền admin `RAV_PILOT_OPERATOR_IDS`. Migration `023_pilot_signup_bootstrap.sql`; 47 PostgreSQL test + 52 Vitest + Ruff/tsc/pilot build đạt.
- [x] **Lát 2 — Kho dữ liệu, dữ liệu mẫu và giữ tiến trình upload (CONTRACTS §21.35)** Kho dữ liệu thống nhất **Dữ liệu của tôi / Dữ liệu mẫu** (video + model), cờ `published` độc lập với trạng thái xử lý, nút **Tạo phiên từ dữ liệu này** trên video mẫu sẵn sàng, tái dùng frame/DINO/CLIP của nguồn `SYSTEM` không chạy lại prepare, tách rõ quyền "xuất hiện trong kho/chọn mới" (`published=true`) và "được đọc trong phiên/snapshot đã tạo" ở cả RLS và mọi đường `backend()`. Đưa tiến trình upload lên cấp `App.tsx` kèm chỉ báo nhỏ luôn hiển thị và `GET /api/v2/uploads` khôi phục theo dõi sau reload. Migration `024_pilot_sample_catalog.sql`; 50 PostgreSQL test + 53 Vitest + Ruff/tsc/pilot build đạt.
- [x] **Lát 3 (gộp Lát 3 & 5, bỏ Lát 4) — Admin quản lý tài nguyên mẫu, theo dõi/log & Seed dữ liệu mẫu thật (CONTRACTS §21.36)** Upload/prepare nguồn `SYSTEM` xuyên suốt (`target_scope='SYSTEM'`, `account_scope='GLOBAL_ONLY'`, `owner_id=NULL` trên nguồn/frame_set/artifact `SYSTEM`, giữ `owner_id` admin trên upload/job), API danh mục admin có phân trang (`GET/PATCH /api/v2/admin/sources`, `/api/v2/admin/models`), sửa tên/mô tả, bật/tắt xuất bản có kiểm tra readiness, đồng bộ `rav_pilot_demo_template_status` và chặn `fork` khi nguồn ẩn. Trang `#operations` 4 khu vực (**Tổng quan / Dữ liệu mẫu / Tác vụ / Nhật ký**), `job_counts` từ DB, định nghĩa rõ `queue_wait_ms`/`run_ms`, lọc log theo `upload_id`/`job_id`/`request_id`, và script `scripts/pilot_seed_sample_catalog.py` nạp video mẫu thật (`FRAMES`/`DINO`/`CLIP`, `published=true`). Migration `025_pilot_system_ingest.sql`. (Admin tạo phiên mẫu `CONFIGURED` chuyển sang **Hướng tới**). 48 PostgreSQL test + 54 Vitest + Ruff/tsc/pilot build đạt.

- [x] **Tải cao và deploy nhật ký admin (2026-10-17)** Người dùng yêu cầu load test, kiểm giá trị log thực rồi deploy nếu đạt. Test API/DB/log fault cô lập trên VPS, giới hạn tài nguyên và dừng khi production bận/thiếu RAM; Gate đạt: 140k log/8 client/CPU96%, 2479 request/60s không lỗi, p95254ms. Sửa span PII bị drop và cache flag, worker + FE/API sourcec7a9b3df đã live; schema/policy giữ nguyên. Cold/cache live trace đạt; [QA](docs/setup/ADMIN_LOGS_RELEASE_QA.md).

- [x] **Nhật ký admin từ review PR Nam (2026-10-17)** Theo người dùng: review PR #2, tích hợp cách xem log vào Vận hành theo §21.19. Tái dùng collector và telemetry đã sanitize; thêm đọc log theo cửa sổ thời gian/phân trang, filters source/lỗi và liên kết trace. Không merge sandbox/infra anonymous, không thêm query vào inference, 27 PostgreSQL, 51 frontend, Ruff/tsc/pilot build và browser fixture đạt. [Review/QA](docs/setup/ADMIN_LOGS_QA.md). Không deploy trong lát này.

- [x] **Deploy bản gộp admin model + PII (2026-10-17)** Người dùng yêu cầu deploy: source 49a3ba8b, migration 021–022, policy mới chỉ thêm MODEL_VERIFY và gán lane YOLO; backup/idle-check, worker + FE/API đã live. Model verify/dedup/403 đạt; 2 export QA READY, 3/3 frame đúng nguồn, cache lần sau 3/3 hit, ZIP checksum/history đạt. Giữ accounting off, upload/auth/PII confirmation và training gates. [Release QA](docs/setup/ADMIN_PII_RELEASE_QA.md). Không push Git.

- [x] **Gộp admin model và PII (2026-10-17)** Theo người dùng: giữ hai tính năng, migration 021 model verify và 022 PII cache. 110 backend pass/1 capacity skip, 50 frontend, Ruff/tsc/pilot build đạt; PostgreSQL cô lập và checkpoint thật. [QA](docs/setup/ADMIN_PII_MERGE_QA.md). Không push/deploy.

- [x] **PII resize + cache bền và gói dataset (2026-10-16)** Người dùng duyệt inference ảnh thu nhỏ tối đa 640,
  scale box về ảnh gốc để che; cache kết quả theo owner/input/model/config và lưu S3 qua artifact/receipt hiện có.
  Giữ confirmation gate trước ZIP, làm rõ trạng thái lưu preview/gói dataset và khôi phục lịch sử.
  56 regression PG/S3, 50 frontend, Ruff/tsc/pilot build và browser fixture đạt; VPS 3 frame inference 0.22–0.38s.
  [QA](docs/setup/PII_RESIZE_CACHE_QA.md). Cần migration022 trước release FE/API/worker, chưa push/deploy.

- [x] **Rà soát casing media variant (2026-10-16)** Theo người dùng: kiểm luồng ghi/đọc ảnh, clip,
  interactive, export và S3; giữ API chữ thường/DB chữ hoa. Chuẩn hóa tại helper dùng chung, từ chối variant lạ,
  95 regression PostgreSQL và lượt cuối 36 test ranh giới/Ruff đạt; [QA](docs/setup/MEDIA_VARIANT_AUDIT_QA.md).
  Không đổi dữ liệu, không push/deploy.

- [x] **Snapshot/export đúng frame và khôi phục lịch sử (2026-10-16)** Sửa lookup FRAME đúng raw/spec/encoding/owner;
  fallback raw kiểm SHA và exact anchor, tuyệt đối không dùng ordinal hay ảnh giả. Đồng bộ snapshot mới vào dropdown,
  đọc lịch sử export phân trang theo phiên, phục hồi review/progress sau reload; giữ confirmation gate và snapshot bất biến.
  27 backend/PostgreSQL, 50 frontend, Ruff/tsc/build và browser fixture đạt; đo 3 FRAME thật tại VPS PII 1.0–1.4s/ảnh.
  [QA](docs/setup/SNAPSHOT_EXPORT_IDENTITY_QA.md). Candidate chưa deploy, không push Git.

- [x] **Admin upload model #142 (2026-10-16)** Theo người dùng duyệt (CONTRACTS §21.33): admin (`RAV_PILOT_OPERATOR_IDS`) upload `.pt`
  qua presigned POST → job `MODEL_VERIFY` trên worker (SHA, nạp/chạy thử, canonical theo SHA) → model SYSTEM INFERENCE_VERIFIED;
  mọi người dùng xem tab Mô hình tham chiếu và chọn model trong Studio. Migration 021, runbook §15. Chưa deploy.
  Test: full PostgreSQL 661 pass/24 skip, 1 fail chập chờn `test_system_import_crash_recovery...` (riêng 3/3 pass, không liên quan);
  4 test #142 gồm checkpoint YOLO11n thật; Vitest 49, tsc, Ruff đạt. Sửa test claim RPC áp lại 021 sau khi áp lại 012. Không push.
  Bước tiếp theo đã duyệt: phiên mẫu gắn model (nới importer demo); dataset mẫu để sau.

- [x] **Response proposal theo batch (2026-10-16)** Theo người dùng: điểm/evidence public chỉ theo picks;
  checkpoint không gửi lại scores mọi ứng viên; read/reload/retry cùng giới hạn. Snapshot backend giữ nguyên.
  Test100k projection, 63 focused/16 compatibility/50 frontend đạt; full656 pass/24skip +1 fixture quota đã sửa/rerun.
  FE/API9c10294a live, worker giữ nguyên; QA16 scores, checkpoint không scores, snapshotDB240rows giữ nguyên.
  [QA](docs/setup/BATCH_RESPONSE_QA.md). Không push Git.

- [x] **Viewer ảnh trước clip (2026-10-16)** Theo người dùng: mở chi tiết/mẫu đã lưu bằng ảnh phóng to;
  chỉ tải/phát clip khi bấm Play, giữ ảnh trong lúc tải hoặc lỗi, không thay decisions/media ownership.
  Browser local initial/pending/error/play/retry đạt; 49 frontend tests, tsc/build/Ruff đạt.
  FE/API source aacfb770 đã live, worker giữ nguyên; [QA](docs/setup/FRAME_VIEWER_QA.md). Không push Git.
- [x] **Hotfix chuyển strategy #141 (2026-10-16)** Tái hiện model_id còn sót khi đổi từ model strategy
  sang Diversity qua Interactive; request chỉ gửi tài nguyên strategy cần và đúng DTO endpoint.
  Giữ snapshot/decisions; 49 frontend tests, tsc/build/Ruff đạt. FE/API source56ca91ff live, worker giữ nguyên;
  QA API chuyển perception → Diversity/Uniform/Random trả batch, lịch sử và Giữ/Bỏ không đổi.

- [x] **Deploy Strategy #141 (2026-10-16)** Theo yêu cầu: build source ac914d59 cho FE/API và worker;
  giữ model PII DEP6, env/schema/policy/upload; backup, idle-check, candidate smoke, rollout và QA tài khoản riêng.
  FE/API và worker live, health/PII/legacy feedback/upload đạt; native cloud còn thiếu CLIP nguồn QA.
  [Release QA](docs/setup/STRATEGY_RELEASE_QA.md). Không push Git.

- [x] **Review #141 sau Claude (2026-10-16)** Sửa native pilot config=null, boundary custom Strategy,
  identity FieldKey và registry khởi tạo đồng thời; test tái hiện trước sửa. 31 PG/54 core/48 frontend,
  tsc/build/Ruff đạt; full 650 pass/26 skip + 1 fixture môi trường đã sửa và rerun cuối 23 pass.
  Chrome bản sao QA: Giữ/Bỏ, 48 ứng viên → 16 picks model thật, snapshot/explain; chi tiết giới hạn trong QA.
  Giữ trạng thái live, không push/deploy.

- [x] **DEP6 — model che PII thật + fail-closed (2026-10-16)** Theo người dùng: YuNet + YOLOv9-t plate ghim SHA,
  inference biển số theo tile, thiếu/sai model → export FAILED `pii_model_unavailable` thay vì xuất ảnh gốc.
  Unit + PostgreSQL/moto pass, kiểm bằng mắt 32 frame thật (sót biển xa, false positive mặt ở ngưỡng 0.3).
  Worker Contabo đã deploy 2026-10-16 (release/dep6, image sha256:218c7a30…, model ghim SHA, 0 export trước đó);
  FE/API Render chưa đổi (push registry chờ người dùng). [QA](docs/setup/DEP6_PII_MODELS_QA.md). Không push Git.

- [x] **Strategy orchestration #141 (2026-10-16)** Theo thiết kế người dùng duyệt: registry/interface strategy độc lập,
  adapter recipe cũ, dispatch/capabilities pilot, strategy CLIP → DINO → uncertainty, sidebar public params.
  Docs trước code; giữ auth/state/cache/jobs/phiên cũ và training gates; test + browser trước commit.
  Không thêm MC dropout/training/visual pipeline builder; chưa benchmark chất lượng không claim uplift.
  Sửa mất scores/trace ở local DB và progress worker làm fail job; 643 backend (PostgreSQL thật) + 48 frontend pass.
  Browser acceptance local đạt 2026-10-16 sau sửa thiếu ứng viên (relax) và explain 422. [QA](docs/setup/STRATEGY_ORCHESTRATION_QA.md). Chưa deploy.

- [x] **Perception release QA/fix #140 (2026-10-15)** Tích hợp bản VPS đang live; sửa claim job nền khi TEXT là HTTP,
  checkpoint trước khi đổi sang recipe nền, kiểm model detect và cache/progress. Test + smoke video thật trước deploy,
  giữ upload VPS/schema/policy hiện tại. 51 core/worker, 28 PostgreSQL (+2 skip), 7 Interactive và 38 frontend pass;
  source 86c10ab7, Render dep-db8e6kdg1s2s739vu5n0, worker/API healthy, slots FREE.
  QA dashcam60s/32 ứng viên: 70.516s fresh inference cache, 28.532s warm; feedback và switch 6→7→8 pass.
  [Bằng chứng/giới hạn](docs/setup/PERCEPTION_INSTABILITY_RELEASE_QA.md). Không push Git.

- [x] **Perception-Instability #140** Tuyển chọn frame từ video theo độ thiếu ổn định của model detection (CONTRACTS §21.31).
  Recipe `perception_instability` độc lập không yêu cầu dataset train tham chiếu hay provenance;
  Người dùng chọn video READY và model detection INFERENCE_VERIFIED;
  Job nền PROPOSE (queue CPU) chọn ứng viên có trần tài nguyên (32–48 frames cho batch 16, điều chỉnh theo batch_size tối đa 96);
  Chạy TTA horizontal flip, đổi tọa độ box lật về hệ gốc, tính IoU 1-1, đếm số box khớp, không khớp, đổi class và instability score;
  Khung hình cả hai lượt không có box đánh dấu "Chưa có tín hiệu" (observed=False); ít frame có tín hiệu hơn batch_size thì trả đúng số lượng kèm lý do;
  Cache key đa thành phần (model hash, anchor exact pixel, preprocessing, transform, consistency version);
  Giao diện Studio: Lưới Foveal kèm badge điểm/chưa có tín hiệu, panel chi tiết giải thích số liệu matching thực tế và toggle xem box lật tùy chọn;
  Chỉ bật available=true khi toàn bộ luồng API → Job → Worker → UI hoạt động và qua kiểm thử.

- [x] **DriveLens landing review fixes (2026-10-15)** Theo yêu cầu sửa 5 finding: nội dung có bằng chứng,
  PII showcase ghi rõ mô phỏng/chưa kiểm định đầy đủ, header responsive, filter đồng bộ frame và phím tắt đúng Studio.
  TypeScript, 34 Vitest, Ruff, build pilot và diff check pass; Chrome thật: filter Mưa/Đêm → frame #7,
  chọn #10 rồi đổi Người đi bộ → #6, toggle PII hai chiều pass; 320/768/1280px không tràn ngang,
  HUD mobile không cắt nội dung, console không có error. Chỉ sửa landing công khai; không chạy backend suite,
  không đổi backend/contract export, không deploy/push. Build còn cảnh báo bundle >500 kB.

- [x] **VPS-Upload-Ingest #139 (Lát 2-3 gộp)** VPS upload → ingest S3 → READY (CONTRACTS §21.30).
  Gộp tải lên trực tiếp đĩa VPS và worker ingest streaming S3 thành một luồng hoàn chỉnh:
  Transport mới `VPS_DISK` cho tệp lớn; client gửi chunk 16 MiB trực tiếp tới VPS daemon qua capability HMAC ngắn hạn;
  Server đọc stream buffer 1 MiB, ghi nối duy nhất vào tệp `.part`, fsync đĩa và ghi metadata `.meta` atomic;
  Offset mismatch 409 từ chối gói lệch; tự động truncate file `.part` sau crash để client resume chính xác từ durable offset;
  Client gọi complete → API enqueue `VALIDATE_UPLOAD` với `staging_source="VPS_DISK"`;
  Worker probe file `.part` trực tiếp, stream canonical lên S3 (S3 multipart chunk 16 MiB), trích xuất frames 4fps, publish READY trong DB transaction;
  Xóa file `.part` và `.meta` ngay sau khi publish READY để giải phóng 100% đĩa; retry an toàn khi gặp lỗi S3;
  Dọn dẹp triệt để khi abort hoặc sweeper staging quá hạn; giữ nguyên transport `POST` và `MULTIPART` S3 để rollback.
  Migration 015–020 đã deploy; policy vps-upload-20261015 bật VPS và storage accounting=false. Giới hạn cấu hình 5 GiB/60 phút; chưa benchmark file 5 GiB. [Release QA](docs/setup/VPS_UPLOAD_RELEASE_QA.md).

- [x] **Storage-Unblocked fix (Lát 1)** Xóa theo accounting receipt gốc; giữ ledger khi đổi policy/mixed data/dedup/legacy; không khóa GLOBAL khi byte đã tính bằng 0. Storage §21.29, ETA §21.28; 98 regression pass (95 + 3 test bổ sung), Ruff pass. Không đổi schema, chưa triển khai Lát 2/deploy/push. [QA](docs/setup/STORAGE_UNBLOCKED_QA.md).

- [x] **Storage-Unblocked #138 (Lát 1)** Bỏ chặn storage quota và cleanup độc lập với accounting (CONTRACTS §21.29).
  Cờ policy bất biến `storage_accounting_enabled: boolean` (default true); khi false cho phép storage_user_bytes=0, storage_global_bytes=0,
  không chặn 429 storage_exceeded, byte mới không cập nhật rav_pilot_storage_account; xóa byte cũ theo receipt gốc;
  giữ rav_pilot_storage_hold để tracking và cleanup; freeze snapshot trên rav_pilot_job và rav_pilot_storage_hold qua migration 019;
  pilot_delete bỏ đối soát tài khoản cho byte chưa từng tính quota; byte cũ vẫn theo receipt gốc.
  Migration 019_pilot_storage_optional.sql; 5 unit + 26 regression tests pass; [QA](docs/setup/STORAGE_UNBLOCKED_QA.md).

- [x] **Dataset-Model-Binding #137 (N3)** Kho Dataset & Model tham chiếu và binding phiên pilot (CONTRACTS §21.6, §21.7, §21.16, §21.17).
  Catalog dataset tham chiếu & versions bất biến; tra cứu model asset kèm quan hệ provenance (khai báo TRAIN/FINETUNE trên dataset version);
  tra cứu và cập nhật reference binding cho phiên duyệt qua CAS (`expected_state_version`), kiểm tra ràng buộc model-dataset,
  giải phóng và cấp phát source pins an toàn. Endpoints: `GET /api/v2/datasets`, `GET /api/v2/datasets/{id}`, `GET /api/v2/models/{id}`,
  `GET /api/v2/sessions/{id}/reference`, `PATCH /api/v2/sessions/{id}/reference`.
  Migration 018_pilot_session_reference_grant.sql; 4/4 integration tests pass; full 583 backend + 34 Vitest pass; [QA](docs/setup/DATASET_MODEL_BINDING_QA.md).

- [x] **PII-Export #136** Pipeline xuất dữ liệu đã che PII & Snapshot Export (CONTRACTS §21.8).
  Xuất phát từ snapshot bất biến; vòng đời REQUESTED → PROCESSING → NEEDS_CONFIRMATION → FINALIZING → READY;
  giải mã frame theo snapshot, chạy PII detection (YuNet + ONNX) che mặt và biển số xe (pixelate16 + GaussianBlur);
  sinh report tự chứa (HTML offline + JSON), tính payload_hash canonical;
  Owner Confirmation Gate (kiểm tra preview + confirm payload_hash);
  đóng gói file ZIP lên S3, cấp link tải có hạn; migration 017_pilot_export.sql.
  Unit + integration pipeline pass (7/7 tests); full frontend 34 Vitest pass, tsc pass; [QA](docs/setup/PII_EXPORT_QA.md).

- [x] **Progress-ETA #135 (N2)** Telemetry tiến độ chi tiết và ước lượng thời gian còn lại (ETA) cho tác vụ worker;
  chuẩn hóa pha DOWNLOADING → PROBING → SAVING_CANONICAL → EXTRACTING_FRAMES → FINALIZING;
  tính fps tức thời và ETA thực tế theo số đo trong pha trích xuất frame; mở rộng API upload/job và UI;
  migration additive 016; tuân thủ CPU-free theo §21.26 và CONTRACTS §21.28 trước code.
  572 backend passed, 26 skipped, 0 failed; frontend 34 Vitest passed; full build pass. [QA](docs/setup/PROGRESS_ETA_QA.md).

- [x] **Large-Upload #134 (Candidate)** User duyệt multipart/retry/resume và giới hạn file/tiền xử lý/phiên đồng bộ.
  Đo video thật 536MiB/24phút60fps pass local, peak RSS BULK worker ~140.1 MiB (peak stack test pytest/uvicorn/Moto ~2.26 GiB), S3 moto;
  full backend regression 566 passed, 26 skipped, 0 failed; frontend 34 Vitest pass.
  Docs §21.27 trước code; migration additive 015, commit e9ca80da. **Lưu ý**: Đây là candidate local đạt;
  chưa rollout DB live, chưa deploy Render/Contabo; còn gate browser interrupt/resume và test với S3 cloud trước release live.

- [x] **Interactive-CPU #133** Render→Contabo HTTPS, RAM context/features và model text resident;
  giữ/bỏ local, Đề xuất tiếp tự lưu batch trước bằng checkpoint RPC/CAS, không nút Save/PROPOSE queue/poll/CPU quota.
  552 backend ở checkpoint trước auto-save cuối; focused cuối 47 pass/2 skip, Chrome thật và frontend34 pass;
  Contabo7 pass, TEXT p95 235ms cô lập. Live policy interactive-20261014/migration014, worker healthy4CPU/6GiB;
  cloud operator upload/ảnh/clip/xóa và 5 lượt save+random-propose 1.08–2.69s đạt. Browser cloud còn gate riêng.
  [QA/giới hạn](docs/setup/INTERACTIVE_CPU_QA.md). Commit local; không push Git.

- [x] **N1-Claim-Lanes #132** Theo yêu cầu: gom CPU/MEDIA claim + identity + policy snapshot,
  giữ proof handler stopped/recovery; test local/fault và capacity mixed-load Contabo cô lập.
  Sau gate: backup/drain, apply additive RPC migrations, provision policy/slots tương thích, bật TEXT/YOLO/BULK live;
  Local 546 pass, Linux 84 pass, 3 mixed-load rounds đạt; live lanes healthy 4 CPU/6 GiB.
  [QA và giới hạn](docs/setup/CLAIM_LANES_QA.md): cloud functional smoke còn mở khi chuyển #133; không push Git.

- [x] **N1-Publish-RPC #131** Gom publish/tất toán PROPOSE thành hàm DB cho đường chuẩn (hold đủ,
  không storage/GPU hold/object pending); replay/fence/cancel/context và accounting giữ nguyên.
  Ngoại lệ dùng đường đầy đủ hiện có, không cắt recovery. Migration additive, capability fallback cho DB chưa migrate;
  540 backend pass/5 skip/hook loại riêng; 18 mô phỏng latency pass. Chuẩn 22 sync → 1, chờ giả lập 260 ms
  publish 7.36 → 0.819 s; VPS SELECT 1 và model cold/warm đo riêng. [QA](docs/setup/PROPOSE_PUBLISH_RPC_QA.md).
  Không deploy/migrate cloud/push; deficit/holds đặc biệt còn legacy, chưa claim toàn batch ≤4 s.

- [x] **N1-Rollout #130** Deploy source bbd7d7a theo yêu cầu; combined Render + worker cùng source,
  backup/idle/drain/rollback copies, health/ready và cloud upload/batch ảnh/clip/owner/delete smoke đạt.
  CLIP cache sửa quyền/provision checkpoint, offline text inference trong live container đạt.
  Giữ schema/policy/slots/caps, chưa bật lanes; cloud proposal/browser đầy đủ và capacity còn gate riêng.
  [Release QA](docs/setup/N1_RELEASE_QA.md). Không push Git.

- [x] **N1-Dashcam QA** Tải dashcam comma.ai 60 s, kiểm SHA256; test local upload/ảnh/clip/xóa,
  Chrome draft/batch/retry với DINOv2-S + pHash thật cho 240 unit. Hai E2E đạt; không deploy/push.
  Upload READY 28–32 s còn cần tối ưu; không claim cloud SLA/AI quality. [QA](docs/setup/DASHCAM_LOCAL_QA.md).

- [x] **N1-DB #129** Theo duyệt người dùng: read job/media không khóa profile, poll đúng job;
  draft decisions + submit/enqueue nguyên tử; batch URL ảnh; gom SQL/pipeline publish. Test PostgreSQL/local UI và benchmark
  trước/sau: 526 backend/5 skip (hook loại riêng), 34 frontend, browser thật forced completed-job replay và snapshot draft guard,
  tsc/build modes/Ruff đạt. [QA](docs/setup/PROPOSAL_LOCAL_QA.md). Không deploy/push; P4 cache nâng thêm và acceptance cloud vẫn lát riêng.

- [x] **Docs-Deploy #128** Rà docs/plan/design; archive lịch sử local và plan đã thay thế; cập nhật kiến trúc high-level/detail,
  status live/candidate/backlog, navigation và relative links. Docs-only; không đổi runtime, schema, policy hoặc deploy.

## Thứ tự tiếp theo

- [x] **Hotfix evidence cũ** Chuẩn hóa proposal evidence đúng per-unit khi mở lại batch interactive;
  43 frontend/14 perception tests, Ruff/tsc/build đạt; deploy FE/API `e746d70a`, giữ worker.
  Chrome mở lại phiên bị crash: batch 13 đủ 16 ảnh, giữ 23/100; API QA 7 batch (5 perception, 2 interactive) đạt.

- [ ] **A0 — Baseline/capacity** Trace click → READY/batch, DB round trips, S3/decode, queue, RSS/CPU và tải hỗn hợp 1/3 account;
  kiểm region/pool/cache/cost và peak tài nguyên trước thay deployment profile.
- [ ] **N1/P0–P5 — Đề xuất nhanh** [Plan proposal](docs/backlog/PROPOSAL_LATENCY_PLAN.md): draft decisions,
  submit cả lô + enqueue nguyên tử, poll đúng job, read path/batch media và publish/reconcile ít round trips, cache có giới hạn.
- [ ] **A2 — Rollout compute** Nền tảng #127 đã code/test; còn benchmark mixed-load, chọn numeric budget/policy,
  provision slots/policy version, drain/compatibility, rollout/rollback và cloud smoke. Không tự bật profile ví dụ.
- [ ] **A3 — Artifact/cache** Manifest/grid/features/model version bất biến, cache xuyên prepare/propose/analysis;
  đo eviction/bytes/cache hit và recovery. Không trộn embedding spaces hoặc sửa exact anchors.
- [ ] **A4/N2 — Deployment envelope và ETA** CPU usage telemetry, giữ GPU accounting; thêm estimate dễ hiểu từ số đo,
  source/image/migration/policy compatibility, restore drill và CI build/publish/health gates.
- [ ] **A5/N3–N5 — Tham chiếu/report** Catalog dataset version + model SHA/training provenance + binding phiên,
  bounded recipe, chạy model trên proposal và report có evidence, không suy confidence thành accuracy.
- [ ] **DEP6 — PII/export** Handler/model mặt/biển số, snapshot frozen, report/preview → owner confirm → ZIP;
  fault/recovery/accounting và quality gate độc lập trước mở tính năng.
- [ ] **N6 — Quality/learning loop** Frame đại diện trên grid, sampling version khi cần; export/gán nhãn ngoài hệ thống,
  import dataset/model mới và đánh giá split độc lập. Chưa có training service/auto-retrain.
- [ ] **Pilot acceptance** Browser auth/expiry/A-B, GPU/Kaggle thật, backfill explicit, restore/cost/capacity và toàn luồng;
  checklist tại [execution plan](docs/backlog/DEPLOYMENT_EXECUTION_PLAN.md).

Mỗi lát sửa thiết kế trước code nếu thay contract; test phù hợp → commit tiếng Việt không dấu → tick → dừng.
Push Git và deploy chỉ theo yêu cầu tương ứng của người dùng; roadmap không tự cấp authorization triển khai tất cả.

- [x] **Release kho mau/signup va hotfix callback/quota (2026-10-17)** Review Antigravity; bo Lat4 moi, giu template compatibility. Sua seed embedding that/exact IDs, upload auth generation;86 regression +40 seed/API/frames/features,56 frontend/tsc/build/Ruff. Deploy worker431eb9e8/API94c1adc0, migrations023–026; sample86unit/172anh DINO/CLIP that, QA session/proposal/admin/403/signup dat. Sua Site URL localhost va callback; retire trigger cap policy cu, default signup GPU50phut/4h CPUoff theo user;38 profile/auth +1 trigger/quota test dat. [QA](docs/setup/SAMPLE_CATALOG_RELEASE_QA.md). Khong push Git. Admin quota editor con backlog.
