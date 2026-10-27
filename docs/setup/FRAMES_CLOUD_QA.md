# DEP7-Frames release va smoke cloud — 2026-10-13

Người dùng yêu cầu ghi backlog rồi deploy bản hiện tại để thử luồng dữ liệu (#123). Source image `b195c61`, gồm fix `f0d278e`;
chỉ commit docs local, không push Git/merge main. Backlog: [N1–N6](../backlog/POST_BASIC_FLOW_DEVELOPMENT_PLAN.md).

## Release đã xác nhận

- Render `srv-db68u7d9fdbs73f8v890`, deploy `dep-db6vffom7kps73bh8an0` **Live**; `/app` và `/health/ready` 200.
  Image `docker.io/tonmeu/rav26@sha256:4d2fd220a1cc4557f9ade7ceec63c912c0ca8c7c5fbc910db0b5193c5d8c23a4`.
- Contabo worker **healthy**, pin `rav26-worker@sha256:13dafd8a5d6fadd9ac3b553719d3717cbe7b0dac41ceb016c86e11a35fb963dc`.
  Bootstrap `blocked=[]`; shutdown Linux trên image release: 19 test đạt. Combined image smoke non-root/routes/import nhẹ đạt.
- Backup DB trước migration: `output/frames-release/pilot-before-frames.dump` (618.501 byte, PGDMP).
  Migration additive `010_pilot_frames.sql` đã áp; hai profile gắn policy mới `frames-test-20261013`.
  Batch 64, PUT concurrency 8, frame cap 2 GiB, CPU estimate hold 600.000 ms, deadline 1.800.000 ms;
  không bổ sung bonus hoặc đổi storage/base quota. Cache đĩa worker giới hạn 20 GiB.
- Cửa sổ rollout chặn admission bằng disable policy cũ sau kiểm không có active job; dừng worker, chuyển API, rồi profile và worker.
  Script rollout ban đầu thay nhầm tên biến image, dừng trước sửa file worker; đã sửa bước này, giữ nguyên backup và xác nhận cuối.
  Hostname DB trong `.env` local có tab thay dấu chấm, đã sửa riêng ký tự đó; không đưa secret vào Git.

## Smoke Supabase/S3/worker thật

Qua services operator với `ActorContext` của account test `vuthien3002`, không phải authenticated HTTP/browser E2E.
Render HTTP công khai kiểm 200 readiness/app và 401 khi gọi sources không token. Không thay auth hoặc gửi email đăng nhập.
Một video tổng hợp mới 8 s, 640×360, 10 fps, 169.865 byte; không dùng/xóa dữ liệu có sẵn của người dùng.

| Chặng đo ở máy operator | Giây |
|---|---:|
| create upload → S3 POST → complete → polling READY | 52,719 |
| thumbnail lookup + tải JPEG | 3,719 |
| frame lookup + tải JPEG | 2,266 |
| clip mới: request → polling → tải MP4 | 37,157 |
| clip cache: chỉ lookup/cấp URL | 1,375 |
| yêu cầu xóa → polling SUCCEEDED | 47,734 |

- READY có 32 unit × 2 variant = **64 ảnh**, thumbnail/frame trả ngay READY và `job_id=None`, SHA bytes S3 khớp.
- Clip context vẫn tạo từ raw, lượt tiếp theo dùng cache, không job mới. Media của account khác trả 404.
- Chỉ source smoke `2252800b-f3a6-4f53-8eed-a3926f1f9a26` bị xóa; raw và cả 64 ảnh HEAD absent, source/frame set DELETED.
  Upload, clip, delete đều SUCCEEDED, usage FINAL, cleanup_done=true. CPU/GPU/MEDIA FREE; không có active job.
- Persisted storage trở về baseline 58.844.575 byte GLOBAL/account Duy, account smoke = 0.
  Staging smoke còn reservation **169.865 byte** USER/GLOBAL đúng contract: POST capability 15 phút + grace 60 phút;
  supervisor sweep sau hạn mới xóa staging/nhả hold. Chưa chờ và kiểm sweep sau 75 phút trong lượt này, không coi reservation hợp lệ là leak.

Evidence ignored: `output/frames-release/cloud-smoke.json`, `cloud-smoke.log`, `worker-cloud.log`, `cloud-breakdown.json`,
`release-verified.json`. Không commit URL ký, dump DB hoặc secret. Source smoke còn tombstone audit, không purge DB.

## Breakdown worker và giới hạn

Upload attempt 37,505 s: handler 14,357 s; publish 15,920 s; phần còn lại trong attempt khoảng 7,228 s.
Trong handler, materialize ảnh 8,378 s: decode/encode **0,352 s**, intent/reserve batch **4,106 s**, PUT+HEAD **3,135 s**,
còn lại bookkeeping khoảng 0,785 s. Download/hash 0,686 s, probe/core 0,884 s, canonical PUT+HEAD 0,753 s.
Clip attempt 24,628 s: handler 6,341 s, publish 14,619 s. Delete attempt 38,358 s: handler 13,335 s, publish 13,137 s.
Các span lồng nhau; không cộng attempt/handler với span con. Tổng thời gian operator còn có admission, queue, HTTP/S3 và polling;
không gán toàn bộ chênh lệch cho queue hoặc CPU. Đây là một lượt cloud, không A/B và không đại diện video dài.

**Đủ để người dùng thử bản mới; chưa đủ để gọi các luồng mượt.** Publish/DB vẫn chiếm nhiều thời gian, N1 cần làm trước tính năng mới.
Chưa kiểm click-through đăng nhập trình duyệt trên Render (tool browser lỗi khởi tạo), GPU/Kaggle, đề xuất/YOLO/report hoặc backfill SYSTEM cloud.
Không tick DEP7-Frames/DEP7 tổng. Nguồn cũ chưa có bộ ảnh cần bấm **Chuẩn bị ảnh**; không auto backfill nguồn cũ.

## Rollback và người dùng thử

Giữ backup host `/opt/rav26-worker/output/pre-frames-b195c61-{release.env,worker.env,compose.worker.yml}` (quyền 600).
Combined trước release: `sha256:724119b1b640968030bcac5337743904e2fd06095a039a6a5679b9543214cbd5`;
worker trước release: `sha256:350d40c7d4810a3849e69f136857820e2698aef903215ff27c7fb4a411d9170c`.
Theo [FRAMES_RELEASE](FRAMES_RELEASE.md): không chạy worker cũ xử lý job/nguồn có frame set, không drop migration/bảng ảnh.

Mở https://c4-app-026.onrender.com/app, đăng nhập, thử video ngắn mới → READY → thumbnail/frame/clip → reload → xóa video test.
Với video cũ, bấm Chuẩn bị ảnh rồi kiểm thumbnail tự cập nhật. Ghi upload/job ID cùng thời điểm ở màn Vận hành để đối chiếu tracing.
