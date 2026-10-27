# Các khái niệm cần phân biệt

**Status: CONFIRMED EXPLANATION**

## Data Curation vs Annotation

### Data Curation
Trả lời: **trong rất nhiều dữ liệu đang có, dữ liệu nào đáng được giữ, review hoặc đưa tiếp sang annotation?**

Ví dụ: deduplication, representative/diverse selection, similarity search, novelty analysis, coverage inspection, subset selection.

### Annotation
Trả lời: **với những dữ liệu đã được chọn, nhãn nào cần được gán?**

Ví dụ: bounding box, segmentation, classification, tracking, QA.

RAV-26 không cần tự xây lại annotation editor nếu có thể export sang công cụ chuyên dụng.

## Bốn khái niệm không được đánh đồng

1. **Within-video redundancy:** nhiều frame trong cùng video gần như lặp lại cùng scene/state.
2. **Incremental novelty:** frame mới khác thế nào so với historical/existing dataset.
3. **Semantic/task relevance:** khác về feature chưa chắc hữu ích cho task.
4. **Downstream training utility:** thêm frame đó có giúp model tốt hơn sau labeling/retraining hay không.

## Top-K / Budget K

`K` là số sample được phép chọn từ candidate pool. Các chiến lược phải so sánh với cùng `K`.

## PCA / t-SNE / MDS

Có thể hữu ích cho exploration/debugging, nhưng không dùng khoảng cách trên plot 2D làm ground truth selection/evaluation vì projection làm méo geometry.
