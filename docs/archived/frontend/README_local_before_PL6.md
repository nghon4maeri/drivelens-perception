# src/web — frontend React + TypeScript

> **LƯU TRỮ 2026-10-09.** Bản gốc: `src/web/README.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


Vite + React 19 + Tailwind + lucide-react. FE chỉ nói chuyện với BE qua HTTP `/api/v1` và `/media` (architecture §3); BE giữ state,
FE không tự tính. **Thiết kế giao diện (bố cục, màn hình, token, thành phần dùng chung, cách giữ state): [`docs/design/UI_DESIGN.md`](../../design/UI_DESIGN.md)** — muốn đổi giao diện thì sửa file đó trước.

## Chạy

```bash
# terminal 1 — backend (ở gốc repo); đổi thư mục video: RAV_VIDEOS_DIR=...
python -m uvicorn src.rav.api:app

# terminal 2 — frontend
cd src/web
npm install          # lần đầu
npm run dev          # http://127.0.0.1:3000, proxy /api và /media sang http://127.0.0.1:8000
```

Backend ở cổng khác: `RAV_API=http://127.0.0.1:8001 npm run dev`. Kiểm tra: `npm run lint` (tsc), `npm run build` (ra `dist/`, không commit).
Máy ít RAM: chạy backend trong một terminal riêng (ngoài công cụ chạy nền), đóng bớt tab trình duyệt.

## Màn hình (route hash)

| Route | Màn |
|---|---|
| `#videos` (mặc định) | **Kho video**: lưới thẻ, tìm / lọc trạng thái / sắp xếp, Thêm video + kéo thả, chọn nhiều → Tạo phiên mới / Thêm vào phiên…, panel chi tiết |
| `#new` (`&v=id,id`) | **Phiên mới**: chọn video, mục tiêu (mẫu), K (gợi ý % pool), tên; "Tạo & bắt đầu duyệt" tự đề xuất đợt đầu |
| `#exports` | **Dữ liệu đã xuất**: mọi lần xuất, lọc theo phiên, tải `.zip` |
| `#s=<sid>` | Phiên — thanh tiêu đề có "N video · + Thêm"; **Studio duyệt**: lưới 4×4, thanh đợt (hoàn tác, Giữ cả, Bỏ cả, Đợt mới), panel phải |
| `#s=<sid>&t=archive` · `analysis` · `export` | Mẫu đã lưu · Phân tích (3 view, chỉ số, lịch sử đề xuất) · Xuất |

Sidebar trái: điều hướng màn global ở trên, danh sách phiên + "Phiên mới" ở dưới. Phím tắt: phím `?`.

## Cấu trúc

```
src/web/src/
├── main.tsx            gắn App + ErrorBoundary
├── App.tsx             khung: sidebar + route hash, dữ liệu dùng chung (video, phiên; hỏi lại tiến độ tiền xử lý), phím T / ?
├── SessionView.tsx     một phiên: thanh tiêu đề + 4 tab, Studio, phím tắt Studio
├── api.ts              client + type DTO viết tay theo architecture §5.3 — chỗ duy nhất gọi fetch
├── SchemaForm.tsx      form sinh từ JSON schema Params (recipe, tham số view)
├── ui/                 thành phần dùng chung (Button, IconButton, Modal, Panel, ScreenHeader, Badge, ProgressBar, EmptyState,
│                       ErrorBanner, Field, inputClass) + kiểu Status, hàm định dạng
├── hooks/              useSessionData (dữ liệu + Giữ / Bỏ / hoàn tác / đề xuất của một phiên), useHotkeys
├── components/         AppSidebar, VideoLibrary (trạng thái tiền xử lý, tải lên, AddVideosModal), ChessboardGrid +
│                       DashcamThumbnail, CurationSidebar, RecipePanel, VideoContextLightbox, BatchExplain, KeyboardHelpModal
├── screens/            VideosScreen, NewSessionScreen, ExportsScreen (+ ExportList), ArchiveScreen, AnalysisScreen, ExportScreen
├── renderers/          một renderer cho mỗi `view.type`: embedding_map, timeline, duplicate_pairs (index.ts = bảng type → renderer)
└── types/navigation.ts route hash (parseHash, sessionHref), ThemeMode
```

Thêm view mới: BE thêm View component; FE thêm một renderer và một dòng trong `renderers/index.ts`.
Thêm màn / thành phần: theo `UI_DESIGN.md` §4–6 — dùng `ui/`, không chép chuỗi class; dữ liệu qua `api.ts`.
