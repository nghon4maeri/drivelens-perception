# references — so với tập nào

Contract: [CONTRACTS §5 dòng 6](../../../../../design/CONTRACTS.md); Context ở §4/18; ngữ nghĩa dataset/model §14.0/16 và reference phiên §14.6.

- `session_kept` dùng unit đã keep trong phiên; vòng đầu rỗng xử lý theo §9.5. Recipe cũ không tự thêm dataset reference.
- V2 dùng tập train + kept cho các recipe bổ sung dữ liệu theo §18; dataset versions được verify, đóng băng trong reference phiên (§14.6).
- Ảnh reference không trở thành candidates hoặc thay pool/state. Dataset là tập train/finetune do người dùng khai báo với checkpoint (§14.0/16), không mặc định là validation/test hoặc ground truth của pool.

**Thêm implementation:** class `@register("reference")` trong `src/rav/components/references/`.

**Test tối thiểu:** state có kept = {a, b} → trả đúng 2 unit; recipe V2 kiểm training reference không lọt pool, cùng FieldKey và source/version integrity theo §18.

**Ý tưởng:** vòng nhập nhãn/model sau retrain cho Active Learning (ALIGNMENT A7). Dataset tham chiếu hiện có không đồng nghĩa app đã có training pipeline hoặc bằng chứng uplift.
