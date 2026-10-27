# exporters — xuất frame đã keep

Contract: [CONTRACTS §5 dòng 12](../../../../../design/CONTRACTS.md); command `export` §11.2.

- MVP: CSV (`unit_id, video_id, t`, …) + thư mục ảnh, cho CVAT / Label Studio.
- Ghi ảnh bằng `cv2.imencode` + `Path.write_bytes` (đường dẫn có dấu).

**Thêm implementation:** class `@register("exporter")` trong `src/rav/components/exporters/`.

**Test tối thiểu:** 2 unit kept → CSV 2 dòng + 2 file ảnh.

**Ý tưởng:** COCO/YOLO format, Label Studio task JSON.
