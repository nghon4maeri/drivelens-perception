# Dashcam local QA — N1, 2026-10-13

Candidate `8507aff`, Windows local. Không sửa production code, push hoặc deploy.

## Dữ liệu thật

Video [comma.ai compression challenge](https://github.com/commaai/comma_video_compression_challenge),
[file gốc](https://media.githubusercontent.com/media/commaai/comma_video_compression_challenge/master/videos/0.mkv).
Repository công bố MIT; không suy giấy phép toolkit thành giấy phép của mọi dataset khác.
LFS SHA256 `2611f5f3e186f3529777749f97bd4cce3a208d6b3559e137bd45d256980d2fa9` khớp file tải,
37,545,489 bytes. Remux MKV → MP4 bằng FFmpeg `-c copy -an`: không resize/re-encode hình ảnh.
MP4 37,541,050 bytes, HEVC 60 s, 1164×874, 20 fps. Dữ liệu ở `data/dashcam-qa/`, không đưa vào Git.

BDD100K là lựa chọn sau cho nhiều điều kiện và nhãn chuẩn. Trang Berkeley/homepage không truy cập được
bằng công cụ web trong phiên này; chưa tải hoặc test BDD100K. Không kết luận dataset đã đóng hay bắt đăng nhập.

## Test và kết quả

PostgreSQL thật port 54329, API HTTP/JWT test, worker spawn CPU/MEDIA thật, S3 Moto trên loopback.
Không dùng Supabase/AWS/Contabo production. Hai test chạy nối tiếp; một video, không phải capacity test.

| Chặng | Thời gian |
|---|---:|
| Upload → READY, gồm materialize ảnh | 31.68 s; lượt browser riêng 27.96 s |
| Thumbnail đầu tiên | 107.6 ms |
| Lưới 48 ảnh, endpoint từng ảnh/4 đồng thời | cold 1.381 s; warm 1.521 s |
| Frame đủ phân giải | 69.0 ms |
| Context clip từ video gốc | cold 4.680 s; warm 95.4 ms |
| DINOv2-S + pHash cho 240 unit thật, CPU 2 threads | 54.25 s |
| Đề xuất đầu tiên 16 frame, đã có embedding | 0.707 s |

Các số đơn lẻ không đại diện p95 nhiều lượt. Grid trên là harness endpoint cũ;
browser candidate xác nhận batch 16 ảnh trong một request, chưa có số latency batch riêng.
Thời gian embedding gồm load model và tính đặc trưng bằng component hiện tại; không gồm upload/READY,
không phải phép đo job PREPARE GPU/Kaggle. Artifact NPZ thật được nạp vào test DB/S3 fixture theo manifest;
worker PROPOSE đọc artifact này và tính diversity thật. Không dùng embedding ngẫu nhiên trong browser test này.

- Frames E2E: **1 passed**, 113.10 s. JPEG khớp decode video, ảnh không tạo job RENDER_MEDIA,
  clip cache, cô lập hai chủ sở hữu, xóa/tính lại dung lượng/receipt/slot đều đạt.
  Recovery kill CPU và SYSTEM importer trong cùng test vẫn dùng video tổng hợp riêng.
- Chrome E2E: **1 passed**, 40.59 s. Upload thật trước mở session; hai job PROPOSE SUCCEEDED.
  Draft/undo/reload, snapshot guard và tab navigation đạt. Mất response sau commit và job đã SUCCEEDED,
  retry cùng key không nhân đôi job/16 decision. Hai POST (gốc + retry), mỗi POST 16 changes;
  không POST decisions riêng, một targeted job GET, không poll account jobs/full history,
  một batch media 16, không page error.
- Không có ground truth hoặc đánh giá thủ công ranking: chưa đo chất lượng active learning/mAP.
  Chưa test YOLO, CLIP text-match, PII, multi-account load hay network cloud ở lượt này.

## Tái chạy và evidence

Source `. ./scripts/use_project_storage.ps1` trước Python. Dùng PostgreSQL test/runbook,
`RAV_PILOT_TEST_DATABASE_URL`, dependency Moto trong `output/compute-runtime/testdeps`.

```powershell
$env:RAV_BENCH_VIDEO='data/dashcam-qa/comma-0.mp4'
$env:RAV_FRAMES_E2E_RUNS='1'
python -m pytest tests/test_pilot_frames_e2e.py -q --basetemp=output/dashcam-qa/pytest-frames
python output/dashcam-qa/compute_features.py
python -m pytest output/dashcam-qa/test_browser.py -p tests.conftest -q --basetemp=output/dashcam-qa/pytest-browser
```

Ad-hoc harness giữ ở `output/dashcam-qa/`: `compute_features.py`, `prepare_browser.py`,
`test_browser.py`, `browser.mjs`, `real-dino.json`, logs/exit, `browser-result.json`,
`browser-db.json`, `browser-after.png`, `upload.json`, `first-proposal.json`.
Frames report: `output/frames-bench/e2e-local-20261013-235202.json`.
Local JWT config chỉ là test token, không chứa production credentials. Output/data bị gitignore.

Kết luận: cơ chế cơ bản và proposal candidate qua dữ liệu dashcam thật. Upload/preprocessing
28–32 s cho 1 phút vẫn là ưu tiên đo breakdown/tối ưu; không ngoại suy tuyến tính video dài,
không suy số local thành cloud SLA. Giữ deployment/capacity gates trong PLAN.
