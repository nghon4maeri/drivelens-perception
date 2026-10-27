# Annotation Platform vs Data Curation Platform

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/research-pack/04_market_and_competitors/01_annotation_vs_data_curation.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Status: CONFIRMED EXPLANATION**

## Annotation Platform

Trọng tâm: **gán nhãn cho dữ liệu đã được chọn**.

Typical capabilities:
- bounding boxes;
- segmentation;
- classification;
- tracking;
- interpolation;
- annotator workflow;
- QA.

Ví dụ đã thảo luận:
- CVAT
- Label Studio

## Data Curation Platform

Trọng tâm: **chọn, tìm, kiểm tra và tổ chức dữ liệu trước hoặc giữa các vòng modeling/annotation**.

Typical capabilities:
- embeddings;
- similarity search;
- deduplication;
- diversity/representative sampling;
- outlier discovery;
- dataset exploration;
- active-learning related signals;
- export/handoff.

Ví dụ đã khảo sát:
- FiftyOne
- LightlyStudio
- Encord/Encord Active

## Ranh giới không tuyệt đối

Các platform ngày càng overlap. Một annotation tool có thể có model-assisted selection; một curation tool có thể tích hợp annotation.

Vì vậy competitor nên được đánh giá theo **use case thực tế**, không chỉ theo marketing category.
