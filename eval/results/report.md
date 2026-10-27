# Evaluation Report

> Báo cáo đánh giá chất lượng sản phẩm theo tiêu chí BTC.

---

## 1. Metrics

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Response accuracy | >80% | — | ⏳ |
| Response latency | <3s | — | ⏳ |
| User satisfaction | >4/5 | — | ⏳ |
| Test coverage | >60% | — | ⏳ |

## 2. Test Results

### Unit Tests
```
pytest tests/ -v
# Paste output here
```

### Integration Tests
```
# Mô tả test scenarios và kết quả
```

## 3. User Feedback

| User | Feedback | Rating |
|------|----------|--------|
| [User 1] | [feedback] | [1-5] |
| [User 2] | [feedback] | [1-5] |

## 4. Demo Results

- Ngày demo: [YYYY-MM-DD]
- Người tham gia: [số người]
- Feedback chung: [tóm tắt]
- Issues phát hiện: [danh sách]

## 5. Kết quả lịch sử (pipeline cũ, trước khi viết lại structure)

> Chuyển từ `docs/visual_evaluation.md` (2026-09-21). Code tạo ra kết quả này là `src/` cũ, đã thay bằng structure mới.
> **Phạm vi hẹp:** 1 video dashcam, 750 frame (~25 s), K=50, embedding `PE-Core-L-14-336`.
> ND-Rate đo bằng pHash, ngưỡng Hamming ≤ 5 (proxy, độc lập với embedding dùng để chọn).
> Không suy ra mAP hay rare-event coverage từ số này.

| Chiến lược | Near-Duplicate Rate@50 | So với Random |
|---|---:|---:|
| Random (5 seeds: 42, 123, 999, 2026, 8888) | 46.80% ± 5.74% | – |
| Uniform Temporal | 32.00% | −31% |
| Embedding Diversity (greedy max-min) | 24.00% | −48.7% |

Hình minh hoạ: [random](images/grid_random.jpg) · [temporal](images/grid_temporal.jpg) ·
[diversity](images/grid_diversity.jpg) · [PCA](images/pca_analysis.png) · [frame xa tâm nhất](images/rare_cases.jpg).

Cần chạy lại trên BDD100K với nhiều video trước khi dùng làm bằng chứng chính.

## 6. Benchmark L4 — SƠ BỘ (ngưỡng pHash 5 chưa spot-check; đã thay bằng §7)

> **Sơ bộ** (quyết định #41): ngưỡng Hamming pHash ≤ 5 là mặc định, **chưa spot-check** bằng view `duplicate_pairs`.
> Số chính thức: L7 (spot-check → chỉnh ngưỡng nếu cần → chạy lại). Không suy ra mAP hay rare-event coverage từ số này.

- Ngày: 2026-09-30. Dữ liệu: `data/youtube5m/6WTF1JV5knI.mp4` (5 phút dashcam), decoder pyav 4 fps → pool **1.200 unit**.
- Mỗi lượt: state rỗng, chọn một lần K frame (`batch_size = K`), chấm `nd_rate_at_k` trên pHash.
  Recipe `diversity` chọn bằng `emb.dinov2_s`; metric dùng `hash.phash` → **độc lập** (§12.5) ở mọi lượt.
- Lệnh: `RAV_VIDEOS_DIR=data/youtube5m python -m src.rav.cli benchmark --k 50 200 --seeds 0 1 2 3 4`
  (P1 lần đầu 121 s trên CPU; benchmark 1,6 s khi đã cache).

| recipe | K | seeds | ND-Rate@K (mean ± std) | min–max | độc lập | relaxed |
|---|---:|---:|---:|---:|---|---|
| diversity | 50 | 5 | 0.00% ± 0.00% | 0.00%–0.00% | có | 0/5 |
| diversity | 200 | 5 | 0.00% ± 0.00% | 0.00%–0.00% | có | 0/5 |
| random | 50 | 5 | 1.60% ± 3.20% | 0.00%–8.00% | có | 0/5 |
| random | 200 | 5 | 5.70% ± 1.08% | 4.00%–7.00% | có | 0/5 |
| uniform | 50 | 5 | 0.00% ± 0.00% | 0.00%–0.00% | có | 0/5 |
| uniform | 200 | 5 | 1.50% ± 0.00% | 1.50%–1.50% | có | 0/5 |

`diversity` và `uniform` không phụ thuộc seed (không có ngẫu nhiên) nên std = 0.

**Lưu ý cho spot-check L7:** ND-Rate rất thấp ở mọi recipe, kể cả random K=200 (chọn 1/6 pool) chỉ ~6%.
Có thể ngưỡng 5 quá chặt với dashcam đang chạy (frame liền nhau 0,25 s đã lệch > 5 bit) → thang đo chưa phân biệt được
diversity với uniform ở K=50. Spot-check `duplicate_pairs` ở vài ngưỡng (5, 8, 10, 12) trước khi chốt.

Quét thử ngưỡng 10 (cùng lệnh, thêm `--metric nd_rate_at_k --metric nd_rate_at_k threshold=10`; vẫn **sơ bộ**):

| recipe | K=50 | K=200 |
|---|---:|---:|
| diversity | 4.00% | 31.00% |
| random | 20.00% ± 8.49% | 51.30% ± 2.62% |
| uniform | 10.00% | 29.50% |

Ở ngưỡng 10 thang đo có dải rộng (random ~50%), diversity vẫn thấp hơn random rõ, nhưng **ở K=200 không hơn uniform**
(31.0% vs 29.5%). Nếu spot-check chọn ngưỡng quanh 10 thì đây là câu hỏi chính của L7: vì sao diversity không hơn uniform khi K lớn.

## 7. Spot-check ngưỡng pHash + Benchmark CHÍNH THỨC (L7b, 2026-09-30)

### 7.1. Spot-check (quyết định #41, #51)

- Lệnh: `RAV_VIDEOS_DIR=data/youtube5m python -m src.rav.cli spotcheck 90bfbcf92da7c52e --distances 0 2 4 6 8 10 12 14 16 18 20 --per 4`
  (view `duplicate_pairs` trên cả pool 1.200 unit = 719.400 cặp, 4 cặp ngẫu nhiên mỗi mức, seed 0).
- Lưới: [spotcheck_90bfbcf92da7c52e.jpg](images/spotcheck_90bfbcf92da7c52e.jpg) — mỗi hàng một khoảng cách d, mỗi ô một cặp a | b.
- **pHash luôn có đúng 32 bit 1** (bit = hệ số DCT > trung vị) → khoảng cách luôn chẵn; ngưỡng 5 cũ thực chất là 4.

| d | số cặp đúng d | tích luỹ ≤ d | nhận xét (xem lưới) |
|---:|---:|---:|---|
| 0–2 | 78 | 0,01% | màn hình tiêu đề đen đầu video + cùng khoảnh khắc — trùng |
| 4 | 145 | 0,03% | cùng vị trí, frame liền nhau — trùng |
| 6 | 371 | 0,08% | cùng cảnh, lệch rất ít — gần trùng |
| 8 | 863 | 0,20% | lẫn: khoảng nửa cùng cảnh, nửa đã khác chi tiết (xe khác, góc khác) |
| 10 | 2.547 | 0,56% | khác thời điểm, chỉ giống "kiểu cảnh" (đường mưa) — không trùng |
| ≥ 12 | | | khác thời điểm |

**Chốt: ngưỡng Hamming ≤ 8** (người dùng chốt, #51) — bao mọi cặp gần trùng ở d ≤ 6 và phần cùng cảnh ở d = 8, chấp nhận vài cặp ở 8 đã khác chi tiết.
Giới hạn: spot-check trên **một** video dashcam mưa, 44 cặp được xem; video khác (đêm, cao tốc) có thể cần xem lại.

### 7.2. Benchmark chính thức

- Dữ liệu: `data/youtube5m/6WTF1JV5knI.mp4` (5 phút, `video_id` 90bfbcf92da7c52e), pyav 4 fps → pool **1.200 unit**. State rỗng, chọn một lần K frame.
- Lệnh: `RAV_VIDEOS_DIR=data/youtube5m python -m src.rav.cli benchmark --k 50 200 --seeds 0 1 2 3 4 --metric nd_rate_at_k --metric nd_rate_at_k threshold=6 --metric nd_rate_at_k threshold=10`
- `diversity` chọn bằng `emb.dinov2_s`, metric đo `hash.phash` → **độc lập** ở mọi lượt; không lượt nào phải nới bộ lọc (relaxed 0/5).

**ND-Rate@K, ngưỡng 8 (chính thức)** — thấp = tốt; mean ± std theo 5 seed (diversity, uniform tất định nên std = 0):

| recipe | K = 50 | K = 200 |
|---|---:|---:|
| diversity | **0,00%** | 12,00% |
| random | 7,60% ± 5,12% | 25,70% ± 3,30% |
| uniform | **0,00%** | **9,50%** |

Độ nhạy theo ngưỡng (không phải số chính thức, để xem kết luận có đổi không):

| recipe | K=50 @6 | K=200 @6 | K=50 @10 | K=200 @10 |
|---|---:|---:|---:|---:|
| diversity | 0,00% | 2,00% | 4,00% | 31,00% |
| random | 2,80% ± 3,92% | 12,60% ± 1,11% | 20,00% ± 8,49% | 51,30% ± 2,62% |
| uniform | 0,00% | 2,50% | 10,00% | 29,50% |

**Kết luận (trong phạm vi 1 video, 1.200 frame):**
- `diversity` ít gần trùng hơn `random` rõ ở mọi K và mọi ngưỡng (K=200 @8: 12,0% vs 25,7%, tức giảm ~53%).
- So với `uniform`: ngang ở K=50; **ở K=200 `diversity` không hơn `uniform`** (12,0% vs 9,5% @8; 2,0% vs 2,5% @6; 31,0% vs 29,5% @10).
  Kết luận này không đổi theo ngưỡng → là câu hỏi thuật toán (vd `nms_s`, `min_dist` của `greedy_nms`, hoặc thêm objective), làm việc riêng sau MVP-core.
- Không suy ra mAP hay rare-event coverage từ số này; cần thêm video (BDD100K) trước khi dùng làm bằng chứng chính.

## 8. Action Items

- [ ] Vì sao `diversity` không hơn `uniform` ở K lớn (§7.2) — thử `nms_s` / `min_dist`, xem có dồn chọn frame tiêu đề đầu video không.
- [ ] Spot-check thêm trên video khác trước khi mở rộng sang BDD100K.

