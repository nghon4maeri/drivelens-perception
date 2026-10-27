# DEP6 — Model che PII thật và fail-closed, QA 2026-10-16

Contract: [§21.8 DEP6](../design/CONTRACTS.md). Candidate local; không deploy, không push, không migration.

## Vấn đề audit ở #136 (đang nằm trong source live 86c10ab7)

- Thiếu weights → detector trả 0 box, export vẫn ra ZIP "đã che" chứa **ảnh gốc**.
- Nhánh biển số là `pass`; `onnxruntime` không có trong requirements.
- Worker Docker tìm model ở `runtime/pii/models`, thư mục không được mount/copy → bản live gần như chắc chắn không che.
  Chưa kiểm trực tiếp VPS (cần quyền truy cập live).

## Sửa

- `PIIDetector` nạp YuNet 2023mar + YOLOv9-t-640 plate end2end, ghim SHA-256 (trùng hash plan cũ), thư mục
  `RAV_PII_MODEL_DIR` (mặc định `data/models/pii`, đã mount trong compose worker). Bỏ detector giả.
- Fail-closed: thiếu/sai SHA/nạp lỗi → `pii_model_unavailable`; EXPORT_BUILD dừng trước mọi decode/upload, export `FAILED`.
- Biển số: tile 640 chồng 20%, tile cuối sát mép, pad 114, output end2end cộng offset tile, ngưỡng `plate_threshold`.
- Evidence từng ảnh ghi file + SHA model; report và cổng xác nhận nói rõ che tự động có thể sót/che thừa.
- UI export FAILED hiện lý do (trước chỉ hiện chữ FAILED). Runbook §14 cách cài model; `worker.env.example` thêm biến.

## Test

- Unit: thiếu model và model sai SHA → không ready, `detect` báo `pii_model_unavailable`; tile 1280×720 → 6 tile,
  box map đúng offset, score thấp bị loại; ảnh nhỏ → 1 tile pad; model thật trong `data/` nạp được và ảnh xám không ra box.
- PostgreSQL + moto: lifecycle export dùng detector inject; test mới thiếu model → `FAILED pii_model_unavailable`
  và không có object `/exports/` nào trên S3. Đã kiểm test fail khi bỏ `require_ready`.
- Full backend với PostgreSQL thật: 646 passed, 26 skipped (opt-in/Linux/thiếu `yolo11n.pt`); ruff, tsc, Vitest 48 pass.

## Kiểm chất lượng bằng mắt (frame thật trong `data/`, ảnh QA ở `output/pii-qa/`, không commit)

32 frame từ 4 video (1280×720 – 1728×1080), CPU local 0.4–1.0 s/frame.

- Biển số: bắt được biển xe buýt, xe máy, SUV ở cự ly gần/trung bình; ảnh sau che không đọc được.
  Sót biển xe phía xa/nhỏ (van trắng ở xa, xe phía trước khi biển khuất).
- Mặt: bắt đúng nhiều mặt trong cảnh đông người. **False positive** ở ngưỡng 0.3: ô lớn trên mặt đường ướt (0.54),
  ô nhỏ trên tán cây. Giữ ngưỡng mặc định (ưu tiên che thừa hơn sót); chủ sở hữu xem preview trước khi xác nhận.
- Không có tập nhãn PII → **không claim recall/precision**.

## Kiểm thêm trước deploy (2026-10-16, local)

- **Image Linux worker** (`rav26-worker:contabo-candidate`, container tạm, không build image mới để không thêm ~3 GB trên C:):
  `pip install onnxruntime>=1.20.0` chọn 1.31.0, giữ numpy 1.26.4, `pip check` sạch; `cv2.FaceDetectorYN` có sẵn;
  cwd `/app` → mặc định `/app/data/models/pii` (compose mount `./data`). Model nạp, frame thật ra biển số 0.70;
  thiếu model → `pii_model_unavailable`.
- **Ngân sách BULK lane** (1 thread, `--cpus=1`): 720p 0.68 s/frame, 1080p 0.98 s, 4K 4.1 s; RSS đỉnh ~1 GiB (< 1.5 GiB lane).
  Handler kiểm `stop` giữa các frame. Snapshot 200 frame 1080p ≈ 3.3 phút.
- **Lỗi tìm được và đã sửa:**
  - onnxruntime đặt cứng 2 thread, vượt BULK = 1 → dùng `RAV_MODEL_THREADS` của lane, `cv2.setNumThreads` theo cùng giá trị.
  - OpenCV không đọc model ở đường dẫn có dấu (máy dev) → nạp model từ chính bytes đã kiểm SHA (cũng chặn đổi file sau khi kiểm).
  - `manifest.json` trong ZIP không ghi model đã che → thêm `pii_models` (file + SHA) và `pii_evidence` từng ảnh.
- **Lifecycle với model thật** (PostgreSQL + moto): build → review → confirm → finalize → READY, ZIP có ảnh và SHA hai model.
- Full backend (PostgreSQL): 646 passed, 1 failed `test_pilot_quota::test_base_then_bonus_fifo_and_refund_to_origin`
  (quota window theo `now()`, không đụng trong lát này; chạy lại riêng 3/3 pass) — ghi nhận là test chập chờn, chưa sửa.

## Deploy worker Contabo (2026-10-16, người dùng yêu cầu)

- Phạm vi: nhánh `release/dep6` = source live `8b96acdc` + 2 commit DEP6 (không gồm #141, còn gate browser).
- Preflight: worker healthy image `sha256:5949acfa…`, 0 job active, mọi slot FREE, **chưa có export nào trên live**
  (không có ảnh chưa che nào đã xuất). Policy `vps-upload-20261015`: không có deadline EXPORT_BUILD, CPU accounting tắt.
- Rollback: `/opt/rav26-worker/output/pre-dep6-20261016/{release.env,worker.env,compose.worker.yml}` (root, 600).
- Model: `/opt/rav26-worker/data/models/pii`, SHA-256 khớp, UID 1000, 444.
- Image `rav26-worker:dep6-20261016-9611236d` = `sha256:218c7a3037e5eea44577e620c5deba10edcd2c2d5b151cb7c0532dea189d5129`,
  build trên VPS từ tarball `0830f69b…`; container tạm với data live: detector ready, `pip check` sạch.
- Sau chuyển: healthy, process TEXT/YOLO/BULK/MEDIA/GPU sẵn sàng, detector trong container live `ready True`,
  Interactive `/health` `{"ready":true}`, Render health/ready/app 200. Không đổi worker.env, schema, policy, slots.
- Chưa smoke một export thật trên live (không có tài khoản QA; không tự tạo user).
- FE/API Render **chưa đổi**: image `tonmeu/rav26:combined-dep6-20261016-9611236d` đã build + smoke local
  (health 200, non-root, bundle có thông báo mới, route giống live) nhưng push registry bị chặn quyền trong phiên;
  người dùng tự push + chọn digest. Rollback FE hiện tại: `sha256:651b91c6…`.

## Trạng thái sau release Strategy #141

Worker và FE/API đã lên source chung, giữ model PII ghim SHA; candidate/live detector ready, pip check sạch.
[Release QA](STRATEGY_RELEASE_QA.md) thay trạng thái chờ publish/rebuild ở mốc trên.

## Chưa làm

- Smoke export thật trên live, browser acceptance màn Export và benchmark snapshot lớn.
- Kiểm deadline EXPORT_BUILD theo policy đang áp dụng trước export lớn; không đổi policy trong release #141.
