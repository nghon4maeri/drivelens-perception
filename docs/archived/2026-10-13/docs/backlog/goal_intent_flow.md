# Luồng goal → intent có cấu trúc (bản nháp, 2026-10-03)

> **Trạng thái: chốt một phần (2026-10-03, quyết định #63 — xem §6).** Phần còn mở ở §7. Chưa đồng bộ vào CONTRACTS / architecture / UI_DESIGN;
> khi chốt phải sửa CONTRACTS trước (AGENTS.md luật 1) rồi mới code. Chưa có code nào cho luồng này.

## 1. Vấn đề

`goal` hiện là câu tự do (CONTRACTS §12.2): lưu cùng phiên, ghi vào từng `Decision`, hiện trên UI — **thuật toán không đọc**
(không objective nào `uses_goal = True`). Đề xuất chỉ dựa trên novelty, chưa trả lời câu "frame có phù hợp mục tiêu không" (tài liệu focus §3).

## 2. Luồng mong muốn

```
1. Người dùng nhập goal (câu tự do) khi tạo phiên
2. Lưu goal → bắt đầu phiên
3. Mô hình (LLM) đọc câu, suy intent → tổ chức thành structure
4. Mô hình tự kiểm intent: chỗ nào mơ hồ / mâu thuẫn / không khả thi với dữ liệu → alert
5. UI hiện structure; mục bị alert tô đỏ — KHÔNG chặn người dùng (vẫn đề xuất / duyệt được)
6. Người dùng review: sửa trực tiếp structure, hoặc chat chi tiết với mô hình để làm rõ
7. Structure (sau sửa) là thứ thuật toán đề xuất dùng
```

Nguyên tắc:

- Mô hình chỉ **suy và đề xuất** structure + cảnh báo; không tự chấm / chọn frame (vai `ai:<model>` là client của command layer, CONTRACTS §11–12).
- Alert là thông tin, không phải cổng chặn. Người dùng luôn là người quyết.
- Mỗi lần sửa structure (tay hoặc qua chat) được lưu lại để tái lập đề xuất cũ.

## 3. Structure dự kiến (phác, chưa chốt)

```json
{
  "find":       ["người đi bộ ban đêm"],
  "avoid":      ["đường vắng không có người"],
  "conditions": {"timeofday": "night"},
  "classes":    ["pedestrian"],
  "quality":    {"min_sharpness": "…"},
  "alerts":     [{"field": "conditions.timeofday", "message": "hoàng hôn có tính là đêm không?"}]
}
```

Ví dụ loại alert:
- Mơ hồ: "ban đêm" có gồm hoàng hôn? "người đi bộ" có tính người bị che?
- Mâu thuẫn: find và avoid chồng nhau.
- Không khả thi với dữ liệu: điều kiện cần `tag.*` / `det.*` mà pool chưa có field đó (§5).

## 4. Structure được dùng thế nào (hai mặt)

| Mặt | Phần structure | So với | Cần gì |
|---|---|---|---|
| Semantic | `find`, `avoid` | embedding ảnh ↔ embedding câu | feature ảnh–text (CLIP/SigLIP) + objective `text_match` (CONTRACTS §8, "Hướng tới" #38) |
| Symbolic | `conditions`, `classes`, `quality` | field của frame | `tag.*`, `det.*` (CONTRACTS §8.0), feature độ nét |

Các objective / mask này ghép với `novelty` qua combiner, selector giữ nguyên (`greedy_nms`).

## 5. Phụ thuộc và chỗ phải đổi khi chốt

- **Field chưa có:** `tag.*`, `det.*` mới có quy ước (§8.0) và chỗ đọc (`get_evidence`), chưa feature nào tính thật. Nguồn khả dĩ:
  detector (YOLO), metadata dataset (BDD100K có timeofday / weather), CLIP zero-shot.
- **Schema:** `goal` của phiên (CONTRACTS §7, §12.2) và `Decision.goal` (§12.3) đổi từ `str` sang structure (hoặc tham chiếu tới phiên bản structure).
- **UI:** màn tạo phiên / Studio hiện structure, tô đỏ alert, khung chat; `explain_proposal` hiện theo trường structure.
- **Phụ thuộc mới:** API LLM (khoá trong `.env`, chi phí, cần mạng), model ảnh–text → `requirements.txt`.

## 6. Đã chốt (2026-10-03, quyết định #63)

| # | Nội dung | Chốt |
|---|---|---|
| 1 | Câu goal gốc | **Lưu cả câu gốc lẫn structure** (câu gốc để đối chiếu khi LLM hiểu sai) |
| 2 | LLM | Gọi qua **API** (khoá trong `.env`); LLM lỗi / chậm không chặn duyệt |
| 3 | Sửa goal giữa phiên | **Không cho** trong MVP — goal cố định sau khi người dùng xác nhận structure, để cache / thuật toán / tái lập proposal không vỡ. **Cân nhắc nâng cấp** sau (người dùng xem lại): áp cho đợt sau, snapshot ghi phiên bản goal |
| 4 | Phạm vi chat / agent | **Chỉ sửa goal** (structure) trước khi xác nhận; không gọi command khác (propose, lọc lưới, keep/drop) |
| 5 | Mục tiêu + mô tả từng đợt | **Implement luôn** (không để "Hướng tới") |
| 6 | Đo goal có ích không | **Người dùng research thêm** — xem §8 |
| 7 | Nguyên tắc chọn | **Frame đưa lên phải có giá trị (đạt goal), không cần "giá trị nhất" theo heuristic.** Không ép đủ số frame mỗi đợt; thiếu frame đạt thì đợt ít hơn / rỗng (khớp FR-05 tài liệu focus: không ép mẫu kém để đủ K). Hệ quả (đang chờ chốt, §7.5): điều kiện goal là cổng lọc, không phải điểm mềm |

## 7. Còn mở

1. **`tag.*` có cần không?** Gợi ý: không bắt buộc ở MVP — `conditions` (vd ban đêm) chấm bằng mặt semantic (CLIP zero-shot với câu điều kiện);
   `tag.*` chỉ thêm độ chắc / dễ giải thích khi có nguồn thật (metadata BDD100K). Chờ người dùng chốt.
2. **`det.*` theo detector nào:** detector của hệ thống đang được curate dữ liệu (họ YOLO — phiên bản / checkpoint chưa chốt, tài liệu focus §1, §5).
3. **Mục tiêu từng đợt — hình dạng:** bản tối thiểu (mục tiêu đợt = recipe có tên ý nghĩa + mô tả sinh từ số liệu, không đổi `Proposal`)
   hay trường `intent` / `description` riêng trên `Proposal` + router gợi ý. Phải chốt trước khi sửa CONTRACTS.
5. **Hệ quả của nguyên tắc #7:** (a) `find` / `conditions` / `quality` thành cổng lọc (mask) có ngưỡng; điểm chỉ để xếp thứ tự trong các frame đã qua cổng;
   (b) `greedy_nms` bỏ bước nới ngưỡng ×0 (đang lấp đầy đợt bằng frame gần trùng) — đợt được phép ít hơn `batch_size`; (c) UI có trạng thái
   "hết frame đạt goal". Ngưỡng CLIP: so cặp câu (vd "ban đêm" vs "ban ngày", find vs avoid), qua khi câu dương thắng — chi tiết thuật toán, mặc định + ghi docs.
4. Schema structure chốt những trường nào; danh sách `conditions` / `classes` hợp lệ lấy từ đâu (theo field đang có của pool?).

## 8. Đo hiệu quả (người dùng research — chưa chốt)

Câu hỏi: làm sao biết recipe dùng goal tốt hơn `diversity` không dùng goal? Ứng viên đã nêu: keep rate theo đợt / recipe, thời gian duyệt
và chất lượng quyết định (tài liệu focus §4A), uplift downstream (§4B, cần thí nghiệm thật). Chưa claim gì khi chưa chốt cách đo (#61).
