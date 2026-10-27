# Đề bài RAV-26 ban đầu

**Status: ORIGINAL — điểm xuất phát của dự án, không phải current scope.**

Tài liệu này cố ý giữ đề bài gần với PRD Gate 1 ban đầu để luôn thấy RAV-26 đã rẽ nhánh từ đâu.

## Product ban đầu

**RAV-26 — Data Curation Copilot for ADAS & Robotics**

- Domain: ADAS / Robotics — Data Curation & Active Learning.
- Primary user: Data/ML Engineer hoặc Data Curation Engineer.
- Bối cảnh: dữ liệu fleet/synthetic rất lớn trong khi ngân sách labeling có hạn.
- Vấn đề chính: nhiều sample trùng lặp, thiếu coverage, rare case có thể bị bỏ sót, khó biết sample nào đáng ưu tiên.
- Core value ban đầu: chọn ít sample hơn nhưng có giá trị huấn luyện cao hơn, có explanation và human review.
- MVP horizon ban đầu: 3–4 tuần.

## Problem statement gốc

Kỹ sư ADAS/Robotics phải xử lý lượng dữ liệu rất lớn nhưng chỉ có ngân sách gán nhãn giới hạn. Việc chọn thủ công hoặc ngẫu nhiên có thể:

- đưa quá nhiều frame gần giống nhau vào annotation;
- bỏ sót rare case hoặc ODD chưa được cover tốt;
- làm tăng chi phí annotation;
- không có quy trình định lượng để chứng minh batch được chọn tốt hơn random.

PRD gốc còn đặt câu hỏi về model weakness trước labeling, nhưng phần đó về sau được tách khỏi MVP data-curation đầu tiên.

## User ban đầu

Data/ML Engineer hoặc Data Curation Engineer chịu trách nhiệm dataset ADAS/Robotics.

Mục tiêu trong PRD gốc:
- chọn sample giá trị cao để gán nhãn;
- giảm annotation cost;
- cải thiện model;
- có explanation đủ để người dùng tin vào batch được đề xuất.

## Vì sao file này phải tồn tại riêng

Sau nhiều vòng research, scope đã được thu hẹp và một số giả định ban đầu đã bị tách ra khỏi MVP.

Không chỉnh ngược PRD gốc để “khớp” hướng hiện tại. Điểm xuất phát cần được giữ nguyên:

> **RAV-26 ban đầu là bài toán rộng về data curation + active learning cho ADAS/Robotics, với tham vọng chọn sample có training value cao hơn dưới annotation budget hữu hạn.**
