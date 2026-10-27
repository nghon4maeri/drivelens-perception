# Release admin model + PII — 2026-10-17

Người dùng yêu cầu deploy bản ghép. Source `49a3ba8b14e5951c3eb41d0865659841a2711f34`; không push Git.

## Live

- Render deploy `dep-db96116i0phs73ceiko0`, combined image `docker.io/tonmeu/rav26@sha256:501d6f41070434568d641c5b82445b4cd2391bb8a46ea66b4bdee86eae2cd9b2`.
- Worker `sha256:724ad541d42b53ff68f031c210ac276259d840d3163043295a27bc9f382f3192`, health healthy; candidate image health/routes/registry và PII pinned weights đạt trước rollout.
- Migration `021_pilot_model_verify.sql`, `022_pii_cache_index.sql` đã áp; index `idx_artifact_pii_cache` tồn tại.
- Policy `admin-pii-20261017-49a3ba8b` kế thừa version trước, chỉ thêm profile MODEL_VERIFY (120000 CPU ms, 0 GPU ms theo runbook và smoke checkpoint thật); lane YOLO thêm MODEL_VERIFY. CPU/storage accounting vẫn false; giới hạn upload và GPU gates giữ nguyên.
- Trước rollout: 0 active jobs, 0 slots bận, 0 upload active. Backup release.env/worker.env/compose trên VPS `output/pre-admin-pii-20261017-49a3ba8b`, snapshot policy/profile mapping và service trước rollout trong `output/admin-pii-release` (gitignore, chứa cấu hình riêng, không commit).

## Smoke thật

- Checkpoint YOLO11n công khai SHA `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1` upload presigned POST qua API → MODEL_VERIFY `59d8946a-c9ee-46de-9977-7d490e6190df` SUCCEEDED. Cùng SHA đã có giữ nguyên model metadata/số lượng catalog. Tài khoản QA non-admin POST upload → 403.
- Phiên QA riêng `bd182532-638b-42fa-a003-e6bee163d732`, snapshot `001b1530-6a5a-43a5-9704-14fb0a3099e0`, 3 frame video QA tại 10/30/50 giây, frame_idx 200/600/1000. Không sửa decisions hay export của người dùng.
- Hai export `34d387a4-c466-4e77-87c1-fdb0e47f0677` và `ff74e543-4169-4949-8ca3-ac8462048770` đều READY sau review + confirmation, ZIP lưu trên S3, tải lại checksum đúng; PNG full-size 1164×874. Đối chiếu preview với đúng FRAME nguồn; cùng unit/output SHA ở lần sau.
- Manifest: pipeline pii-onnx-resize-v2, inference 640×481; lần đầu 0/3 cache hit, lần sau 3/3. Không suy ra recall PII hay latency toàn luồng từ ba mẫu; I/O/DB/ZIP vẫn có chi phí. Review đầu đã dừng do helper QA dùng nhầm endpoint media, sau đó sửa helper và tiếp tục cùng export; không phải lỗi sản phẩm.
- Lịch sử hai export đọc lại qua API sau hoàn tất; artifact ZIP/report/PII có lưu bền. Health /health, /health/ready và /app 200 sau smoke.
- Chrome tải bản mới, thấy lịch sử export, cảnh báo export cũ chưa xác minh nguồn, nút xác nhận export cũ bị disable; kho model có form admin và model hiện có. Upload bằng picker chưa kiểm được vì extension thiếu Allow access to file URLs; không đổi quyền extension, đã kiểm phần upload qua API thay thế.

## Giới hạn / rollback

Chưa benchmark video lớn/nhiều người hoặc đo recall PII. Ảnh nhỏ/xa có thể sót; confirmation vẫn bắt buộc. Các export cũ sai ảnh phải tạo lại từ snapshot, không sửa ngầm.

Rollback: idle/drain, restore worker release.env + worker.env từ backup và image Render cũ theo service-before; restore profile policy_id theo snapshot (không sửa config policy bất biến). Giữ migration additive 021–022 để không mất dữ liệu mới, không drop artifact. Không thực hành rollback trên live trong lượt này.

Evidence logs/manifest/checksum: `output/admin-pii-release` (gitignore). Regression trước release: 110 backend pass/1 opt-in skip, 50 frontend, Ruff/tsc/pilot build; Docker combined build chạy lại frontend checks.
