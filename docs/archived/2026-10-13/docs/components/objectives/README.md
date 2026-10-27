# objectives — điểm từng unit theo một tiêu chí

Contract: [CONTRACTS §5 dòng 7](../../../../../design/CONTRACTS.md); `Scores` ở §2; quyết định §9.1–9.2/9.5; text matching §15, reference/model-guided scoring §18.

- Objective chấm **từng unit độc lập**; đa dạng trong batch thuộc về selector (§9.2).
- `Pick.breakdown` = điểm **từng objective**, key = `Scores.name` = `type` của objective (§1.6, §7).
- Tham số (kể cả `field`) nằm trong `params` của config: `{"type": "novelty", "strength": 1.0, "params": {"field": "emb.dinov2_s"}}` (§7).

| Hiện có | Ý nghĩa / định nghĩa tại CONTRACTS |
|---|---|
| `novelty` | Frame càng khác tập đã keep càng được điểm cao; vòng đầu (chưa keep gì) thì frame càng khác "frame trung bình" của pool càng cao. Công thức: CONTRACTS §9.5. |
| `text_match` | Query/negative explicit trong config; gate/score và giới hạn tại §15. Không đọc goal phiên hoặc tự suy structure bằng LLM. |
| `reference_novelty` | Mốc train + kept cho recipe V2; định nghĩa và nearest-reference evidence tại §18. |
| `detection_instability` | Proxy original/flip consistency của model phiên; định nghĩa/giới hạn tại §18, không xác suất lỗi hoặc ground truth. |

Mask/threshold và quyền trả batch ít/rỗng theo §15/18; không tự hạ gate để đủ số. Binding feature/model của recipe đóng băng theo §18.

**Thêm implementation:** class `@register("objective")` trong `src/rav/components/objectives/`; field lấy từ `Params` (vd `field="emb.dinov2_s"`).

**Test tối thiểu cho novelty:** giá trị trong [0,1], căn theo `unit_ids`; unit trùng reference → 0; reference rỗng → không lỗi, frame xa tâm điểm cao hơn.
Text/V2 kiểm gate, cache/reference/model requirements, ordered IDs và replay theo §15/18; chất lượng ngữ nghĩa phải đánh giá riêng.

**Ý tưởng:** lọc chất lượng từ field có nguồn đo thật, more-like-this và objective khác theo CONTRACTS §8.
LLM chuyển goal thành structure vẫn Hướng tới (§15), không đồng nghĩa `text_match` hiện có đã hiểu goal tự do.
