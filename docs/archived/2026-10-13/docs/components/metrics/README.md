# metrics — tập đã chọn tốt tới đâu

Contract: [CONTRACTS §5 dòng 11](../../../../../design/CONTRACTS.md); quy tắc độc lập + Goodhart §12.5; ngưỡng pHash §9.10; báo tổng kèm `by_video` §13.7.

| MVP | Công thức | Chiều tốt |
|---|---|---|
| `nd_rate_at_k` | Bao nhiêu phần trăm frame đã chọn có "anh em gần trùng" trong chính tập đó (định nghĩa: CONTRACTS §9.10) | thấp = tốt |
| `keep_rate_by_recipe` | Người giữ lại bao nhiêu phần trăm frame mà mỗi recipe đề xuất (định nghĩa: CONTRACTS §10; đọc `ctx.proposals` + `ctx.state`) | cao = tốt |

- Metric **không** dùng field recipe đã dùng (`Proposal.fields_used`); nếu buộc dùng chung → ghi "không độc lập".
  Metric khai báo field mình đọc (`fields`, lấy từ params); P3 kiểm và điền `independent`, metric chỉ tính số (#44).
- Trước benchmark chính thức: spot-check ngưỡng bằng view `duplicate_pairs`, ghi vào `eval/results/report.md` (§9.10).
- Tập rỗng → giá trị định nghĩa được (vd 0.0), không raise.
- **Khoá trong dict** (#42): `<tên metric>` = tổng; `by_video/<video_id>` = góc nhìn theo video; `independent` = 1.0 nếu field
  của metric **không** nằm trong `Proposal.fields_used`, 0.0 nếu có (báo cáo ghi "không độc lập"). P3 điền khoá này (#44).
- `nd_rate_at_k`: `by_video/<id>` = tỉ lệ frame của video đó (trong tập K) bị đánh dấu gần trùng khi xét **cả tập** →
  tổng = trung bình có trọng số của `by_video`.

**Thêm implementation:** class `@register("metric")` trong `src/rav/components/metrics/`.

**Test tối thiểu:** hash dựng tay `[0b0, 0b1, 0xFFFF]` → ND-Rate = 2/3; tập rỗng → 0.0.

**Ý tưởng:** `temporal_spread`, `coverage@K` (cần nhãn), `judge_agreement` (Cohen's κ), `proposals_to_budget`.
