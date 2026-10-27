# Large upload #134 — kiểm thử candidate

Ngày 2026-10-14. Contract: [§21.27](../design/CONTRACTS.md). Chưa deploy hoặc provision policy live.

## Sửa môi trường test Windows

E2E lanes trước đây dùng Popen + SIGTERM để dừng supervisor. Trên Windows thao tác này terminate supervisor,
không cho nó shutdown/join các process con. Đã phát hiện và dọn 8 process chỉ thuộc test PostgreSQL local port54339;
các process đó tiếp tục kết nối database fixture đã bị xóa. Không dừng API dev, Vite hoặc worker live.

Harness Windows chuyển supervisor sang multiprocessing spawn với shared stop Event. Supervisor vẫn là process riêng,
giữ phép đo/giới hạn RSS riêng khỏi pytest/API/Moto. Chạy supervisor chung thread với API/Moto không phù hợp với
RSS guard của lanes nên không dùng cách đó. Linux giữ Popen/SIGTERM. Không đổi shutdown hoặc RAM guard production.

## Video 536 MiB chạy đầy đủ

File người dùng: `YTSave_YouTube_Media_HnkhHfwLnmg_Dash-Cam-Owners-Australia-Weekly-Submissions-October-Week-2_002_720p.mp4`.
561733151 byte, 1437.95 giây, 1280×720, 60fps. Không cắt video hoặc thay bằng dữ liệu tổng hợp.

HTTP API thật → multipart 16MiB/4 PUT đồng thời → PostgreSQL thật local → S3 Moto local → supervisor +
TEXT/YOLO/BULK/MEDIA process thật. GPU/Kaggle không tham gia. Policy candidate chỉ áp dụng trong database fixture.

| Số đo | Kết quả |
|---|---:|
| Truyền bytes, bao gồm tạo/init/sign multipart | 5.716 s |
| Từ bắt đầu upload đến READY | 717.315 s (11 phút 57 giây) |
| Frame lấy mẫu / ảnh FRAME+THUMBNAIL | 5752 / 11504 |
| Dung lượng bộ ảnh | 850178567 byte |
| Peak RSS BULK | 146935808 byte (~140.1 MiB) |
| Peak pytest/API/Moto | 2425815040 byte (~2.26 GiB) |
| Lấy URL 3 thumbnail đầu/giữa/cuối | 102.237 ms |
| Open context + proposal random cold 16 frame, 32 URL ảnh | 302.923 ms |

Test kiểm part status sau reconnect, READY, core SHA nguồn, đủ frame/ảnh, media READY không enqueue,
proposal trên pool vượt4000 unit và supervisor exitcode0. Quét process chỉ đọc sau test: không còn worker test.
Số thời gian là local/mock S3, không thay benchmark Contabo/S3 thật; random không là benchmark CLIP/embedding.

Evidence trong `output/large-upload/`: `real-video-fixed2.log`, JSON cùng tên video, `regression-fixed.log`.

- `tests/test_pilot_large_video.py`: **1 passed**, 1 cảnh báo deprecation Starlette, 726.30 s gồm setup/teardown.
- Upload/multipart/receipts regression: **24 passed**, 39.25 s.
- Full backend regression (`python scripts/check_dev.py --scope backend --ignore-known-windows-hook` trên cluster PostgreSQL 16 test riêng cổng 25432, log `output/dev-ci-backend-full.log`): **566 passed, 26 skipped, 0 failed** trong 409.83 s.
- Frontend checks (`python scripts/check_dev.py --scope frontend`): tsc passed, **34 Vitest passed**, 2 lần build (local và pilot mode) passed.
- Ruff và `git diff --check`: pass.

Video241MiB lần trước đã tới READY nhưng suite teardown lỗi; không tính lần đó là test xanh toàn bộ.
Còn gate browser interrupt/resume, chạy candidate Linux/Contabo và kiểm S3 thật trước release; ký layout5GiB
không chứng minh đã truyền file5GiB. Live hiện vẫn giữ release #133 và giới hạn upload cũ.
