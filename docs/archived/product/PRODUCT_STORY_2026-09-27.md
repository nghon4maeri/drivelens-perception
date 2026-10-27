# RAV-26: chọn frame đáng gán nhãn từ video, có người duyệt

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/PRODUCT_STORY.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


> Bài giới thiệu ngắn cho người mới vào dự án. Mỗi đoạn có **nguồn** trỏ tới tài liệu gốc:
> đọc thấy chỗ nào khó hiểu hoặc không khớp thì sửa ở tài liệu nguồn trước, rồi cập nhật lại bài này.
> Cập nhật 2026-09-27.

## 1. Chuyện bắt đầu từ đâu

Một chiếc xe gắn camera chạy 1 giờ ghi được hơn 100.000 frame. Đa số frame liền nhau gần như giống hệt nhau:
xe dừng đèn đỏ 40 giây là vài trăm frame cùng một cảnh. Nếu ta muốn huấn luyện các mô hình perception (object detection, segmentation, ...) trên dữ liệu mới mà chỉ có tiền gán nhãn cho vài trăm frame, thì chọn ngẫu nhiên hay cứ mỗi giây lấy một frame sẽ khiến phần lớn ngân sách trả cho những cảnh đã có.

Đề bài RAV-26 gốc khá rộng (data curation + active learning, cải thiện model). Nên nhóm thu hẹp lại cho MVP:

> Trong một lượng lớn frame từ video, chọn một tập nhỏ hơn, ít trùng lặp hơn, nhưng vẫn giữ thông tin hữu ích.

Nguồn: [đề bài gốc](../2026-10-13/docs/research-pack/01_origin/01_original_rav26_problem.md) ·
[cách hiểu đã chốt](../2026-10-13/docs/research-pack/02_problem_understanding/01_problem_reframing_confirmed.md) ·
[alignment với đề bài](../../design/RAV26_ALIGNMENT.md)

## 2. Người dùng

**Minh** (nhân vật minh hoạ), Data/ML Engineer của một nhóm ADAS - Advanced Driver Assistance Systems. Minh cần chọn **200 frame** từ video dashcam để gửi gán nhãn.
Minh không muốn bấm qua từng frame, nhưng cũng không tin một thuật toán chọn thay mình mà không cho xem vì sao.

Nguồn: [đề bài gốc, mục User](../2026-10-13/docs/research-pack/01_origin/01_original_rav26_problem.md) ·
[vai trò người duyệt](../../design/CONTRACTS.md) (§12)

## 3. Một buổi làm việc của Minh

**Tạo phiên.** Minh mở app, tải các video dashcam lên hệ thống (một hoặc nhiều video). Minh bấm "Phiên mới", chọn các video muốn dùng,
ghi mục tiêu *"giữ frame khác biệt về cảnh/ánh sáng/đối tượng; bỏ frame mờ hoặc gần trùng"* và ngân sách **K = 200**.
Lần đầu nhận một video, hệ thống mất vài phút tách frame và tính embedding; các phiên sau dùng lại video đó thì mở ngay.
Nguồn: [upload + luồng chính](../../architecture_diagram.md) (§5.2, §6) · [pipeline chuẩn bị video P1](../../architecture_diagram.md) (§4.2–4.3) ·
[thuật toán không phụ thuộc số video](../../design/CONTRACTS.md) (§13)

**Nhận đề xuất.** Minh chọn chiến lược *novelty* và bấm "Đề xuất". Hệ thống trả **16 frame** (một batch),
khác nhau và khác những frame mà Minh đã chọn trước đó (Minh đang xây dựng tập dữ liệu 200 frame để train); mỗi frame kèm điểm của từng tiêu chí.
Nguồn: [thuật ngữ recipe / batch / proposal](../../architecture_diagram.md) (§4.1) · [hàm `propose()`](../../architecture_diagram.md) (§4.4)

**Duyệt.** Minh mở từng frame, tua vài giây trước và sau để hiểu ngữ cảnh, rồi **keep** hoặc **drop**.
Mỗi quyết định được ghi lại: ai quyết, lúc nào, cho đề xuất nào.
Nguồn: [log quyết định](../../design/CONTRACTS.md) (§12.3) · [API xem/sửa state](../../architecture_diagram.md) (§5)

**Lặp lại.** Vòng sau hệ thống sẽ tự tránh những frame giống cái Minh đã giữ. Thấy chiến lược hiện tại đề xuất dở, Minh đổi sang chiến lược khác;
các quyết định cũ vẫn giữ nguyên. Thanh tiến độ báo **kept / 200**.
Nguồn: [đổi recipe giữa phiên](../../design/CONTRACTS.md) (§10) · [ngân sách target_k](../../architecture_diagram.md) (§4.1)

**Xem tổng thể.** Bản đồ embedding cho thấy các frame đã chọn phủ không gian thế nào; timeline cho thấy đề xuất có dồn vào một đoạn video không.
Nguồn: [trực quan hoá](../../architecture_diagram.md) (§7)

**Xuất.** Đủ 200 frame, Minh xuất thư mục ảnh + CSV để đưa vào CVAT / Label Studio.
Nguồn: [phạm vi MVP](../../../PLAN.md)

## 4. Làm sao biết nó tốt hơn chọn ngẫu nhiên?

Cùng một tập frame, cùng K, so ba cách: **Random**, **Uniform Temporal** (cách đều), và chiến lược của hệ thống.
Thước đo chính là **Near-Duplicate Rate@K**: bao nhiêu phần trăm frame được chọn có một frame khác gần trùng với nó.
Phép đo trùng dùng **pHash**, độc lập với embedding dùng để chọn, để thuật toán không tự chấm điểm cho chính mình.
Trong phiên còn có **keep rate theo chiến lược**: chiến lược nào được Minh giữ nhiều hơn.

**Không claim:** tăng mAP, giảm chi phí theo %, hay bắt được mọi tình huống hiếm; những điều này cần gán nhãn + huấn luyện lại để chứng minh.

Nguồn: [phạm vi đánh giá đã chốt](../2026-10-13/docs/research-pack/02_problem_understanding/03_evaluation_scope_confirmed.md) ·
[kết quả hiện có](../../../eval/results/report.md) · [quy tắc độc lập](../../design/CONTRACTS.md) (§12.5)

## 5. Vì sao thiết kế như vậy

| Quyết định | Lý do ngắn | Nguồn |
|---|---|---|
| Tự làm web, không build trên FiftyOne / LightlyStudio | Hai tool khó tiếp cận cho việc chọn + duyệt frame; thuật toán chọn không vượt trội; build trên đó có thể tốn công hơn | [thử nghiệm 27/09](../research/05_hands_on_audit/06_findings_2026-09-27.md) · [audit trước](../research/05_hands_on_audit/01_audit_summary.md) |
| Mọi thuật toán là một file cắm vào, chọn bằng tên trong config | Thử chiến lược mới không phải sửa pipeline, API hay UI | [CONTRACTS §1–5](../../design/CONTRACTS.md) |
| Thuật toán không biết có mấy video | 1 video hay 1.000 video chỉ khác bộ lọc pool | [CONTRACTS §13](../../design/CONTRACTS.md) |
| Người là "judge", nhưng judge thay được | Log quyết định của người hôm nay là dữ liệu để sau này đánh giá recipe không cần người, hoặc cắm judge tự động | [CONTRACTS §12](../../design/CONTRACTS.md) |
| Chỉ bước chuẩn bị video là nặng | Tính một lần mỗi video rồi cache; đề xuất và đánh giá chỉ tốn mili-giây | [architecture §4.3](../../architecture_diagram.md) |

## 6. Chưa làm và giới hạn

- MVP là **bản 1 người dùng, chạy local**; muốn host cho nhiều người phải rà lại trước. → [LOCAL_SINGLE_USER_LIMITS](../../design/LOCAL_SINGLE_USER_LIMITS.md)
- Bản đầu tiên được kiểm thử trên 1 video (dữ liệu hiện có là mock, cần video thật ≥ 1.000 frame); làm việc trên nhiều video
  đã có trong thiết kế và là bước tiếp theo. → [PLAN](../../../PLAN.md) · [CONTRACTS §13](../../design/CONTRACTS.md)
- Chưa có: tìm lỗi model bằng hai detector, chấm cả đoạn clip, judge tự động, agent chatbot, giải thích trực quan. → [architecture §10](../../architecture_diagram.md) · [khảo sát YOLO](../2026-10-13/docs/research-pack/03_research_backing/03_perception_reference_models.md)

## 7. Đọc tiếp

[README](../../../README.md) → [PLAN](../../../PLAN.md) → [architecture_diagram.md](../../architecture_diagram.md) → [CONTRACTS.md](../../design/CONTRACTS.md) → [research-pack](../2026-10-13/docs/research-pack/00_README.md)
