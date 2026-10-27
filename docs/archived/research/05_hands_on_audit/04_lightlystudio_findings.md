# LightlyStudio — findings từ hands-on audit

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/research-pack/05_hands_on_audit/04_lightlystudio_findings.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Status: API_VERIFIED ingest + SOURCE_VERIFIED selection primitives + UI BLOCKED**

## Runtime / source

Audit ghi nhận:
- source repo pin ở commit cụ thể;
- Apache-2.0;
- runtime OSS `lightly-studio 1.1.2`;
- source checkout cần Node >=24, local Node thấp hơn nên audit dùng wheel đã build frontend.

## API đã chạy

Trên rainy-dashcam fixture:
- add video;
- extract/represent frames;
- local dataset được tạo;
- embedding generation được **tắt chủ động** để tránh model-weight download.

## Selection primitives xác nhận từ source

Source có:
- `diverse`;
- `deduplicate`;
- `preselected_tag_name`;
- sequence sampling / mean-pooled sequence embeddings.

Nhưng audit **chưa chạy** diversity/dedup/preselection bằng real embeddings.

## UI

GUI server launch thành công.

Browser interaction bị BLOCKED bởi cùng lỗi automation.

Chưa verify:
- sampling dialog flow;
- review/tagging UX;
- video navigation;
- export journey.

## Kết luận hẹp

LightlyStudio có nhiều primitive phù hợp data curation ở source/API surface, nhưng hands-on evidence hiện yếu hơn FiftyOne.
