# sources — video nào có trong catalog

Contract: [CONTRACTS §5 dòng 1](../../../../../design/CONTRACTS.md), pool và id toàn cục ở §13; upload/storage/preparation hiện hành ở §19–20 và [architecture §5.1](../../../../../architecture_diagram.md).

- `local_folder` đọc catalog video local; upload lưu raw/catalog, storage S3 giữ raw bền theo CONTRACTS §19–20.
- Source không decode hoặc tính feature. Upload/GET/mở trang không tự chạy DINO/CLIP; hai preparation explicit và readiness độc lập theo §20.
- Chọn video vào phiên cần DINO+pHash sẵn; recipe dùng CLIP cần thêm cache CLIP. Local/CLI preparation giữ theo cấu hình explicit (§20).

**Thêm implementation:** class `@register("source")` trong `src/rav/components/sources/`, trả `VideoRef` (§2).

**Test tối thiểu:** thư mục tạm có 2 file video giả → đúng 2 `VideoRef`, `id` ổn định giữa hai lần chạy.

**Ý tưởng:** source adapter mới cho catalog từ xa. S3 transport hiện có ở §19 không đồng nghĩa source adapter S3 hoặc multi-user đã có; seed BDD/system là target pilot §21.
