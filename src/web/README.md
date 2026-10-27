# Frontend RAV-26

React/TypeScript/Vite. Thiết kế theo [UI_DESIGN](../../docs/design/UI_DESIGN.md); API và core behavior theo [architecture](../../docs/architecture_diagram.md) + [CONTRACTS](../../docs/design/CONTRACTS.md).

Chạy/cài từ [README repo](../../README.md). Windows: source scripts/use_project_storage.ps1 từ repo root trong mỗi terminal trước Python/npm; cache/temp/output ở data/ hoặc output/.

```powershell
# Từ repo root, terminal frontend riêng
. .\scripts\use_project_storage.ps1
Set-Location -LiteralPath 'src/web'
npm run dev
```

Backend local chạy src.rav.api:app cổng 8000; Vite dev cổng 3000 proxy /api và /media. Kiểm tra npx tsc --noEmit và npm run build.
api.ts là nơi gọi HTTP và giữ DTO; renderers bám view.type, components dùng ui/ và hooks hiện có.

## Deploy frontend lên Render từ image (#100)

Frontend là Render Web Service phục vụ SPA bằng Nginx từ Docker image build sẵn; API là image riêng,
worker vẫn Contabo. Người dùng chọn Docker Hub private. Xem [Docker image release](../../docs/archived/2026-10-13/docs/setup/DOCKER_IMAGE_RELEASE.md).

Build từ repo root: `docker build --platform linux/amd64 -f src/web/Dockerfile -t rav26-web .`.
Image build chế độ pilot, nhận runtime public `VITE_API_URL`, `VITE_SUPABASE_URL`,
`VITE_SUPABASE_PUBLISHABLE_KEY` (alias `VITE_SUPABASE_ANON_KEY`). Đổi env Render/restart không cần build lại.
Chỉ ba giá trị public được đưa vào `/runtime-config.js` (no-store); không DB/S3/Kaggle/service-role key.
API cấu hình `RAV_FRONTEND_ORIGIN` đúng origin FE. Nginx hỗ trợ SPA deep links, PORT và `/health`.

Local/static build vẫn đọc Vite env; `VITE_RAV_MODE=local|pilot` chọn chế độ khi build.
`render.yaml` Static Site cũ là cấu hình dự phòng, chưa sử dụng trong release bằng image.
CI kiểm vitest/TypeScript/build; image update trên Render cần deploy explicit, chưa có CI publish vì billing org bị chặn.
Danh sách màn/cấu trúc cũ lưu ở [archive](../../docs/archived/frontend/README_local_before_PL6.md).
