# Cách hiểu bài toán đã được chốt sau thảo luận

**Status: CONFIRMED**

## 1. Pain point gần nhất của MVP

Đối với video ADAS/Robotics, dữ liệu có tính liên tục theo thời gian nên nhiều frame liền nhau chứa thông tin gần giống nhau.

Bài toán gần nhất được chốt cho MVP:

> **Trong một lượng lớn frame từ video, làm sao chọn một subset nhỏ hơn, ít redundant hơn, nhưng vẫn giữ được phần thông tin hữu ích cần quan sát tiếp?**

## 2. Data Curation và Active Learning được tách ra

- **MVP:** sample/frame selection và data curation.
- **Phase sau:** Active Learning, model uncertainty và retraining loop.

Không dùng embedding/diversity để tuyên bố trực tiếp model sẽ cải thiện.

## 3. Video là trọng tâm hơn independent images

Video có temporal redundancy, nhiều frame gần cùng scene, thay đổi nhỏ do motion/illumination/wiper/blur, và event ngắn.

Vì vậy cần giữ `video_id`, `frame_id/frame_index`, timestamp và provenance khi đánh giá selection.

## 4. “Khác biệt” phải dùng từ cẩn thận

Embedding distance không đồng nghĩa semantic value.

```text
pixel difference
→ visual-feature difference
→ semantic difference
→ task-relevant difference
```

Với DINO/CLIP distance, cách gọi phù hợp hơn là **visual-feature dissimilarity**.

## 5. Giá trị dữ liệu là tương đối

Một điểm được làm rõ về sau:

> Dữ liệu mới không chỉ cần khác nhau trong chính batch mới; về dài hạn, giá trị của nó phải được hiểu tương đối với những gì dataset hiện tại đã có.

Tuy nhiên incremental data value **chưa được chứng minh** bằng audit hiện tại và chưa phải metric MVP đã validate.
