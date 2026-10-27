> Người dùng xác định 2026-10-02 (bản gốc: `data_curation_project_focus.md`). **Chưa đồng bộ** vào PRODUCT_STORY / CONTRACTS / architecture / UI — sẽ làm sau buổi trình bày với mentor.

# Data Curation Copilot — Phạm vi và selling point

## 1. Các quyết định đã chốt

| Nội dung | Quyết định |
| --- | --- |
| Nhiệm vụ | Detection 2D từ dashcam; danh sách lớp sẽ chốt theo thí nghiệm |
| Mục tiêu huấn luyện | Sửa một điểm yếu cụ thể, đồng thời giữ hiệu năng chung |
| Lợi ích chính | Giúp kỹ sư duyệt nhanh hơn nhờ đề xuất có bằng chứng rõ |
| Model dự kiến | Họ YOLO; chưa chốt phiên bản, kích thước và checkpoint |
| Dataset dự kiến | BDD100K — cách hiểu của tên “bdd100” người dùng nêu |
| Điều kiện hiện có | Có dataset đã gán nhãn để giả lập chọn mẫu, có model/checkpoint và khả năng train lại |
| Đơn vị hạ tầng | Frame/sequence; frame là sequence dài một frame |
| Quyền quyết định | Kỹ sư duyệt trước khi gửi annotation/train |

## 2. Định vị dự án

**Workbench giúp kỹ sư chọn dữ liệu để sửa một điểm yếu của detector dashcam, với đề xuất có bằng chứng dễ kiểm tra để rút ngắn thời gian thẩm định.**

Selling point ưu tiên: giảm công duyệt mà vẫn giữ chất lượng quyết định. Cải thiện mô hình là kết quả cần kiểm chứng riêng; chưa tuyên bố duyệt nhanh hơn, tiết kiệm annotation hoặc tăng mAP khi chưa đo.

### Phạm vi năng lực phục vụ

Workbench tìm dữ liệu phù hợp với **khoảng trống năng lực quan sát được**, không phân hạng theo số lần hay mức độ finetune.

| Tình trạng quan sát được | Ưu tiên curation |
| --- | --- |
| Model chưa phù hợp miền dashcam | Mẫu gán nhãn được rõ, đại diện cho miền và có dấu hiệu lỗi lặp lại |
| Model làm tốt phần lớn dữ liệu | Các nhóm điểm yếu cụ thể và phần độ phủ còn thiếu |
| Model còn lỗi ở điều kiện khó | Biến thể khó nhưng vẫn tạo được ground truth đáng tin |

Các nhu cầu này có thể chồng lấp. Mẫu không quá khó với con người vẫn đáng xem nếu model không xử lý tốt. Tuy nhiên, nhìn rõ chỉ hỗ trợ tính khả dụng nhãn; trước khi quy lỗi cho thiếu dữ liệu cần kiểm tra phạm vi lớp, preprocessing, ánh xạ nhãn và ngưỡng inference.

Ba mức thành công cần báo riêng: **tìm ứng viên đáng xem** (ảnh/clip và prediction), **xác nhận điểm yếu** (ground truth và lỗi lặp lại trên nhóm), **chọn dữ liệu giúp sửa lỗi** (cải thiện trên đánh giá độc lập sau train). Thành công chẩn đoán chưa chứng minh hiệu quả huấn luyện.

## 3. “Mẫu đáng giữ” nghĩa là gì?

Kỹ sư đánh giá ba câu hỏi:

1. Mẫu có phù hợp mục tiêu phiên chọn không?
2. Có đủ thông tin để gán nhãn theo yêu cầu không?
3. Có bổ sung điều gì so với các mẫu đã có không?

Giá trị trước annotation là đóng góp dự kiến, phụ thuộc mục tiêu và tập hiện có. Độ hiếm, uncertainty, diversity và coverage là căn cứ đề xuất; không bảo đảm một mẫu sẽ tăng mAP.

VLM có thể nhận xét tổng quan batch, nhưng phải được cung cấp mục tiêu, quy tắc nhãn và dữ liệu đối chiếu. Phần nhận xét hình ảnh là đánh giá cần kiểm tra; số lượng, quota và phép so trùng phải lấy từ hệ thống. Chỉ nhìn batch không đủ để kết luận mức bổ sung vào toàn dataset hoặc lỗi của model.

## 4. Hai phép kiểm chứng riêng

### A. Selling point chính: duyệt nhanh với chất lượng được giữ

So sánh duyệt có bằng chứng với duyệt không có bằng chứng. Giữ cùng pool ứng viên, tiêu chí và cách bố trí; chia các batch tương đương, hoán đổi thứ tự/điều kiện giữa người duyệt để hạn chế hiệu ứng nhớ.

| Metric | Cách tính / quy ước |
| --- | --- |
| Thời gian duyệt | Tổng thời gian thao tác và xem ngữ cảnh / số mẫu đã quyết định; báo thêm thời gian từng batch |
| Giảm thời gian (%) | 100 × (thời gian không bằng chứng − có bằng chứng) / thời gian không bằng chứng |
| Tỷ lệ giữ không phù hợp | Số mẫu giữ nhưng thẩm định độc lập kết luận không phù hợp / tổng số mẫu giữ |
| Tỷ lệ bỏ sót mẫu phù hợp | Số mẫu bỏ nhưng thẩm định độc lập kết luận phù hợp / tổng số mẫu được thẩm định là phù hợp |

Thẩm định độc lập dùng cùng mục tiêu, tập đối chiếu và rubric ba câu hỏi ở mục 3; người thẩm định không thấy điều kiện thử nghiệm hay nhận xét VLM. Mẫu chưa đủ thông tin được ghi riêng, không ép thành đáp án chắc chắn. Quyết định curation không luôn có một đáp án duy nhất; đây là chuẩn đối chiếu đã thống nhất, không phải ground truth về giá trị huấn luyện.

Ghi thời gian chờ hệ thống riêng và thời gian hoàn thành tổng thể. Báo kết quả theo từng người duyệt và độ biến động, không chỉ một số trung bình.

- Tỷ lệ Keep cao hoặc đồng thuận cao tự nó chưa chứng minh chất lượng tốt.
- Tính tỷ lệ giảm thời gian từ kết quả đo; ngưỡng thành công cần chốt trước thử nghiệm.

### B. Mục tiêu dữ liệu: sửa điểm yếu, bảo toàn năng lực chung

So sánh model ban đầu, model bổ sung dữ liệu Random và model bổ sung dữ liệu Curation. Giữ cùng ngân sách frame được gán nhãn, checkpoint và cấu hình training; lặp thí nghiệm để báo độ biến động.

| Metric | Cách tính / quy ước |
| --- | --- |
| AP mục tiêu | AP@[.50:.95] của lớp mục tiêu trên nhóm điều kiện đã chốt |
| mAP chung | mAP@[.50:.95] trên các lớp đã thống nhất, cùng tập đánh giá |
| Uplift so với ban đầu | AP hoặc mAP Curation − model ban đầu |
| Lợi thế chọn mẫu | AP hoặc mAP Curation − Random ở cùng ngân sách |
| Kiểm tra suy giảm | AP/mAP sau bổ sung − ban đầu trên tập chung và các nhóm cần giữ |

Báo AP/mAP trên thang 0–100; chênh lệch là **điểm**, không gọi nhầm là phần trăm tăng tương đối. Báo số ảnh và số đối tượng của từng nhóm. AP tổng hợp theo đường precision–recall; không thay bằng một phép đo recall tại một ngưỡng confidence.

- Tập đánh giá cuối phải độc lập; dùng tập chẩn đoán/validation riêng để chọn điểm yếu và tinh chỉnh chính sách.

Giả lập annotation: ẩn ground-truth boxes của pool khi chọn, chỉ mở nhãn sau khi quyết định. Không dùng nhãn bị ẩn để tạo bằng chứng cho agent/người duyệt. Giữ tách biệt các chuyến đi/sự kiện khi có thông tin nguồn.

### C. Notebook chẩn đoán trước khi chọn điểm yếu

**Giả thuyết khảo sát ban đầu:** detector yếu với người đi bộ ban đêm; kiểm tra thêm kích thước đối tượng và che khuất. Chưa coi đây là điểm yếu đã xác nhận của YOLO.

Notebook chỉ cần inference và phân tích lỗi, chưa cần train lại:

1. Kiểm tra ánh xạ lớp BDD100K–checkpoint và cấu hình inference.
2. Đo AP theo lớp/ngày–đêm; phân tích thêm nhóm kích thước và che khuất với quy tắc đánh giá rõ ràng. Khi lọc đối tượng, cần xử lý đối tượng ngoài nhóm theo quy tắc ignore của evaluator, không tùy tiện xóa nhãn rồi tính prediction tương ứng thành false positive.
3. Hiển thị prediction cạnh ground truth cho lỗi bỏ sót, nhầm lớp và định vị kém.
4. Chọn nhóm có lỗi rõ, đủ dữ liệu bổ sung và đủ mẫu đánh giá độc lập.

Recall chẩn đoán = TP / (TP + FN), với khớp một–một, đúng lớp và ngưỡng IoU/confidence cố định được ghi rõ. Đếm thêm false positives. Ngưỡng minh họa có thể là IoU=0,50, confidence=0,25; đây chưa phải ngưỡng vận hành đã chốt.

Kết quả nghiên cứu/model card có sẵn chỉ giúp đặt giả thuyết hoặc tìm checkpoint. Phải đo trên model của dự án; nhóm ban đêm kém hơn không tự chứng minh ánh sáng là nguyên nhân duy nhất.

### D. Tiêu chí kết luận

- **Selling point được hỗ trợ:** thời gian duyệt giảm, đồng thời chất lượng quyết định nằm trong mức chấp nhận đã chốt; không kết luận chỉ từ tỷ lệ Keep.
- **Mục tiêu sửa lỗi được hỗ trợ:** AP nhóm mục tiêu tăng so với ban đầu, còn tập chung/nhóm bảo toàn không giảm quá ngưỡng đã chốt.
- **Curation tốt hơn Random:** có lợi thế ở metric mục tiêu so với Random cùng ngân sách, báo cả độ biến động qua các lần thử.
- Ba kết luận độc lập: duyệt nhanh không mặc nhiên làm model tốt hơn; model tăng điểm không mặc nhiên chứng minh chính sách chọn tốt hơn Random.
- Ngưỡng thời gian, sai lệch chất lượng và suy giảm chấp nhận được sẽ chốt trước thử nghiệm chính thức, sau pilot; chưa đặt con số cam kết tùy ý.
- Diversity, coverage và tỷ lệ mẫu khó là số mô tả tập chọn, không thay thế AP/mAP. Với giả lập trên nhãn có sẵn, chưa có bằng chứng tiết kiệm công annotation thực tế.

## 5. Những điểm còn cần chốt

1. **Điểm yếu cụ thể:** dùng notebook khảo sát người đi bộ ban đêm và các yếu tố kích thước/che khuất, rồi chọn theo kết quả; chưa chốt điểm yếu trước khi đo.
2. **Danh sách lớp và model:** phiên bản YOLO, kích thước, checkpoint và dữ liệu đã dùng để train nếu biết.
3. **Ngân sách:** K=200 frame chỉ là cấu hình minh họa hiện tại; với sequence, đếm số frame thực sự cần nhãn.
4. **Phân chia dữ liệu:** train ban đầu, pool ứng viên, tập chẩn đoán và tập đánh giá cuối; chưa xác nhận sẵn có tập đánh giá độc lập.
5. **Tiêu chuẩn thành công:** mức giảm thời gian, mức chất lượng duyệt cần giữ, mức cải thiện nhóm mục tiêu và mức suy giảm chung chấp nhận được.

Chưa cần mở rộng chức năng. Bước tiếp theo là chọn và xác nhận một điểm yếu trên model/dataset dự kiến để biến định vị này thành thí nghiệm cụ thể.

## 6. Rà soát yêu cầu UI hiện tại (FR/NFR)

Đây là rà soát tài liệu được cung cấp, chưa phải kiểm tra implementation. Ưu tiên sửa tính đúng đắn và cơ sở bằng chứng, không mở rộng phạm vi sản phẩm.

### FR hiện có

| ID | Kết luận | Điều chỉnh cần thiết |
| --- | --- | --- |
| FR-01 | Sửa | Đề xuất tối đa 16 ứng viên phù hợp mục tiêu; DINOv2/FPS là implementation hiện tại, không phải định nghĩa giá trị. Xét tập đã giữ và nhu cầu toàn cục, không chỉ tản xa trong batch |
| FR-02 | Giữ, sửa trạng thái | Phân biệt Chưa duyệt / Giữ dự kiến / Bỏ / Đã lưu. Không mặc định cả 16 là đã được kỹ sư xác nhận |
| FR-03 | Giữ | Phím tắt chỉ tác động đúng ngữ cảnh; không kích hoạt khi nhập chat hoặc điều khiển player; có hoàn tác thao tác nhầm |
| FR-04 | Có điều kiện | Clip từ nguồn thật khi có; cửa sổ ±3s cắt theo biên video. Telemetry chỉ hiển thị khi có dữ liệu đồng bộ. Mô phỏng phải gắn nhãn, không dùng làm bằng chứng |
| FR-05 | Giữ, sửa | K là giới hạn frame duy nhất được chọn, không ép giữ mẫu kém để đủ đúng K. Chốt batch không vượt ngân sách; sequence tính theo frame cần nhãn và không đếm đôi frame chồng lấp |
| FR-06 | Giữ, định nghĩa lại | Nhận xét batch theo mục tiêu và bằng chứng có nguồn. Ghi “đóng góp độ phủ dự kiến”, không gọi là uplift model; cập nhật theo các mẫu thực sự đang chọn |
| FR-07 | Để nâng cao | Bốn slider và nhiều thuật toán không cần trên luồng duyệt chính. Thay đổi cấu hình không làm mất quyết định đang có; lưu cấu hình dùng để đề xuất |
| FR-08 | Giữ | Lịch sử là các mẫu đã lưu; Un-keep cập nhật ngân sách, số liệu và lần xuất sau |
| FR-09 | Giữ, sửa hợp đồng | Manifest chọn mẫu có ID, nguồn, timestamp/frame index, khoảng sequence, phiên bản và quyết định. Chưa có nhãn không thể gọi là bộ annotation COCO/YOLO hoàn chỉnh; PCA không bắt buộc cho pipeline annotation |

### Nội dung nên giảm hoặc bỏ khỏi luồng chính

- Bỏ hành động “Giữ Novelty ≥65%”: chưa có diễn giải/hiệu chuẩn ngưỡng, novelty không đồng nghĩa giá trị. Đề xuất của agent không tự trở thành phê duyệt.
- Trên card chỉ giữ ID/thời điểm, trạng thái và 1–2 bằng chứng liên quan mục tiêu. Tốc độ, novelty và pHash đưa vào chi tiết khi thực sự có ích.
- PCA và số cụm là công cụ phân tích phụ; không suy độ phủ ODD từ hình PCA hoặc mặc định 10 cụm có nghĩa nghiệp vụ.
- Không hiển thị số tiền tiết kiệm nếu chưa có mô hình chi phí/baseline đo được. ND-Rate phải có định nghĩa phép so trùng, ngưỡng và tập tham chiếu.
- Không kết luận “đường thẳng vô ích” hoặc “tạt đầu nguy hiểm” chỉ từ appearance. Nhãn kịch bản phân biệt nguồn đo, giả thuyết AI và xác nhận người duyệt.
- Dark/light và chat mở là ưu tiên sau; giữ phần tóm tắt có bằng chứng trước.

### FR còn thiếu để đáp ứng mục tiêu đã chốt

1. **Ngữ cảnh phiên:** mục tiêu, lớp/điều kiện ưu tiên, model/version và tập dữ liệu đối chiếu được hiển thị rõ.
2. **Kiểm tra bằng chứng:** xem prediction vùng liên quan; nếu lý do là trùng hoặc bổ sung, xem mẫu/thống kê tham chiếu. Nhãn ẩn không được dùng trong phiên giả lập.
3. **Trạng thái và phục hồi:** chưa duyệt không tự được commit; quyết định tạm khác dữ liệu đã lưu; giữ tiến độ khi tải lại hoặc lỗi, hoàn tác thao tác batch.
4. **Nguồn và xuất:** truy ngược frame/sequence về nguồn, xác định frame cần nhãn; xử lý thiếu clip/telemetry và export một phiên bản nhất quán.
5. **Đo selling point:** ghi sự kiện duyệt, xem clip và thời gian chờ để tính metric mục 4A. Chưa cần dashboard mới.

Đề gốc còn yêu cầu ẩn danh PII và ít nhất hai vai trò, nhưng tài liệu UI chưa thể hiện. Cần quyết định ranh giới: dữ liệu đã ẩn danh từ bước nhập hay Workbench chịu trách nhiệm; hai vai trò có quyền gì. Không mặc nhiên mở thêm một workflow phê duyệt mới.

### Phân loại và tiêu chí NFR

| Yêu cầu | Nhận xét / tiêu chí cần làm rõ |
| --- | --- |
| NFR-01 Latency | Giữ mốc <50ms cho phản hồi local của phím tắt; đo p95 trên máy/trình duyệt và tải xác định. Không áp cùng mốc cho tải clip, sampling hoặc API VLM |
| NFR-02 Cognitive load | Giữ; kiểm chứng bằng thời gian và chất lượng duyệt ở mục 4A, không chỉ bằng số màu/nút |
| NFR-03 Một nút Duyệt | Đây là ràng buộc UI, không phải “single source of truth” dữ liệu. Enter và nút click phải gọi cùng hành động commit |
| NFR-04 Persistence | Chuyển tab chỉ là mức tối thiểu; cần bảo toàn trạng thái qua reload/lỗi. Chỉ báo Đã lưu sau commit thành công |
| NFR-05 Responsive/dark | Tách riêng: responsive là thích ứng bố cục; dark/light là lựa chọn theme. Chốt kích thước desktop tối thiểu, không ép 4×4 khi ảnh quá nhỏ để thẩm định |
| Data integrity | Commit nguyên tử, gửi lại không nhân đôi; không vượt K, không đếm trùng; manifest khớp snapshot đã lưu |
| Traceability/explainability | Mỗi lý do có nguồn và phiên bản; số liệu có mẫu số/tập tham chiếu. Thiếu bằng chứng được ghi rõ; agent không phát biểu uplift dự đoán như kết quả đo |
| Throughput/resilience | Đo riêng thời gian nạp batch, mở clip và nhận VLM; VLM chậm/lỗi không chặn thao tác duyệt và không thay đổi quyết định đã lưu |
| Accessibility | Không dùng màu làm tín hiệu duy nhất; focus/trạng thái có icon/text; phím tắt không xung đột với nhập liệu/player |
| Privacy/evaluation integrity | PII được xử lý trước khi hiển thị/gửi API theo ranh giới đã chốt; không lộ nhãn ẩn hoặc dữ liệu đánh giá cuối cho chính sách chọn |

Không phải mọi mục NFR mới đều cần một màn hình hay công nghệ mới. Chúng là các thuộc tính cần kiểm tra của workflow hiện có.

## Tham chiếu

BDD100K có metadata về thời điểm, thời tiết, bối cảnh và thuộc tính occlusion; cần kiểm tra bản dữ liệu thực dùng. Các metadata này hỗ trợ xác định nhóm thí nghiệm, không chứng minh model đang yếu ở nhóm đó.

- [Định dạng dữ liệu BDD100K](https://github.com/bdd100k/bdd100k/blob/master/doc/source/format.rst)
- [Định nghĩa metric detection COCO](https://github.com/cocodataset/cocodataset.github.io/blob/master/dataset/detection-eval.htm)
