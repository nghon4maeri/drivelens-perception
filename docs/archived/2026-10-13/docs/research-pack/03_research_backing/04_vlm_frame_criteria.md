# Research: VLM chấm và lọc frame theo mục tiêu người dùng

> **Trạng thái: nghiên cứu, chưa phải hợp đồng.** Chưa code. Muốn triển khai phải viết vào CONTRACTS (§7, §12.2, §12.4) + UI_DESIGN
> và được duyệt trước (AGENTS luật 1). Ngày 2026-10-12.

## 0. Tóm tắt

- VLM **không thay** thuật toán chọn frame. Thuật toán (DINOv2, pHash, novelty, NMS, YOLO) lọc rẻ trước; VLM chỉ xác nhận những gì
  thuật toán không hiểu được: frame có đúng mục tiêu không, đủ rõ để gán nhãn không, đang ở điều kiện / tình huống gì.
- Dùng VLM **zero-shot**, không train. Thay việc train bằng việc **đo**: so câu trả lời của VLM với khoảng 200 frame người gắn nhãn tay.
- Hai việc dùng chung một bộ tiêu chí:
  (a) **lọc frame theo mục tiêu** người dùng nhập (mục 1–3);
  (b) **rubric chấm từng frame** để làm báo cáo tập dữ liệu (mục 4–5).
- VLM chỉ mô tả từng ảnh. Mọi con số về cả tập do **code** tổng hợp; LLM viết báo cáo chỉ từ các con số đó.

## 1. Tiêu chí lọc theo mục tiêu người dùng nhập

Màn "Phiên mới" hiện chỉ lưu mục tiêu dạng chữ tự do và thuật toán chưa đọc nó (`uses_goal = False`). CONTRACTS §12.2 đã chừa hướng
*"Sau: có thể thêm tiêu chí có cấu trúc"*. Câu mục tiêu được tách thành 6 loại tiêu chí. Mỗi tiêu chí có mức **bắt buộc** (must)
hoặc **ưu tiên** (should). Người dùng xác nhận hoặc sửa bộ tiêu chí trước khi lọc.

| # | Loại | Câu hỏi | Ai kiểm | Căn cứ |
|---|---|---|---|---|
| ① | Đối tượng | Cần cái gì trong ảnh? | YOLO lọc trước, VLM xác nhận | ISO 34503 (dynamic elements) |
| ② | Điều kiện | Khi nào / ở đâu (đêm, mưa, ngược sáng, giao lộ…)? | Lọc thô bằng độ sáng, VLM xét chính | ISO 34503 (environmental, scenery) |
| ③ | Tình huống | Chuyện gì đang xảy ra (cắt ngang, băng qua, áp sát)? | VLM xem 3 frame liền nhau | ISO 21448 SOTIF (triggering conditions) |
| ④ | Gán nhãn được | Đủ rõ không, che khuất bao nhiêu? | OpenCV lọc trước, VLM xét | ISO/IEC 5259 (accuracy) |
| ⑤ | Loại trừ | Không muốn gì (mờ, lóa…)? | VLM + thuật toán | — |
| ⑥ | Đa dạng | Có cần khác nhau không? | Chỉ thuật toán (novelty, pHash, NMS) | ISO/IEC 5259 (diversity) |

### Áp vào 3 mục tiêu mẫu trên UI

| Mục tiêu mẫu | ① Đối tượng | ② Điều kiện | ③ Tình huống | ④ Gán nhãn được | ⑥ Đa dạng |
|---|---|---|---|---|---|
| Người đi bộ ban đêm hoặc thiếu sáng, nhìn đủ rõ | must: pedestrian | must: night **hoặc** low_light | — | must: nhìn rõ | should |
| Xe máy cắt ngang hoặc áp sát, không che khuất nhiều | must: motorcycle | — | must: cut_in **hoặc** close_proximity | must: che ≤ partial | should |
| Khác biệt cảnh/ánh sáng/đối tượng, bỏ mờ/trùng | — | — | — | must: không mờ | **must** (thuật toán) |

Mục tiêu "đa dạng cảnh" gần như không cần VLM (chỉ cần kiểm chất lượng ảnh). VLM có giá trị nhất khi mục tiêu có ①②③ cụ thể.

### Quy tắc xếp hạng

- Tiêu chí must bị VLM trả **no** → loại frame.
- Tiêu chí must bị VLM trả **unsure** → vẫn giữ nhưng xếp sau và gắn dấu, để người quyết định.
- Điểm = tỉ lệ tiêu chí should đạt × điểm thuật toán (novelty).
- Không đủ số frame thì hiện ít hơn và ghi "chỉ tìm được n frame khớp", không ép cho đủ.

## 2. Luồng xử lý

```
[UI] Người dùng nhập mục tiêu
  │
[A] LLM tách mục tiêu → bộ tiêu chí JSON (1 lần khi tạo phiên)
  │   UI hiện chip để người dùng XÁC NHẬN / SỬA: [✓ người đi bộ] [✓ ban đêm | thiếu sáng] [✓ nhìn rõ] [+ thêm]
  │
[B] Tiền xử lý (đã có: DINOv2, pHash, YOLO trên worker; cần thêm: độ nét / độ sáng)
  │
[C] Lọc bằng thuật toán (rẻ)                      vd 1.200 frame → ~150
  │   mask: YOLO có lớp cần tìm, độ sáng hợp điều kiện, không mờ, không trùng frame đã giữ
  │   xếp hạng: novelty (+ text_match CLIP nếu có), greedy NMS lấy ~48
  │
[D] VLM kiểm từng tiêu chí must / should trên ~48 frame      48 → 16
  │
[E] Studio hiện 16 frame + chip đạt / không đạt → người Giữ / Bỏ
```

Bước xác nhận ở [A] cần thiết vì câu tự do dễ hiểu sai: "thiếu sáng" là ban đêm, trong hầm hay trời âm u? Bộ tiêu chí lưu cùng phiên
để truy vết được (ISO/PAS 8800: traceability). Thanh đợt hiện phễu "1.200 → 150 → 48 → 16" để người dùng thấy mỗi bước loại bao nhiêu.

### Bộ tiêu chí lưu cùng phiên (ví dụ)

```json
{
  "goal_text": "Người đi bộ ban đêm hoặc thiếu sáng, nhìn đủ rõ để gán nhãn.",
  "criteria": [
    {"id": "obj",   "type": "object",    "level": "must",   "any_of": ["pedestrian"],
     "prefilter": {"det": "person", "min_score": 0.25}},
    {"id": "cond",  "type": "condition", "level": "must",   "any_of": ["timeofday:night", "lighting:low_light"],
     "prefilter": {"brightness_max": 0.35}},
    {"id": "label", "type": "labelable", "level": "must",   "target": "pedestrian", "max_occlusion": "partial"},
    {"id": "div",   "type": "diversity", "level": "should", "by": "algorithm"}
  ],
  "exclude": ["motion_blur"]
}
```

## 3. Triển khai chi tiết: "lọc rẻ trước, VLM xác nhận sau" — không cần train

### Ví dụ loại ① Đối tượng

1. **Đổi mục tiêu thành tên lớp.** LLM chọn lớp từ danh sách đóng, người dùng xác nhận. Danh sách ánh xạ sang lớp YOLO COCO có sẵn:
   `person`, `bicycle`, `car`, `motorcycle`, `bus`, `truck`, `traffic light`, `stop sign` (YOLO11n trong pilot đã có các lớp này).
2. **YOLO lọc thô.** Giữ frame có lớp cần tìm với ngưỡng confidence thấp (~0,25) để không sót; chấp nhận báo nhầm vì VLM sẽ loại lại.
3. **VLM xác nhận zero-shot.** Gửi ảnh nguyên frame, kèm **ảnh crop phóng to quanh box YOLO** khi đối tượng nhỏ/xa.
   Hỏi **câu đóng**, định nghĩa rõ lớp ngay trong câu hỏi:

   ```
   Ảnh dashcam. Câu hỏi: trong ảnh có NGƯỜI ĐI BỘ (người đang đi/đứng trên đường hoặc vỉa hè,
   không phải người ngồi trên xe) nhìn thấy được không?
   Chỉ mô tả cái nhìn thấy. Không chắc thì trả unsure.
   Trả JSON: {"present": "yes|no|unsure", "count_visible": 0-10, "where": "left|center|right|multiple",
              "evidence": "1 câu mô tả thứ nhìn thấy"}
   ```

   VLM đã hiểu "người đi bộ" sẵn nên không cần train. Phần việc là **định nghĩa lớp trong câu hỏi**, ví dụ phân biệt người đi bộ với
   người ngồi trên xe máy (YOLO coi cả hai là `person`).
4. **Kết hợp kết quả.**

   | YOLO | VLM | Xử lý |
   |---|---|---|
   | có | yes | Đạt, chip ✓ |
   | có | no | Loại (YOLO báo nhầm) |
   | có | unsure | Giữ nhưng xếp sau, chip ? để người quyết |
   | không | — | Không gửi VLM (tiết kiệm chi phí) |

5. **Đo thay cho train.** Khoảng 200 frame gắn nhãn tay "có người đi bộ không" → so với VLM, tính precision/recall và κ.
   Đạt ngưỡng (ví dụ κ ≥ 0,7) thì dùng; không đạt thì sửa câu hỏi/định nghĩa rồi đo lại.

### Khi nào mới phải train

| Trường hợp | Cách xử lý |
|---|---|
| Lớp không có trong COCO (xe ba gác, biển báo Việt Nam, ổ gà…) | YOLO không lọc trước được → VLM xét trực tiếp (nhiều frame hơn, tốn hơn); muốn rẻ thì fine-tune YOLO trên vài trăm ảnh có nhãn |
| VLM sai nhiều ở một lớp (κ thấp) | Thêm 2–3 ảnh ví dụ đúng/sai vào prompt (few-shot, vẫn không train); vẫn kém thì bỏ lớp đó |
| Chạy hàng triệu frame, VLM quá đắt | Dùng kết quả VLM làm nhãn để train model nhỏ (distillation) — giai đoạn sau |

### Các loại còn lại

| Loại | Lọc trước (thuật toán) | VLM xác nhận | Đo bằng |
|---|---|---|---|
| ② Điều kiện | Độ sáng trung bình (OpenCV) cho đêm/thiếu sáng | Chọn trong danh sách đóng: day/night, clear/rainy/foggy… | Nhãn weather/timeofday có sẵn của BDD100K, không cần gắn tay |
| ③ Tình huống | YOLO có lớp liên quan, box lớn dần qua các frame | 3 frame liền nhau (t−0,5 s, t, t+0,5 s): "xe máy có cắt ngang/áp sát không?" | Gắn tay ~200 cụm; loại khó nhất |
| ④ Gán nhãn được | Độ nét (Laplacian variance), độ sáng | "Đủ rõ để vẽ khung không? Bị che: none/partial/heavy" | Gắn tay ~200 frame |
| ⑤ Loại trừ | Độ nét (mờ), độ sáng (cháy sáng) | Câu yes/no cho từng điều cần loại | Gắn tay |
| ⑥ Đa dạng | Novelty, pHash, NMS — đã có | Không dùng VLM | `nd_rate_at_k`, `min_dist` — đã có |

## 4. Rubric VLM chấm từng frame

Chỉ dùng từ vựng đóng; trường nào cũng có `unsure`.

| Trường | Giá trị | Căn cứ |
|---|---|---|
| `topic_match` | yes / partial / no / unsure + lý do | ISO/IEC 5259 (relevance) |
| `labelable` | yes / partial / no / unsure | ISO/IEC 5259 (accuracy) |
| `label_issue` | none, motion_blur, overexposed, underexposed, windshield_occlusion, no_target_object | ISO/IEC 5259, ISO/PAS 8800 |
| `triggers` (chọn nhiều) | night_glare, backlight_sun, rain_on_windshield, fog_low_visibility, small_distant_object, occluded_object, truncated_at_edge, dense_traffic, motorbike_cut_in, unusual_vehicle, none | ISO 21448 SOTIF |
| `odd.weather` | clear, overcast, rainy, foggy, snowy | ISO 34503, BDD100K |
| `odd.timeofday` / `odd.lighting` | day, dawn_dusk, night / normal, glare, backlight, low_light | ISO 34503 |
| `odd.scene` / `odd.road` | city_street, highway, residential, rural, tunnel, parking / straight, intersection, roundabout, construction_zone, bridge | ISO 34503 |
| `odd.dynamic` | mật độ giao thông (low/med/high); người đi đường dễ bị tổn thương có mặt (pedestrian, cyclist, motorcyclist) | ISO 34503 |
| `critical_visual` | yes / no / unsure (người hoặc xe rất gần, có nguy cơ) | SOTIF (hazard) |
| `pii_flag` | true / false (mặt người hoặc biển số đọc được) | Yêu cầu ẩn danh PII |
| `evidence` | 1 câu mô tả đúng thứ nhìn thấy | ISO/PAS 8800 (traceability) |

**VLM không chấm** (để thuật toán làm): trùng lặp, mức mới so với tập đã giữ, độ hiếm, độ phủ toàn tập, độ không chắc của model.

**Quy tắc chung:**
- VLM chỉ mô tả từng ảnh, không đếm trên cả tập, không so sánh giữa các ảnh.
- Không đưa khung YOLO vào prompt khi VLM đóng vai đánh giá độc lập (tránh bị YOLO dẫn dắt, #44).
- Mọi kết luận về toàn tập do code tổng hợp từ JSON của VLM.

Khung prompt rubric (rút gọn):
> Bạn là kỹ sư kiểm tra dữ liệu dashcam. Topic: «{goal}». Danh sách lớp: «{classes}». Với **mỗi ảnh**, chỉ mô tả những gì nhìn thấy
> trong ảnh đó, trả JSON đúng schema. Không đếm trên cả tập, không so sánh giữa các ảnh. Không chắc thì chọn `unsure`.
> `evidence` phải là thứ nhìn thấy được, không suy diễn.

## 5. Báo cáo tập dữ liệu: 3 tầng

Báo cáo phải trả lời 4 câu:

| # | Câu hỏi của kỹ sư | Nguồn trả lời |
|---|---|---|
| Q1 | Tập có đúng topic / mục tiêu không? | VLM: `topic_match` |
| Q2 | Có gán nhãn được không? | VLM: `labelable` + OpenCV (độ nét, độ sáng) |
| Q3 | Phủ đủ, cân bằng, không trùng chưa? Thiếu gì? | Metric hệ thống + phân bố thẻ ODD do VLM gắn |
| Q4 | Nên làm gì tiếp? | LLM suy ra từ Q1–Q3, quy về hành động cụ thể trong hệ thống |

```
Tập đã giữ (K frame)                     Mẫu ngẫu nhiên của pool (~200 frame, làm mốc so sánh)
        │                                        │
 [Tầng 1] VLM chấm TỪNG frame theo rubric  →  JSON / frame
 [Tầng 2] CODE tổng hợp: %, phân bố, so với pool, so với mục tiêu
          + metric có sẵn (nd_rate_at_k, min_dist, coverage, keep_rate, review_time — job METRICS DEP5b2)
 [Tầng 3] LLM viết báo cáo CHỈ từ JSON tầng 2 (+ vài frame ví dụ), chỉ chữ
```

Quy tắc: VLM không đếm/kết luận toàn tập; LLM không tự tính số; mọi con số trong báo cáo trỏ về một khoá trong JSON tầng 2.

### Chỉ số tầng 2

| Nhóm | Chỉ số | Có sẵn? | Mốc so sánh |
|---|---|---|---|
| Đúng topic | % `topic_match` yes/partial | Mới | Mẫu pool, recipe `random` |
| Gán nhãn được | % `labelable` yes; phân bố `label_issue` | Mới | Ngưỡng ban đầu ≥ 80% (chỉnh sau pilot) |
| Trùng lặp | `nd_rate_at_k` | **Có** | `random` / `uniform` |
| Phân tán | `min_dist`, `cluster_coverage` | **Có** | Mẫu pool |
| Rải thời gian | `temporal_coverage` | **Có** | — |
| Độ phủ ODD | Phân bố weather/time/scene/road/dynamic; ô có ở pool mà tập không có | Mới | Tập so với pool (ISO/PAS 8800 representativeness) |
| Điều kiện khó | Số frame theo từng trigger | Mới | Tập có làm giàu điều kiện khó không |
| Phân bố lớp | Từ detection YOLO | **Có** (DEP5b2) | Lớp hiếm (ISO/IEC 5259 balance) |
| Quá trình duyệt | `keep_rate_by_recipe`, `review_time` | **Có** | — |
| Độ tin của VLM | % `unsure`, κ đã đo | Mới | Để LLM biết mức tin phần VLM |

Mỗi chỉ số kèm mẫu số và phạm vi (n frame, tập nào).

### Mẫu báo cáo LLM phải viết

1. Tóm tắt 3 dòng trả lời Q1–Q3.
2. Đúng topic: %, kèm 2–3 frame ví dụ khớp và không khớp (frame id).
3. Gán nhãn được: %, các lỗi chính.
4. Độ phủ và cân bằng: ô ODD thiếu so với pool, điều kiện khó có được làm giàu không, trùng lặp.
5. Khuyến nghị (Q4): mỗi khuyến nghị là một hành động trong hệ thống, ví dụ "đề xuất thêm đợt với mục tiêu *mưa đêm*",
   "bỏ 12 frame `labelable = no`".
6. Giới hạn: phần nào do AI đánh giá (cần kiểm tra), cỡ mẫu, những gì không kết luận được (mAP, mức tiết kiệm).

Không đưa số nào ngoài JSON; không dùng chữ "uplift" hay "tăng mAP"; ghi rõ câu nào đến từ đánh giá của AI.

## 6. Kiểm chứng

| Cần chứng minh | Cách đo |
|---|---|
| LLM tách tiêu chí đúng | ~30 câu mục tiêu mẫu, mỗi câu có bộ tiêu chí đáp án |
| VLM trả lời đúng từng loại tiêu chí | κ riêng từng loại; loại ② dùng nhãn BDD100K, ①③④⑤ gắn tay ~200 frame; loại ③ cần đo kỹ nhất |
| Bước lọc thuật toán không làm mất frame đúng | Recall: đếm frame đúng mục tiêu bị loại ở bước [C] |
| Báo cáo trung thực | Kiểm tự động 100% con số trong báo cáo khớp JSON tầng 2 |
| Báo cáo hữu ích | Người dùng chấm 2–3 phiên thật theo Q1–Q4 (thang 1–5) |

## 7. Ráp vào hệ thống hiện có (pilot v2)

| Thành phần | Hiện trạng | Cần thêm |
|---|---|---|
| YOLO trên worker | **Có** (`YOLO_BATCH`, DEP5b2) — chạy sau khi đề xuất | Cho chạy trên tập ứng viên trước bước VLM |
| Độ nét / độ sáng | Chưa | Feature rẻ (OpenCV) trên worker |
| CLIP text match | **Có** (`text_diversity`) | — |
| Job VLM | Chưa | Kind mới (vd `VLM_CHECK`) cùng khuôn YOLO_BATCH: đóng băng frame + bộ tiêu chí, gọi VLM, lưu JSON từng frame bất biến, tính quota |
| Bộ tiêu chí trong phiên | Chưa (goal chỉ là chữ) | `criteria` trong spec phiên + bước xác nhận chip trên màn Phiên mới |
| Báo cáo tầng 2 | Một phần (job `METRICS`) | Thêm chỉ số từ thẻ VLM, so với mẫu pool |

Chi phí VLM/LLM cần đo thật ở bản thử (số frame gửi VLM × giá model hiện hành); bước [C] lọc càng tốt thì càng ít frame phải gửi VLM.

## 8. Cần chốt trước khi viết hợp đồng

1. Danh sách lớp đối tượng và danh sách trigger (nên khớp lớp YOLO đang có + nhu cầu gán nhãn thật).
2. VLM dùng cho lọc theo mục tiêu trước, hay báo cáo tập dữ liệu trước. Đề xuất: lọc theo mục tiêu trước (người dùng thấy ngay giá trị).
3. Model VLM/LLM và nơi gọi (worker, có quota riêng như GPU).
4. Thay đổi thiết kế: `criteria` vào session spec (CONTRACTS §7, §12.2), bước xác nhận tiêu chí trên màn Phiên mới (UI_DESIGN),
   VLM ở vai Judge chế độ "Gợi ý" (CONTRACTS §12.4) — người vẫn quyết giữ/bỏ.
