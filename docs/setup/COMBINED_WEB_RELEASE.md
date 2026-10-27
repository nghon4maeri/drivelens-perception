# Release FE/API gộp — Render

Cập nhật #128; [status/digest hiện tại](DEPLOYMENT_STATUS.md), [runbook cũ](../archived/2026-10-13/docs/setup/COMBINED_WEB_RELEASE.md).
Service chính srv-db68u7d9fdbs73f8v890, https://c4-app-026.onrender.com/app. Không thao tác service IrishCoffee suspended.
Docker target combined build React rồi chạy FastAPI nhẹ, cùng origin /api/v2; không worker/model inference trong Render.

## Build và smoke trước publish

Từ repo root, Windows/Docker Linux containers:

```powershell
. .\scripts\use_project_storage.ps1
$releaseTag = git rev-parse --short=12 HEAD
docker build --platform linux/amd64 --target combined -t "tonmeu/rav26:combined-$releaseTag" .
docker run -d --name rav-combined-smoke -p 127.0.0.1:18080:8000 "tonmeu/rav26:combined-$releaseTag"
Invoke-RestMethod http://127.0.0.1:18080/health
Invoke-WebRequest http://127.0.0.1:18080/login
docker stats --no-stream rav-combined-smoke
docker stop rav-combined-smoke
docker rm rav-combined-smoke
```

Test FE/BE phù hợp trước build; image non-root/import nhẹ, assets/runtime-config, API missing không fallback HTML,
traversal/missing files, app redirect/deep link. Không DB env thì readiness 503 là expected trong image smoke,
nhưng release target bắt buộc DB/schema ready. Secret/private key/data/output không trong build context.

## Publish và rollout đã được yêu cầu

1. Ghi source SHA/digest/config names/schema/policy và rollback trước đổi. Registry private, credentials read-only cho Render.
2. Publish tag đã smoke: `docker push tonmeu/rav26:combined-SHA`, lấy registry digest immutable.
3. Đúng service hiện tại; PORT 10000, VITE_API_URL rỗng, RAV_FRONTEND_ORIGIN=https://c4-app-026.onrender.com.
   DATABASE_URL role rav_pilot_api qua Session pooler TLS, Supabase JWT/JWKS, scoped AWS/S3 backend env;
   public FE Supabase URL/publishable key riêng. Không postgres admin hoặc Kaggle token ở API.
4. Kiểm source/image/migration/policy compatibility và drain/admission nếu có schema/payload thay.
   Apply migrations operator sau backup/restore checks; app không tự migrate. Frames cutover theo [frames release](FRAMES_RELEASE.md).
5. Chọn digest trên Render. Update Source/Connect có thể kích hoạt deploy ngay, chỉ thao tác sau gate/env đã đúng.
6. Chờ Live: health/readiness, landing/login/app/assets/runtime config, 401/404 JSON, authenticated owner isolation,
   upload/media/jobs theo phần thay đổi. Browser test riêng với account smoke, không đổi/xóa dữ liệu user sẵn có.
7. Monitor /health và /health/ready đúng hostname; ghi evidence/source/digest/deploy ID/gates thiếu vào status/decision log.

Git push khác registry push/deploy và cần yêu cầu riêng. Push image chưa khiến live tự cập nhật.
render.yaml static fallback không quản lý service gộp. CI publish/deploy health gates còn roadmap, không coi đã tự động.

## Rollback

Giữ previous image/env/schema/policy compatibility và backup, không drop migrations additive hoặc chạy worker cũ với payload mới.
Drain/stop verified rồi rollback phiên bản tương thích theo frames/compute runbook; không chỉ chọn image cũ bất kỳ.
Service FE/API tách cũ chỉ là fallback lịch sử ở [archive](../archived/2026-10-13/docs/setup/DOCKER_IMAGE_RELEASE.md).


## Live #133 interactive (2026-10-14)

[Deployment status](DEPLOYMENT_STATUS.md) và [Interactive QA](INTERACTIVE_CPU_QA.md) là checkpoint hiện tại.
TEXT chuyển thành HTTPS FastAPI khi RAV_INTERACTIVE_ENABLED=1; GPU/BULK/YOLO/MEDIA vẫn durable.
Render cấu hình URL/token/CA pin cùng worker TLS/token. CPU accounting tắt bằng policy mới; không sửa snapshot job cũ.
Drain/backup trước đổi worker/API/policy, giữ rollback cả ba; không chạy legacy PROPOSE queue khi TEXT đã chuyển HTTP.
