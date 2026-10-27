# Deployment status — live và gate

Cập nhật 2026-10-18, logo ly Irish coffee (Render `dep-dba6o2uq1p3s73ahjsc0`). [Release ODD trước đó](ODD_PHASE_CACHE_QA.md). [Mẫu tham chiếu](ADMIN_SAMPLE_REFERENCE_QA.md). [ODD coverage](ODD_COVERAGE_RELEASE_QA.md). [Đăng ký/kho dữ liệu mẫu](SAMPLE_CATALOG_RELEASE_QA.md). [Nhật ký admin](ADMIN_LOGS_RELEASE_QA.md). [Release QA](STRATEGY_RELEASE_QA.md). [QA perception](PERCEPTION_INSTABILITY_RELEASE_QA.md); [upload QA](VPS_UPLOAD_RELEASE_QA.md).

| Thành phần | Live | Giới hạn |
|---|---|---|
| Render FE/API | https://c4-app-026.onrender.com/app, `dep-dba6o2uq1p3s73ahjsc0` (rollback `dep-dba6jvlg1s2s73avq9dg`) | API nhẹ; bytes đi thẳng VPS |
| Combined image | `docker.io/tonmeu/rav26@sha256:43fe2c35e87a4d113130523c7a2c94879a3861281e7094df6aa4c7d6a9df964d` | FE/API source `6117266d`; rollback `sha256:ffc2307e…213dfe` (`de94b45e`) |
| Contabo worker | `sha256:c86c529c8a2d2c9d92bbcb24cbc7bb6448ea2b0974d2663fea63f86d0e8d4cb3` | Source `c6b3ab70` (METRICS + duplicate_pairs theo khối); rollback `1cdc0abc…` (`f49a4905`), `7f3b2bd7…` (`fc53ed8`); healthy, 4 CPU/6 GiB |
| Upload ingress | `https://rav26-upload.161.97.164.25.sslip.io` | Public TLS/CORS, purpose-bound capability, one active VPS upload |
| Supabase | migrations 001–027 theo inventory (không có migration008) | API/worker roles tách;027 bảo toàn receipt INTERACTIVE_SAVE |
| Policy | `admin-pii-20261017-49a3ba8b` | CPU/storage accounting=false; giữ receipts/accounting cũ; GPU policy riêng |
| Upload ceilings | 5 GiB / 60 phút | Đã test thật 35.80 MiB, chưa benchmark 5 GiB |
| S3 | aiia-irish-coffee/us-east-1/pilot-rav26 | Canonical/frames private, verified cloud smoke |

Cloud smoke qua Render đã xác thực: upload/resume/retry/abort → S3 → 240 units/480 images READY;
165.14 s cho toàn luồng, gồm lần gửi chunk trùng có chủ đích. File staging VPS được dọn sau commit.
Không coi health hoặc test nhỏ là bằng chứng full large-video/GPU/PII/semantic readiness.

Strategy #141, admin upload model #142 và PII resize/cache/identity đã live từ sourcec7a9b3df; model PII ready.
ODD coverage native cloud smoke trên sample CLIP/DINO thật đã đạt ở source065ff890; có model tùy chọn,
feedback/reload/empty gate và legacy switch. Tổng thời gian job nhỏ20–32s, chưa coi là tối ưu latency.

Mẫu tham chiếu §21.39 đã live: video6710 frame thêm tham chiếu ẩn trong5.72s, dùng lại raw/13420 ảnh/2 embedding,
không copy S3/job/charge mới; nguồn riêng không đổi. Người dùng chỉ đọc mẫu đã xuất bản, không xóa được;
backing source bị pin. Job copy cũ đã hủy và cleanup2176 object xong; không tự xuất bản mẫu mới.

## Gate còn mở

Perception Instability đã smoke API/worker thật với YOLO11n COCO và dashcam60s; inference cache/feedback/switch pass.
[QA, số đo và giới hạn](PERCEPTION_INSTABILITY_RELEASE_QA.md). Chrome đã xác minh hotfix mở lại batch cũ;
chưa browser acceptance toàn luồng perception hoặc capacity video dài/nhiều người.

Browser file-picker acceptance, 5 GiB/60 phút, tải nhiều người, capacity/latency; GPU/Kaggle thật;
model/reference quality, PII recall/capacity, backup full restore và release CI. Smoke model upload và PII export 3 mẫu đã đạt, xem release hiện tại.
Snapshot live #133 trước đó và các số đo interactive giữ tại [Interactive QA](INTERACTIVE_CPU_QA.md).
Rollback image/env/policy và evidence nằm trong [Upload release QA](VPS_UPLOAD_RELEASE_QA.md).

Viewer ảnh trước clip đã live, giữ ảnh khi clip tải/lỗi; [QA và giới hạn](FRAME_VIEWER_QA.md).

Proposal public chỉ trả scores/evidence theo batch, không bảng điểm cả pool; [QA](BATCH_RESPONSE_QA.md).

Nhật ký admin và worker.export.* đã live, cold/cache/error correlation và tải đọc cô lập đạt; xem [QA tải và release](ADMIN_LOGS_RELEASE_QA.md). Không đồng nghĩa capacity nhiều job AI hay browser private-cloud acceptance.

Đăng ký email/password đã live; Site URL về `/login` production và SDK nhận callback. Email verification vẫn bật. Dữ liệu SYSTEM demo86 units/172 ảnh, DINO/CLIP thật READY; QA user tạo phiên và proposal đạt. Default signup `signup-50m-gpu-cpu-unlimited-20261017`: GPU50 phút/cửa sổ4h UTC, CPU accounting=false. Tài khoản cũ giữ policy riêng; riêng tài khoản mới bị trigger legacy cấp nhầm trong phiên được báo đã sửa sang default mới, giữ usage/holds. Migration026 vô hiệu hóa việc insert profile của trigger legacy; API bootstrap là authority. Admin policy editor chưa có. Source/image/migration hiện tại xem bảng đầu trang.

Tài khoản demo dùng chung (§21.41) live 2026-10-18: `/login` có nút nạp `demo123@aiia.com`; Auth user đã tồn tại từ 2026-10-17
(không ghi đè), `/me` active, không operator/model-write, GPU 3000000 ms/4h, CPU accounting tắt, không bonus. Image smoke local và
production: health/ready/login/app 200, `/me` không token 401, bundle có nút demo và tên DriveLens, OpenAPI title "DriveLens pilot API".
Chưa bấm thử nút bằng trình duyệt thật. Không có migration/policy/worker thay đổi. Rủi ro: người giữ mật khẩu công khai có thể tự đổi
mật khẩu qua Supabase Auth API (app không có UI đổi mật khẩu); biện pháp chặn/reset chưa duyệt.

Landing 2026-10-18 (`fcfb2738`): ảnh chụp Studio thật (làm mờ biển số), 14 frame export đã che PII + manifest, sơ đồ kiến trúc SVG,
strategy/quy trình/phím tắt khớp FE; ghi nguồn video. API chỉ thêm content-type `image/webp` cho `/assets`. Production: health/ready,
`/`, `/login`, `/app` 200; 16 asset landing 200 đúng content-type; `/me` không token 401. Không đổi worker/schema/policy.

Sơ đồ icon 2026-10-18 (`de94b45e`): logo công nghệ (Simple Icons CC0, Lucide ISC) + logo DriveLens; Render `dep-dba6jvlg1s2s73avq9dg`,
smoke `/assets/architecture-*.svg` 200 `image/svg+xml`.

Logo 2026-10-18 (`6117266d`): ly Irish coffee + ống kính (`src/web/src/assets/logo.svg`) cho header/footer landing, favicon, sơ đồ;
Render `dep-dba6o2uq1p3s73ahjsc0`, favicon `/assets/logo-*.svg` 200 `image/svg+xml`, header live đã chụp kiểm.

Worker 2026-10-18 (`f49a4905`): `min_dist`/`nd_rate_at_k` tính theo khối (sự cố METRICS OOM ~4,8 GB trên tài khoản demo).
Build trên VPS `/opt/rav26-test/metrics-block-20261018-f49a4905`, backup env `output/pre-metrics-block-20261018-release.env`;
recreate khi không có job, 5 process sẵn sàng, healthy. Không đổi schema/policy/lane RSS.

Worker 2026-10-18 (`c6b3ab70`): bản `f49a4905` vẫn ~5,2 GB do view `duplicate_pairs` trên cả pool; nay view tính theo khối.
Build `/opt/rav26-test/metrics-block2-20261018-c6b3ab70`, backup env `output/pre-metrics-block2-20261018-release.env`; recreate khi idle, healthy.
