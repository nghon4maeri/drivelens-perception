# RAV-26 — Khảo sát perception và cấu hình mô hình mục tiêu–tham chiếu

**Ngày tổng hợp:** 27/09/2026  
**Trạng thái:** khảo sát nguồn công khai và ước tính để thiết kế thí nghiệm; chưa benchmark trên phần cứng của dự án.

> **Cập nhật sau 27/09:** phần so mô hình mục tiêu với tham chiếu (YOLO11s/m vs 11x) là **hướng mở rộng, không thuộc MVP 4 ngày**
> (quyết định #11 trong `06_project_state/01_confirmed_decisions.md`). Các mục "Kết luận phục vụ MVP" và "Quyết định tạm thời"
> bên dưới giữ nguyên để lưu lịch sử; contract đã chừa chỗ cho hướng này (feature detection + objective disagreement).

## 1. Kết luận phục vụ MVP

RAV-26 sẽ thử dùng mô hình offline mạnh hơn làm tham chiếu để tìm các frame mà mô hình mục tiêu có khả năng xử lý chưa tốt. Hệ thống giảm trùng, đề xuất từng nhóm nhỏ k mẫu, nhận phản hồi giữ/bỏ và tìm giống/khác từ kỹ sư, rồi cập nhật lượt đề xuất tiếp theo.

Cấu hình thử nghiệm đề xuất:

| Vai trò | Model | Triệu tham số | GFLOPs/ảnh ở 640×640 |
| --- | --- | ---: | ---: |
| Mục tiêu ngân sách thấp | YOLO11s | 9,4 | 21,6 |
| Mục tiêu ngân sách cao hơn | YOLO11m | 20,1 | 68,1 |
| Tham chiếu offline | YOLO11x | 56,9 | 195,3 |

Thông số model lấy từ [Ultralytics YOLO11][S1]. Tham chiếu x có lượng tính toán khoảng 9,0 lần bản s và 2,9 lần bản m. Chênh lệch này chưa bảo đảm model lớn đúng hơn ở mọi tình huống.

Đây là cấu hình đại diện để thử giả thuyết, chưa phải mô hình của VinFast hoặc một hãng cụ thể. Không cần biết checkpoint độc quyền để tiến hành thí nghiệm public, nhưng kết quả chỉ có giá trị trong phạm vi model và dữ liệu đã thử.

## 2. Bối cảnh và giới hạn đã thống nhất

- Dự án được giao đề bài RAV-26; không được cấp dữ liệu, phải tự tìm dataset public để đánh giá và chứng minh giá trị.
- Thời gian còn 4 ngày để có MVP, sau đó tiếp tục cải tiến.
- Không có tập huấn luyện cũ của khách hàng. Tập mẫu đã duyệt sẽ hình thành dần trong mỗi phiên curation.
- Mô hình public được phép dùng; các tiêu chí chọn dữ liệu và phương pháp đánh giá cần là module thay thế được.
- Luồng sản phẩm ưu tiên nhập nhiều video, đề xuất k frame/đoạn nhỏ mỗi vòng và điều chỉnh theo phản hồi.
- Báo cáo này tập trung vào detection 2D từ camera. Chưa suy rộng sang toàn bộ perception 3D, hợp nhất cảm biến hoặc chính sách điều khiển robot.

## 3. Phân biệt thông tin công bố và ước tính

| Loại thông tin | Ý nghĩa |
| --- | --- |
| Phần cứng công bố | Chip hoặc tùy chọn phần cứng xuất hiện trong tài liệu chính thức |
| Mô hình công bố | Tên họ model hoặc checkpoint mà nguồn chính thức xác nhận |
| Kịch bản giả định | Số camera, FPS và tỷ lệ tài nguyên do dự án đặt để tính ngân sách |
| Ước tính ngân sách | Kết quả của công thức sàng lọc, chưa phải benchmark hay giới hạn vật lý |

Các hãng thường không công bố số tham số perception. Không suy ra cấu hình thực tế của hãng chỉ từ tên chip. Cụm “tối đa” trong báo cáo được hiểu là model lớn nhất đáng đưa vào benchmark trong kịch bản giả định, không phải trần đã xác minh.

## 4. Cơ sở nghiên cứu

| Nguồn | Nội dung liên quan | Cách áp dụng và giới hạn |
| --- | --- | --- |
| [ALWOD][S8] | Dùng bất đồng teacher–student kết hợp uncertainty để chọn ảnh cần gán nhãn qua các vòng active learning | Có tiền lệ cho tín hiệu bất đồng; cấu hình nghiên cứu khác cặp YOLO nhỏ–lớn cố định của MVP |
| [Waymo: Offboard 3D Object Detection][S9] | Dùng cả chuỗi point cloud và tính toán offline để tạo kết quả detection/nhãn tốt hơn | Tham chiếu có thể mạnh hơn nhờ ngữ cảnh thời gian, ngoài số tham số; nghiên cứu này thuộc LiDAR 3D |

Hai công trình hỗ trợ hướng nghiên cứu, chưa chứng minh phương pháp RAV-26 sẽ tăng mAP. Bất đồng chỉ là tín hiệu tìm ứng viên lỗi; mô hình lớn không phải ground truth.

## 5. Cách ước tính ngân sách tính toán

### 5.1. Mốc thực nghiệm

[Benchmark Ultralytics DeepStream][S2] công bố YOLO11s trên Orin NX 16 GB, ảnh 640×640:

- TensorRT FP16: 7,91 ms/ảnh.
- TensorRT INT8: 6,05 ms/ảnh.
- Model có 21,6 GFLOPs/ảnh theo quy ước thống kê của Ultralytics [S1].

Lấy lượng công việc của model chia thời gian inference được khoảng 2.730–3.570 GFLOPs tương đương/giây. “Tương đương” là cách quy về lượng công việc của model; inference INT8 không có nghĩa chip đang thực hiện số phép toán dấu phẩy động đó mỗi giây. Đây cũng chưa phải độ trễ toàn bộ pipeline.

### 5.2. Giả định ngoại suy

Để sàng lọc ứng viên, tạm dùng hệ số 2.000–4.000 GFLOPs tương đương/giây trên mỗi 100 TOPS công bố, làm tròn quanh mốc trên. Giả định 50% ngân sách dành cho detector camera; phần còn lại dành cho các tác vụ khác và khoảng dư.

```text
G_ảnh ≈ (2.000–4.000) × (P / 100) × 0,5 / (N_camera × FPS_detection)

P: tổng TOPS của các chip được giả định tham gia tải detection này.
G_ảnh: ngân sách GFLOPs tương đương cho mỗi ảnh.
```

**Ngoại suy tuyến tính theo TOPS là giả định yếu nhất.** TOPS có thể tính ở precision/sparsity khác nhau, cộng GPU và DLA, trong khi model không tận dụng hết các đơn vị đó. Khoảng trên là phân tích kịch bản, không phải khoảng tin cậy thống kê hoặc hệ số chuyển đổi phần cứng phổ quát. Cần thay bằng benchmark đúng chip khi có thiết bị.

Không quy đổi GFLOPs thành số tham số trên các kiến trúc bất kỳ. Báo cáo chỉ đối chiếu các biến thể cùng họ YOLO11, cùng tác vụ và độ phân giải.

## 6. Bảng hãng, sản phẩm và model tối đa đáng thử

**Tất cả số camera × FPS dưới đây là giả định của dự án, không phải cấu hình hãng công bố.** Các khoảng là ngân sách tính toán, chưa kiểm tra bộ nhớ hoặc độ trễ p95.

| Hãng / sản phẩm | Phần cứng được công bố | Kịch bản giả định | GFLOPs tương đương/ảnh | Model lớn nhất đáng thử |
| --- | --- | --- | ---: | --- |
| Unitree G1-Comp | Orin NX, 100 TOPS; hãng xác nhận dùng YOLO11 nhưng không nêu biến thể [S3] | 2 camera × 30 FPS | 16,7–33,3 | YOLO11s: 9,4 M / 21,6 G; cân nhắc n nếu thiếu dư địa |
| Unitree G1-D, tùy chọn Orin | Orin NX 16 GB, 100 TOPS [S4] | 1 camera chạy detection × 20 FPS | 50–100 | YOLO11m/l: 20,1–25,3 M / 68,1–87,2 G, ở phía thuận lợi của ước tính |
| Unitree G1-D, cùng tùy chọn | Như trên | 3 camera × 20 FPS | 16,7–33,3 | YOLO11s: 9,4 M / 21,6 G |
| Volvo EX90, cấu hình hai Orin | Hai DRIVE AGX Orin [S5]; tối đa 254 INT8 TOPS mỗi Orin theo NVIDIA [S6] | 6 camera × 20 FPS; chỉ một chip phục vụ tải này | 21,2–42,3 | YOLO11s: 9,4 M / 21,6 G |
| Volvo EX90, giả định chia tải hai chip | Cùng phần cứng | 6 camera × 20 FPS; hai chip chia luồng | 42,3–84,7 | YOLO11m: 20,1 M / 68,1 G, ở phía thuận lợi của ước tính |
| NIO ET7, cấu hình Adam dùng Orin | Bốn Orin, tổng 1.016 TOPS [S7] | 8 camera × 20 FPS; hai chip phục vụ tải này | 31,8–63,5 | YOLO11s; bản m 68,1 G nằm trên khoảng, cần điều chỉnh hoặc đo lại |
| NIO ET7, giả định huy động bốn chip | Cùng phần cứng | 8 camera × 20 FPS; cả bốn chip chia luồng | 63,5–127 | YOLO11m/l: 20,1–25,3 M / 68,1–87,2 G |
| VinFast VF8/VF9 | Chưa xác minh được cấu hình chip theo đúng phiên bản xe từ nguồn đã đọc | Chưa đủ dữ kiện để gán số theo sản phẩm | Chưa xác định | Có thể thử cấu hình giả lập một DRIVE Orin, nhưng không gắn kết quả đó thành thông số VF8/VF9 |
| Tesla, thế hệ HW3 / FSD Computer | Hai SoC dự phòng, neural accelerator chuyên dụng; mục tiêu nền tảng dưới 100 W trong công bố Hot Chips 2019 [S12] | Không áp hệ số Orin NX cho kiến trúc Tesla | Chưa xác định | Không đủ căn cứ quy thành model YOLO tối đa; hai chip dự phòng không mặc nhiên cộng thành ngân sách cho một model |
| Tesla Model 3/Y/S/X có AI4 và Cybertruck | Tài liệu dịch vụ Tesla xác nhận các cấu hình AI4 [S13]; không có nghĩa mọi đời xe đều dùng AI4 | FSD end-to-end; không giả định mỗi camera chạy một detector YOLO độc lập | Chưa xác định | Chưa xác minh được số tham số/GFLOPs tuyệt đối; cần benchmark và mô tả workload đúng kiến trúc |

**Mức tin cậy:** hàng Orin NX gần mốc benchmark hơn; các hàng DRIVE Orin là ngoại suy có độ tin cậy thấp hơn. Việc nhiều chip chia các luồng độc lập là giả định kiến trúc; cộng TOPS không chứng minh một model có thể dùng toàn bộ tài nguyên đó. Chip có thể dành cho dự phòng hoặc tác vụ khác.

Thông báo VinFast năm 2021 nói về Xavier và kế hoạch Orin [S10]; thông báo robotaxi năm 2026 nói về chương trình phát triển dựa trên Hyperion 10 [S11]. Hai nguồn này chưa đủ để xác nhận checkpoint hoặc cấu hình perception đang dùng trên từng phiên bản VF8/VF9.

## 7. Bảng đối chiếu kích thước model

Theo [Ultralytics YOLO11][S1], detection trên COCO, ảnh 640×640:

| Model | Triệu tham số | GFLOPs/ảnh | COCO mAP50–95 công bố |
| --- | ---: | ---: | ---: |
| YOLO11n | 2,6 | 6,5 | 39,5 |
| YOLO11s | 9,4 | 21,6 | 47,0 |
| YOLO11m | 20,1 | 68,1 | 51,5 |
| YOLO11l | 25,3 | 87,2 | 53,4 |
| YOLO11x | 56,9 | 195,3 | 54,7 |

Điểm COCO giúp chọn ứng viên, không phải dự báo mAP trên dữ liệu ADAS mới. RAM/VRAM cần cho inference gồm cả activation, buffer và runtime; không thể lấy kích thước weights làm tổng nhu cầu bộ nhớ.

## 8. Thử nghiệm tiếp theo cho RAV-26

### 8.1. Cách dùng hai model

Chạy mục tiêu và tham chiếu trên cùng frame, thống nhất bộ lớp và tọa độ box. Ghép box để tìm đối tượng chỉ một bên phát hiện, bất đồng lớp và khác biệt định vị. Chuẩn hóa cách tổng hợp điểm để cảnh đông đối tượng không tự động chiếm hết đề xuất. Giảm trùng theo nội dung và thời gian trước khi trả k mẫu.

Giữ/bỏ mẫu và yêu cầu tìm giống/khác là hai loại phản hồi riêng. Các frame đã duyệt tạo thành trạng thái phiên; chưa cần train lại detector sau mỗi lượt duyệt. Hai model cùng họ có thể cùng bỏ sót đối tượng, vì vậy cần dành một phần đề xuất cho khám phá độc lập với bất đồng.

### 8.2. Kiểm chứng trước khi gọi tham chiếu là đáng tin

1. Chọn dữ liệu public có nhãn; giữ nhãn riêng khỏi bộ chọn. Tách dữ liệu theo video/chuyến khi có thể.
2. Kiểm tra chất lượng tham chiếu trên các nhóm quan tâm và kiểm tra đúng/sai ngay trong tập bất đồng.
3. So top-k với random, uncertainty đơn lẻ và diversity đơn lẻ, cùng ngân sách duyệt; chạy nhiều seed khi phù hợp.
4. Đo tỷ lệ đề xuất có lỗi thật của mục tiêu, số sự kiện lỗi khác nhau, công duyệt trên mỗi lỗi và chi phí inference.
5. Đo thời gian, bộ nhớ, precision và độ phân giải trên máy thực; benchmark thiết bị triển khai là bước riêng khi có phần cứng.

Uplift mAP sau train là phép kiểm chứng tiếp theo. Việc tham chiếu tìm được lỗi của model nhỏ không bảo đảm model nhỏ đủ năng lực học sửa lỗi đó. Không chuyển điểm bất đồng thành con số uplift dự đoán khi chưa có hiệu chuẩn thực nghiệm.

## 9. Tesla end-to-end khác gì với phạm vi MVP?

### 9.1. Những gì Tesla công bố

Tesla mô tả hướng tự hành dựa trên thị giác và mạng neural end-to-end trong báo cáo Q1/2025 [S14]. Tài liệu giải thích FSD mô tả mạng học hành vi lái xe từ con người [S15]. Báo cáo Q3/2024 nêu FSD 12.5 tăng số tham số 5 lần, nhưng không cung cấp số tuyệt đối trong đoạn công bố đó [S16]. Không thể dùng hệ số này để suy ra một model 50 M, 500 M hoặc một kích thước cụ thể khác.

“End-to-end” ở đây mô tả việc học ánh xạ từ thông tin đầu vào tới hành vi lái xe. Nó không đủ để kết luận toàn bộ hệ thống chỉ có đúng một mạng, không có biểu diễn trung gian, hoặc không có bộ điều khiển/kiểm tra bổ trợ. Báo cáo không suy đoán sơ đồ nội bộ độc quyền của Tesla.

### 9.2. So sánh bài toán

RAV-26 là công cụ chọn dữ liệu offline. Phạm vi thử nghiệm hiện tại của công cụ là cải thiện một detector 2D. FSD là hệ thống thực hiện tác vụ lái xe. Bảng dưới so sánh tác vụ học mà dữ liệu phục vụ, không coi hai sản phẩm là cùng loại.

| Tiêu chí | MVP RAV-26 phục vụ detector 2D | Curation phục vụ hệ thống lái xe end-to-end kiểu Tesla |
| --- | --- | --- |
| Câu hỏi của mô hình được cải thiện | Có đối tượng gì và ở đâu trong ảnh? | Hệ thống nên di chuyển thế nào trong bối cảnh hiện tại? |
| Đầu vào thử nghiệm | Frame từ video; đoạn xung quanh giúp kỹ sư hiểu ngữ cảnh | Thông thường cần sequence camera đồng bộ và thông tin trạng thái/điều hướng phù hợp; không khẳng định schema nội bộ Tesla |
| Đầu ra cần so sánh | Bounding box, lớp, score | Quỹ đạo hoặc quyết định/hành vi lái xe, tùy kiến trúc |
| Đơn vị dữ liệu chọn | Frame đại diện, có liên kết về clip và timestamp | Sequence/sự kiện để giữ diễn biến trước–sau và tương tác giao thông |
| Dữ liệu giám sát cho benchmark | Nhãn box và lớp; một số thuộc tính cảnh để phân tích nhóm | Quỹ đạo/hành vi tham chiếu, trạng thái xe và dữ liệu đồng bộ; một ảnh có box không đủ |
| Bất đồng dùng để đề xuất | Thiếu box, khác lớp, lệch vị trí giữa hai detector | Khác biệt về diễn biến/quỹ đạo/quyết định; phải xem xét nhiều hành động đều có thể hợp lệ |
| Metric học được gì | mAP, recall và lỗi theo nhóm tình huống sau train | Có thể cần sai số quỹ đạo offline và đánh giá closed-loop trong mô phỏng: hoàn thành tác vụ, va chạm/vi phạm, độ êm; đây là nhóm metric đề xuất, không phải bảng metric nội bộ Tesla |
| Phần có thể giữ khi mở rộng RAV-26 | Nhập dữ liệu, truy xuất nguồn, chống trùng, duyệt k mẫu, lịch sử phản hồi | Giữ luồng curation; thay schema mẫu, scorer, nhãn và evaluator theo tác vụ |

### 9.3. Ví dụ phân biệt giá trị dữ liệu

**Ví dụ minh họa:** một người đi bộ đứng sát mép đường. Với detection, ta kiểm tra model có phát hiện đúng người đó và đặt box đúng không. Với lái xe, dù box đúng, hệ thống vẫn phải hiểu diễn biến: người đó có đang bước xuống đường, bị che khuất trước đó, hoặc tương tác với xe khác không; từ đó lựa chọn hành vi phù hợp.

Một frame có thể đủ để chứng minh lỗi detection. Để xem quyết định lái xe có phù hợp, thường cần cả đoạn thời gian và trạng thái xe. Vì vậy, tăng mAP không tự động chứng minh tăng chất lượng lái xe. Ngược lại, một tình huống đáng thu thập cho hành vi lái xe có thể không tạo bất đồng lớn giữa hai detector.

### 9.4. Vì sao không điền “Tesla chạy tối đa YOLO bao nhiêu M”?

- Chip Tesla là kiến trúc chuyên dụng; hệ số thực nghiệm Orin NX ở mục 5 không được hiệu chuẩn cho nó.
- Tính toán sequence/multi-camera và các biểu diễn dùng chung không tương đương tổng nhiều lần detection độc lập trên ảnh 640×640.
- Chưa biết số tham số, tần suất, precision và cách phân bổ tài nguyên của đúng phiên bản FSD.
- Thiết kế dự phòng có thể lặp cùng công việc thay vì cộng tài nguyên để chạy model lớn hơn.

Do đó, đưa Tesla vào bảng với trạng thái “chưa đủ dữ kiện định lượng” là giới hạn của bằng chứng. Đây không phải nhận định rằng Tesla yếu hoặc mạnh hơn một model YOLO cụ thể.

### 9.5. Hệ quả cho RAV-26

Giả thuyết MVP vẫn là: dưới ngân sách duyệt cố định, tham chiếu offline giúp tìm nhiều lỗi detection hữu ích của mô hình mục tiêu hơn các baseline. Kết quả có thể chứng minh giá trị curation cho tác vụ perception đã chọn, chưa chứng minh cải thiện toàn bộ lái xe end-to-end.

Đề bài RAV-26 rộng hơn MVP. Thiết kế module cho phép sau này thay detector bằng mô hình sequence hoặc policy, nhưng mở rộng đó cần dữ liệu và evaluator khác; không chỉ thay checkpoint YOLO bằng model lớn hơn.

## 10. Quyết định tạm thời

- Dùng YOLO11s và YOLO11m làm hai mức mục tiêu; YOLO11x làm tham chiếu offline đầu tiên.
- Các model chỉ là module thử nghiệm, có thể thay thế độc lập với giao diện duyệt và bộ đánh giá.
- Chưa tuyên bố mô phỏng đúng model của một hãng hoặc chứng minh chạy thời gian thực trên thiết bị chưa đo.
- Bảng ngân sách dùng để thu hẹp lựa chọn, được cập nhật khi có benchmark đúng phần cứng.

## Nguồn tham khảo

Các nguồn đã được đối chiếu trong phiên khảo sát ngày 27/09/2026. Với sản phẩm nhiều đời, chỉ áp dụng thông tin cho cấu hình nêu trong nguồn.

- [S1 — Ultralytics: YOLO11, thông số model][S1]
- [S2 — Ultralytics: DeepStream trên Jetson, benchmark YOLO11][S2]
- [S3 — Unitree: G1-Comp][S3]
- [S4 — Unitree: G1-D][S4]
- [S5 — Volvo: nâng cấp máy tính EX90][S5]
- [S6 — NVIDIA: DRIVE AGX][S6]
- [S7 — NIO: ET7 và cấu hình Adam][S7]
- [S8 — ALWOD, 2023][S8]
- [S9 — Offboard 3D Object Detection from Point Cloud Sequences, 2021][S9]
- [S10 — VinFast: công bố lựa chọn chip NVIDIA, 2021][S10]
- [S11 — VinFast: chương trình robotaxi với Autobrains và NVIDIA, 2026][S11]
- [S12 — Tesla: FSD Computer, Hot Chips 2019][S12]
- [S13 — Tesla: Service Mode Release Notes, các cấu hình AI4][S13]
- [S14 — Tesla: Q1/2025, hướng vision-only và end-to-end][S14]
- [S15 — Tesla: giải thích hệ thống FSD Supervised][S15]
- [S16 — Tesla: Q3/2024, tăng số tham số FSD 12.5][S16]

[S1]: https://docs.ultralytics.com/models/yolo11/
[S2]: https://docs.ultralytics.com/guides/deepstream-nvidia-jetson
[S3]: https://www.unitree.com/robocup/
[S4]: https://www.unitree.com/G1-D/
[S5]: https://www.volvocars.com/us/support/topic/47d2c97fd33effd3c0a8cc3718c999b7-ee74549933d85020c0a83205327890cd-8664b2fa77a7e089c0a8296870d1a409/
[S6]: https://developer.nvidia.com/drive/agx
[S7]: https://www.nio.com/news/nio-launches-first-autonomous-driving-model-et7
[S8]: https://arxiv.org/abs/2309.07914
[S9]: https://arxiv.org/abs/2103.05073
[S10]: https://vinfastauto.com/vn_vi/cong-nghe-chip-nvidia-tren-o-to-dien-thong-minh-tu-hanh
[S11]: https://vinfastauto.com/vn_vi/vinfast-autobrains-va-nvidia-trien-khai-chuong-trinh-robotaxi-cap-do-4-ung-dung-agentic-ai-dau-tien-tai-dong-nam-a
[S12]: https://old.hotchips.org/hc31/HC31_2.3_Tesla_Hotchips_ppt_Final_0817.pdf
[S13]: https://service.tesla.com/docs/Public/ServiceMode/release-notes.html
[S14]: https://ir.tesla.com/_flysystem/s3/sec/000162828025018851/tsla-20250422-gen.pdf
[S15]: https://www.tesla.com/fsd-evidence-dashboard
[S16]: https://ir.tesla.com/_flysystem/s3/sec/000162828024043432/tsla-20241023-gen.pdf
