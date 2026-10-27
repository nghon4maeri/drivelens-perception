# RAV-26 — Project Brief

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/gate-1/brief.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


**Tên MVP:** Video Frame Sample Selection  
**Lĩnh vực:** Computer Vision / ADAS / Robotics  
**Đối tượng:** Data/ML Engineer, Computer Vision Engineer  
**Thời gian thực hiện:** 3 tuần  
**Trạng thái:** Draft — thống nhất với PRD v2.1

## 1. Problem

Video tạo ra lượng lớn frame liên tiếp có nội dung gần giống nhau. Khi nguồn lực xử lý và gán nhãn có hạn, chọn frame ngẫu nhiên hoặc theo khoảng thời gian cố định có thể giữ lại nhiều ảnh dư thừa. Kỹ sư cần chọn **K frame từ một tập video lớn**, giảm trùng lặp mà không phải kiểm tra từng frame thủ công.

## 2. Tín hiệu từ người dùng

Khảo sát định tính trên Reddit và GitHub cho thấy người làm computer vision gặp vấn đề với near-duplicate frame, chọn frame để annotation và tìm một tập ảnh khác biệt từ video. Chẳng hạn, một [thảo luận về near-duplicate trong dataset từ video](https://www.reddit.com/r/computervision/comments/1njbnbg/impact_of_nearduplicate_samples_for_datasets_from/) ghi nhận **+2 vote, 2 comment đọc được** tại thời điểm khảo sát; [FiftyOne issue #4568](https://github.com/voxel51/fiftyone/issues/4568) nêu nhu cầu chọn **100 frame khác biệt** từ video 30 FPS. Đây là **tín hiệu định tính**, chưa chứng minh quy mô thị trường, mức sẵn sàng chi trả hoặc hiệu quả thuật toán.

## 3. Proposed Solution

RAV-26 nhận video/thư mục frame, lưu `video_id` và timestamp, trích xuất visual embedding bằng một pretrained encoder, rồi cho người dùng chọn số frame cần giữ (**selection budget K**). MVP chạy và đối chiếu ba chiến lược trên **cùng candidate pool, cùng K**:

| Chiến lược | Cách chọn |
|---|---|
| Random Sampling | Chọn ngẫu nhiên K frame. |
| Uniform Temporal Sampling | Chọn K frame cách đều theo thời gian, có quy tắc phân bổ giữa các video. |
| Embedding Diversity | Chọn tuần tự những frame khác biệt theo embedding; cập nhật tập frame đã chọn làm reference trong cùng batch. |

**Đầu ra:** Top-K frame, CSV/JSON chứa đường dẫn và metadata, contact sheet/gallery để quan sát, và báo cáo so sánh baseline.

## 4. MVP Scope & Non-goals

**Trong 3 tuần:** ingestion → embedding/cache → 3 phương pháp selection → xuất Top-K → gallery/contact sheet → một phép đánh giá chính. Có thể triển khai CLI trước, không bắt buộc web app.

**Chưa làm:** Agent/Agent Review, approve–reject workflow, hệ thống annotation, uncertainty scoring, retraining và Active Learning loop, tích hợp CVAT/Label Studio. Giảm trùng lặp **không đồng nghĩa** giữ được mọi rare event hay cải thiện mAP.

## 5. Success Metric — một giả thuyết để kiểm chứng

**Near-Duplicate Rate@K:** tỷ lệ frame trong batch Top-K có ít nhất một frame khác gần trùng theo ngưỡng similarity đã chốt. Ngưỡng được hiệu chỉnh trên một tập nhỏ **cặp frame kiểm tra thủ công** trước khi benchmark; ưu tiên phép đo độc lập với embedding dùng để chọn ảnh, ví dụ pHash.

**Mục tiêu thử nghiệm:** Embedding Diversity giảm Near-Duplicate Rate@K **ít nhất 20% tương đối so với trung bình Random Sampling** trên benchmark và K chính đã chốt; vẫn báo cáo kết quả so với Uniform Temporal. Random cần lưu seed và nên chạy nhiều seed để quan sát độ biến động. Mục tiêu 20% là **giả thuyết**, chưa phải kết quả đã đạt.

**Phân biệt kết quả:** Pipeline chạy đúng, tái lập và xuất đủ K frame = hoàn thành kỹ thuật MVP; chỉ khi đạt mục tiêu đã chốt mới ghi nhận giả thuyết giảm redundancy được xác nhận trên benchmark đó.

## 6. Data & Delivery

**Dữ liệu dự kiến:** một vài sequence MOT17 hoặc UA-DETRAC để phát triển; subset BDD100K để kiểm tra gần domain ADAS hơn. MVP **không yêu cầu nhãn object detection**.

| Tuần | Deliverable |
|---|---|
| 1 | Dataset manifest, embedding cache, Random và Temporal Sampling. |
| 2 | Embedding Diversity, xuất Top-K, contact sheet, hiệu chỉnh ngưỡng near-duplicate. |
| 3 | Benchmark cùng K, báo cáo Near-Duplicate Rate@K, failure cases và demo. |

**Sau MVP:** Nếu kết quả giảm duplicate có tín hiệu tốt, mới nghiên cứu Coverage/Representativeness → Event Recall (khi có nhãn event) → Active Learning và downstream model performance.
