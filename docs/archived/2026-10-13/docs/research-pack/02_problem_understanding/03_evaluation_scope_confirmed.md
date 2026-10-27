# Evaluation scope đã chốt cho MVP

**Status: CONFIRMED**

## Progressive validation

1. chứng minh reduction of redundancy trước;
2. sau đó mới bổ sung coverage/representativeness/event metrics;
3. downstream mAP/F1 chỉ hợp lệ khi thực sự có labeling + retraining + holdout đúng.

## Baseline

So sánh cùng selection budget `K`:
- Random Sampling
- Uniform Temporal Sampling
- Embedding-based Diversity / subset selection

## Primary metric MVP

**Near-Duplicate Rate@K**

Câu hỏi: trong K frame được chọn, tỷ lệ near-duplicate là bao nhiêu?

Mốc giảm 20% tương đối từng được dùng như **product validation threshold thử nghiệm**, không phải con số do literature chứng minh.

## Điều kiện

- cùng candidate pool;
- cùng K;
- duplicate threshold cần spot-check thủ công;
- tránh tự đánh giá bằng đúng selection score;
- nếu dùng pHash/perceptual similarity phải ghi rõ đây là proxy.

## Không được claim

Near-Duplicate Rate thấp hơn không tự động nghĩa là mAP tăng, training value cao hơn, rare-event coverage tốt hơn hay semantic content được bảo toàn đầy đủ.
