# Rà soát casing media variant

2026-10-16; CONTRACTS §21.12/§21.20, sau bản sửa export d0bbfa03. Candidate local, không deploy/push.

## Phạm vi / kết quả đọc code

Rà tất cả tham chiếu variant/frame table trong src, scripts, migrations và test; lần theo producer, SQL và consumer.

| Luồng | Quy ước / kết quả |
|---|---|
| MediaRequestIn, FE media refs/batch | Literal `thumbnail/frame/context` chữ thường, đúng contract |
| PilotMedia request clip | `.upper()` trước params/job/DB `CONTEXT`; worker so `CONTEXT`, đúng |
| PilotMedia request_batch ảnh | Trước sửa caller `.upper()` bù cho helper; DB join nhận uppercase, chưa thấy lỗi runtime casing |
| stored_variant | Lỗi tiềm ẩn: `thumbnail` trực tiếp trả FRAME; chuỗi lạ cũng trả FRAME. Đã sửa chuẩn hóa và kiểm giá trị tại helper |
| lookup_in_tx | Helper hiện không có caller production; trước đây chuyển variant trực tiếp vào SQL. Đã dùng stored_variant trước query |
| frame_writer, publish/import | Manifest uppercase; validate_set và CHECK DB từ chối variant sai. Giữ nguyên, không sửa dữ liệu xấu thành hợp lệ |
| Interactive DB → worker → FE | Đọc uppercase từ DB, map `.lower()` trước lookup và trả image variant lowercase; đúng |
| Export | Literal SQL FRAME đã sửa ở d0bbfa03; regression ảnh thực giữ lại. Chưa thấy query active khác dùng literal lowercase sai |
| S3 private/SYSTEM frame keys | `.lower()` là đúng định dạng đường dẫn; không phải giá trị cột DB |
| DINO/CLIP artifacts | Feature Literal + lookup uppercase, là loại artifact khác với frame |

Không khẳng định mọi lỗi ảnh trước đây cùng nguyên nhân casing. Lịch sử file export cho thấy query lowercase ở implementation
export ceb1a861 và đã sửa trong d0bbfa03; audit này không xác nhận thêm một consumer active bị cùng lỗi.
Các vấn đề hiệu năng/cache/ảnh thiếu bộ sẵn không được quy thành casing khi chưa có bằng chứng.

## Thay đổi

stored_variant tự `.upper()` và chỉ nhận hai loại ảnh hợp lệ, giữ logic max_side. Bỏ `.upper()` rời ở caller ảnh,
để một helper chịu trách nhiệm chuyển sang variant DB. lookup trực tiếp dùng cùng chuẩn hóa.
Không thay validation API lowercase, frame encoding/version, S3 keys, schema, quyền hay dữ liệu production.

## Kiểm thử

- 36 test ranh giới đạt: thumbnail nhiều casing và ngưỡng 320/321; frame luôn full; typo/context bị từ chối;
  API không nhận uppercase; manifest worker sai casing vẫn bị từ chối.
- Thêm test PostgreSQL: lookup trực tiếp bằng lowercase/uppercase phải trả cùng id/key/checksum ảnh đã materialized.
- Regression PostgreSQL riêng cho media, frame upload/prepare, export và interactive: **95 passed**, 120.32 s
  (bao gồm 34 case ranh giới trong lần collect đó). Lượt unit cuối **36 passed**, gồm thêm 2 case manifest sai casing.
- Ruff src/tests đạt. Không sửa frontend; không cần build lại cho thay đổi helper backend này.
