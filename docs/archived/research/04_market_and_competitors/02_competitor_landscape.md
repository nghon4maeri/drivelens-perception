# Competitor landscape — các finding đã có

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/research-pack/04_market_and_competitors/02_competitor_landscape.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Status: DESK RESEARCH + AUDIT EVIDENCE**

## Finding quan trọng nhất

Embedding, similarity search, diversity sampling và deduplication **không còn là khoảng trống công nghệ rõ ràng**.

Một sản phẩm chỉ gồm:

```text
embedding → rank diverse frames → gallery
```

sẽ trùng đáng kể với capability đã tồn tại.

## FiftyOne

Đã xác nhận từ docs/source/audit:
- dataset/image/frame data model;
- similarity index;
- `find_unique`;
- nearest similarity query;
- tags;
- export;
- video/frame primitives;
- plugin/Operator extension path.

Trong audit thực tế, một số capability đã chạy được bằng API; xem folder `05_hands_on_audit/`.

## LightlyStudio

Đã xác nhận ở source/runtime:
- video/frame ingest;
- diverse sampling API tồn tại;
- deduplication API tồn tại;
- `preselected_tag_name`;
- sequence-related sampling;
- local OSS runtime.

Selection bằng real embeddings chưa được thực thi trong audit đã có.

## CVAT / Label Studio

Vai trò được giữ trong research:
- annotation downstream;
- không có lý do rõ ràng để RAV-26 xây lại full annotation editor trong MVP frame-curation.

## Encord / Roboflow / Supervisely

Desk research cho thấy các nền tảng commercial/broad-CV cũng có:
- semantic/similarity search;
- data exploration;
- model-assisted/active-learning related workflows;
- curation collections hoặc sampling.

Chúng là bằng chứng market đã có nhiều data-management/curation capability, nhưng chưa có hands-on audit tương đương FiftyOne/LightlyStudio trong project này.

## Điều chưa được phép kết luận

Chưa đủ bằng chứng để nói:
- FiftyOne hay LightlyStudio “không đáp ứng được RAV-26”;
- một trong hai là host cuối cùng;
- UI của một tool tốt hơn tool kia;
- RAV-26 cần standalone platform mới.

Build-vs-buy chưa có final recommendation tại thời điểm desk research. Cập nhật 2026-09-27: nhóm chốt tự làm giao diện
(quyết định #15) vì công sức tích hợp, xem `05_hands_on_audit/06_findings_2026-09-27.md`.
