# UI design — deployment pilot

Cấp thiết kế frontend; đọc cùng [CONTRACTS §21.16/19/20](CONTRACTS.md) và [architecture](../architecture_diagram.md).
Cập nhật #133: direct proposal và tự checkpoint batch trước. [Local UI/full history](../archived/2026-10-13/docs/design/UI_DESIGN.md).

## 1. Phạm vi và điều hướng

Combined Render phục vụ landing `/`, `/landing`, login `/login`, app `/app`; login Supabase email/password do operator cấp.
Chưa auth → login với next cùng origin; redirect giữ hash, next ngoài app không được dùng. Runtime apiUrl rỗng.
Trong app dùng hash: #videos, #templates, #new, #s=ID&t=studio|archive|analysis|export;
#operations chỉ tài khoản có operations_read. #models (§21.33): mọi người xem catalog model SYSTEM, form upload chỉ khi models_write.
Dataset/exports global còn ẩn trong pilot tới lát phù hợp.
Template/history shared chỉ đọc; fork tạo phiên riêng trống cùng nguồn/cấu hình, không auto propose.
Backend auth/owner là authority; ẩn nút không thay kiểm quyền. Logout/expiry reload xóa state, auth_generation bỏ response cũ.

Landing DriveLens (2026-10-18): chỉ ảnh tĩnh đóng gói trong bundle, không gọi API/dữ liệu private. Gồm ảnh chụp Studio thật
(biển số đọc được làm mờ), frame từ một snapshot export thật đã che PII và sơ đồ kiến trúc SVG; mốc thời gian/frame/vùng PII
lấy nguyên từ manifest export. Ghi nguồn video bên thứ ba. Strategy, quy trình, phím tắt phải khớp catalog và Studio thật.
Không dùng số liệu hiệu quả chưa đo, không hứa PII 100% hoặc tuân thủ pháp lý; nói rõ detector nhẹ có thể sót/nhận nhầm.
Header phải dùng được ở mobile/tablet, chỉ hiện đầy đủ menu khi đủ chiều rộng.

## 2. Studio

Lưới ảnh và phím tắt là trọng tâm; focus/keep/drop/clear có chữ/icon, không chỉ màu. Chi tiết ở panel/tooltip,
giải thích cấp batch, số backend thiếu không invent. State server có version/CAS, retry lỗi rõ.
Keep/drop/clear/undo chỉ draft RAM; bấm Đề xuất chốt batch hiện tại rồi nhận batch mới trực tiếp (§21.26).
Không nút Lưu phiên, không polling/job banner cho direct PROPOSE; lỗi giữ draft hoặc áp dụng checkpoint đã commit.
Reload/rời phiên/logout có thể mất batch tạm mới nhất. YOLO/phân tích/snapshot tự checkpoint trước; local v1 giữ autosave.
Service bận trả429 Retry-After. Legacy queue chỉ dùng khi chưa cấu hình interactive.
YOLO/analysis dùng nút explicit và kết quả gắn proposal/context/model; reload không tự tạo job mới.
Không hiển thị mAP/accuracy nếu thiếu ground truth; recipe thiếu provenance/feature hiển thị lý do.

## 3. Kho dữ liệu và media

Upload direct S3 có tiến độ truyền riêng với chờ worker READY. Không chạy DINO/CLIP ngầm khi upload.
Nguồn cũ NOT_PREPARED có nút Chuẩn bị ảnh explicit; toolbar hoàn thành phải refresh ảnh không cần reload trang.
Thumbnail/frame READY đọc URL S3 đã ký; thiếu ảnh không tự decode hay xếp job trên GET.
Context clip ±3 giây còn job RENDER_MEDIA/cache raw. HTTP media concurrency giữ suất lúc request,
PENDING polling không chiếm suất toàn thời gian, ưu tiên frame/clip đang focus; refresh URL hết hạn theo quyền.
Theo người dùng 2026-10-16: mở chi tiết frame (gồm Mẫu đã lưu) hiển thị ảnh phóng to trước, không tự phát hoặc
request clip. Play mới yêu cầu clip/phát; giữ ảnh hoặc thumbnail đã có trong lúc clip tải/buffering/lỗi,
chỉ thay ảnh bằng video khi video thực sự phát được. Có nút quay về ảnh gốc; phím Space tương đương Play/Pause.
Xóa nguồn phải qua RESTRICT/CAS, DELETE_PENDING và job; lỗi có retry sau đối soát, không báo xóa sạch trước receipt.

## 4. Vận hành và tính năng hướng tới

CPU không hạn mức theo policy live #133; GPU còn quota. CPU usage vẫn số đo telemetry; estimate dễ hiểu thuộc N2. Operations chỉ đọc trace/heartbeat/slot/quota,
không cấp quyền xem raw private người khác hoặc điều khiển job từ operator screen.
Dataset/model binding, report theo model tham chiếu và export PII chưa có flow pilot đầy đủ.
Export chỉ mở khi snapshot/preview/report/owner-confirm/quality gate đúng CONTRACTS §21.8; không dùng export local thay thế.
Giao diện/chủ đề/component styles đang có giữ nguyên; layout local chi tiết tra archive khi sửa shared frontend.

## Interactive #133 — ưu tiên CONTRACTS §21.26

Pilot direct mode: giữ/bỏ ở RAM browser; bấm Đề xuất lưu batch hiện tại rồi trả batch mới.
Đề xuất trả cùng HTTP response, không banner job/polling. Retry giữ request id; bận429 Retry-After.
YOLO/phân tích/snapshot tự checkpoint trước khi chạy. Reload có thể mất batch tạm mới nhất; batch đã chốt vẫn phục hồi.

Điều chỉnh #133: Đề xuất tự lưu batch hiện tại, không nút Lưu phiên; giữ/bỏ vẫn local.
Batch mới tạm tới lượt tiếp; khi chạy YOLO/phân tích/snapshot tự checkpoint trước.

## Nhật ký admin (2026-10-17)

Trang Vận hành hiển thị bảng log có filter nguồn/lỗi/cửa sổ thời gian và phân trang thay trang theo CONTRACTS §21.19. Resource IDs mở timeline sẵn có; log partial/tracing tắt/lỗi tải phải rõ, không nhúng sandbox Grafana anonymous.

Nhật ký admin hiển thị Dùng cache/Tính mới khi event có cache_hit; timeline worker.export.* phân biệt đọc frame, PII và lưu preview theo CONTRACTS §21.19.

Kho mẫu admin có Upload mới / Chọn từ kho của tôi theo CONTRACTS §21.38. File input giữ native picker,
nhưng nút chọn file có nền/viền, khoảng đệm và trạng thái focus/hover/disabled rõ trong mọi theme.
Hai lựa chọn thêm video hiển thị trạng thái đang chọn rõ ràng; nút phụ có viền, nút tạo bản sao dùng màu
primary. Danh sách kho riêng có thumbnail lazy qua media hiện có (không tải video), thời lượng/số frame,
tên giới hạn dòng và tooltip tên đầy đủ; nguồn chưa có ảnh hiển thị placeholder. Giữ readiness/disabled hiện có.
Theo §21.39, thao tác mới đổi thành "Thêm vào kho mẫu": tham chiếu chỉ đọc, không sao chép file hoặc xếp job.
Giải thích nguồn gốc bị khóa xóa khi dùng làm mẫu; mẫu mới vẫn ẩn để admin xuất bản. Job copy cũ chỉ xem/hủy,
không có nút retry copy.
Studio PROPOSE (§21.40) có chi tiết bước chạy: tên dễ hiểu, thời gian đo được, cache khi biết, counter của bước;
không bịa %/ETA. Khi hủy nói "đang chờ tiến trình dừng an toàn", không gọi chung là "dọn".

Login (§21.41) thêm thẻ "Trải nghiệm nhanh" và nút "Nạp tài khoản demo" có viền/nền rõ, disabled khi đang
xác thực; điền email/mật khẩu và chuyển về tab đăng nhập, không tự submit. Hiển thị tài khoản demo công khai,
ghi rõ tài khoản/quota dùng chung, dùng dữ liệu mẫu; không có bước đăng ký/gửi email khi bấm nút.
