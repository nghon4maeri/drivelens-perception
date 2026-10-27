# Viewer ảnh trước clip — 2026-10-16

Theo yêu cầu người dùng: Mẫu đã lưu/chi tiết frame mở ảnh phóng to trước; Play mới tải/phát clip.
Thiết kế trong UI_DESIGN §3; không đổi API, quyền media, dữ liệu hay quyết định.

- Thumbnail giữ nền trong lúc ảnh full tải; clip chưa có hình/lỗi/buffering không che mất ảnh.
- Clip chỉ mount/request khi bấm Play/Space. Chỉ hiện video sau sự kiện playing; ảnh gốc luôn có thể mở lại và dừng video.
- Lỗi clip hiện thông báo nhỏ và nút thử lại, ảnh vẫn hiển thị. Phím tắt/overlay detection giữ nguyên.
- Sửa thêm state Phát/Dừng sau retry: playing xác nhận phát thật, Promise play bị abort không đè trạng thái video đang phát.

## Kiểm chứng local

49 frontend tests, TypeScript, production build và Ruff đạt trên code cuối. Build có warning bundle lớn hơn500kB sẵn có.
Browser in-app qua công cụ được phép, fixture dùng component thật và video comma.ai thật, server localhost8162:

1. Mở viewer: ảnh thấy ngay, Play enabled, chưa có video/clip request (requests.log chưa tồn tại).
2. Bấm Play: server trì hoãn6 giây; ảnh vẫn nhìn rõ, trạng thái nhỏ Đang tải clip ở đáy; sau tải tự phát.
3. Bấm Ảnh gốc: trở về đúng frame t0, video dừng.
4. Clip404: ảnh vẫn giữ, thông báo lỗi và Thử lại clip; retry thành công, nút Dừng và timestamp tiến triển.

Không dùng production DB hoặc thay decisions người dùng. Fixture/log gitignored ở output/frame-viewer-qa.
Chưa acceptance trong Studio đã đăng nhập live ở mốc local này; release ghi riêng khi có.

## Release FE/API

Source `aacfb7701c5ee617a001517998a029ea54aa3f0a`, Render `dep-db8rg17f3r2c73f5lgh0` live, image `docker.io/tonmeu/rav26@sha256:26f3cb93efcb6d0dc35d36022599d626a9f0a2b6f70d256692ca1a2e572016da`.
Worker giữ nguyên `sha256:3d2ea5e8eff7f52b6048fbe2b1aa202803739bd4f07aa0f7db49b132fde2c1d9`; env/policy/schema không đổi. Candidate routes/health đạt;
Docker build chạy lại tsc/tests/build. Live health/ready/login/app200; bundle `/assets/index-7sOS3uHY.js`
đã xác minh chứa viewer mới. Rollback FE/API: `sha256:986a9c595e897c434645e7b964204d4b59114306e7ee1deb7e1e46ff77c8decb`.
Artifacts protected/gitignored: output/frame-viewer-release. Đã dừng server và đóng tab QA do lượt này tạo.
Acceptance ảnh/play/loading/error/retry là browser local component thật; chưa test lại Studio đã đăng nhập live.
Không push Git.
