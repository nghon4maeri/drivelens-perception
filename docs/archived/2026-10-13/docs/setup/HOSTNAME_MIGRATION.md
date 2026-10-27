# Đổi hostname sang c4-app-026 — #118

Yêu cầu ngày 2026-10-12: URL ứng dụng `https://c4-app-026.onrender.com/app`.

## Kết quả rollout 2026-10-12

- Web service cũ `srv-db559q942hec73fp6790` đã **Suspended** sau khi app mới đạt smoke.
  Dashboard xác minh đổi Name chỉ đổi tên hiển thị, không đổi Render Subdomain.
  Đã khôi phục Name cũ sau phép kiểm này.
- Service mới `srv-db68u7d9fdbs73f8v890`: c4-app-026, Singapore, Free $0/month, project T026-Rav026,
  image `docker.io/tonmeu/rav26@sha256:724119b1b640968030bcac5337743904e2fd06095a039a6a5679b9543214cbd5`,
  registry credential hiện có rav26-dockerhub-readonly. Deploy `dep-db68u7t9fdbs73f8va4g`
  **Live**, 20,4 giây, 14:41 ngày 2026-10-12; cùng image manifest `2fc94a3` của release trước.
- Cấu hình được chuẩn bị từ UI service cũ trong RAM, không ghi secret vào repo/output/log.
  Người dùng đã cho phép chuyển đúng 15 biến hiện có sang service mới trong cùng workspace/project/Production.
- AWS app key rav26-app bị AccessDenied GetBucketCORS; không sửa IAM để mở rộng quyền.
  Operator đã đăng nhập Console AWS; lưu CORS bucket aiia-irish-coffee với origin mới duy nhất.
  Giữ POST/GET/HEAD, Content-Type/Range, expose ETag/Accept-Ranges/Content-Range, max-age 300;
  Block Public Access vẫn On, không sửa IAM/ACL.
- HTTPS `/`, `/login`, `/app`, `/health`, `/health/ready`, `/runtime-config.js` và asset
  `/assets/index-B7kFGhTg.js` đều 200; readiness DB/schema ready. Kiểm lại sau suspend vẫn đạt.
- User đăng nhập origin mới: duyla25clc, hai source và hai session hiện có hiển thị;
  cả hai thumbnail S3 có `complete=true`, `naturalWidth>0`. Không enqueue AI hoặc xóa dữ liệu.
- S3 preflight POST từ origin mới 200 và trả đúng Allow-Origin, kiểm lại sau khi bỏ origin cũ đạt.
  Chưa thực hiện upload file mới trong lát hostname; không coi preflight là smoke upload end-to-end.
- Hai UptimeRobot monitor 804231770 `/health/ready` và 804231926 `/health` đã lưu URL mới;
  giữ keyword `"status":"ok"`, interval 5 phút và notification hiện có. UI hiển thị Up khi lưu,
  nhưng last-check lúc đó thuộc lượt trước đổi URL; không claim đã có lượt check mới từ UptimeRobot.
- Evidence ignored: `output/hostname-release/http-smoke.json`, `hostname-live.png`,
  `old-service-suspended.png`, `cors-before.json`. Không có secret trong evidence.

## Cấu hình cần chuyển

Giữ AWS keys scoped của app, DATABASE_URL role rav_pilot_api, PORT, Supabase/JWKS,
hai public Supabase frontend vars, VITE_API_URL rỗng, bucket/prefix, tracing và allowlist operator.
Chỉ đổi RAV_FRONTEND_ORIGIN thành `https://c4-app-026.onrender.com`.
Không dùng postgres admin, Kaggle token, worker env hoặc đổi policy/quota.

## Gate trước khi ngừng web service cũ

1. Service mới Free có hostname chính xác (không suffix), dùng cùng image/role và config.
2. HTTPS `/`, `/login`, `/app`, deep link, assets/runtime config và `/health/ready` đạt.
3. CORS S3 thay đúng origin ứng dụng cũ bằng origin mới; giữ methods/headers và origin khác.
4. Đăng nhập trên origin mới và smoke đọc tài nguyên/upload; storage trình duyệt origin cũ không tự chuyển.
5. Đổi hai UptimeRobot monitor 804231770 (/health/ready) và 804231926 (/health) sau khi site mới đạt.
6. Suspend (không Delete) web service cũ; giữ image/env để rollback. Worker, DB và S3 không thay.

Lát này chỉ đổi hostname; không triển khai kế hoạch frame #117 hoặc claim cải thiện latency.

## Rollback

Resume service cũ, khôi phục origin cũ từ `cors-before.json`, đổi hai monitor về hostname cũ.
Giữ nguyên DB/S3/worker; không restore dữ liệu hoặc tạo credential mới. Session trình duyệt theo origin.
