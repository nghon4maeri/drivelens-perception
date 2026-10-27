# Hướng dẫn thực thi hiện hành — RAV-26

Cập nhật PL6, 2026-10-09. Guide này giữ đường dẫn đọc bắt buộc trong AGENTS; kế hoạch khởi tạo lại MVP đã chuyển sang [archive](../../planning/IMPLEMENT_GUIDE_V2_ORIGINAL.md).

## 1. Đọc trước khi làm

1. [AGENTS.md](../../../../AGENTS.md), [PLAN.md](../../../../PLAN.md): xác định đúng **một lát** đang làm.
2. [docs/README.md](../../../README.md): thứ bậc nguồn sự thật.
3. [CONTRACTS](../../../design/CONTRACTS.md), [architecture](../../../architecture_diagram.md), [UI_DESIGN](../../../design/UI_DESIGN.md) ở mục liên quan.
4. Task/spec của lát; với deployment, bắt đầu từ [execution plan](../../../backlog/DEPLOYMENT_EXECUTION_PLAN.md).

CONTRACTS/architecture/UI là thiết kế code phải theo. Spec deployment chi tiết còn **draft D0**, không tự dùng như thiết kế đã duyệt.
Không chạy lại các bước tạo nhánh/cherry-pick/xóa code trong guide lịch sử. Local limits vẫn có hiệu lực cho tới khi gate deployment đạt.

## 2. Giao task và làm việc

- Docs trước, code sau; đổi thiết kế phải hỏi người dùng. Quyết định mới ghi vào decision log.
- Một phiên một lát PLAN; mỗi subagent có task, input/output, allowed files và expected tests rõ ràng.
- Một integrator giữ schema/RPC/DTO/bootstrap; chỉ một FE integration owner sửa api.ts/App/navigation.
- Thiếu signature, dữ liệu hoặc policy thì báo đúng blocker; không tự hạ scope, sinh metric giả hoặc fallback bỏ kiểm quyền.
- Phiên mẫu shared chỉ đọc để học; bắt đầu từ mẫu chỉ tạo phiên riêng trống cùng nguồn/cấu hình, không copy lịch sử/kết quả hoặc tự đề xuất (#83). Phiên thao tác, lịch sử và kết quả của user riêng. Xem mẫu không tạo quyền đọc phiên private của người khác.
- Dùng S3/Supabase/Kaggle hiện có; credential bootstrap trong `.env` repo được operator chỉ định explicit, không in/copy secret vào image/seed/FE. Preflight kiểm target/namespace và config/role thiếu, không tự tạo tài nguyên mới hoặc fallback bỏ quyền.
- Chỉ sửa file task được giao. Không đụng hooks/logging scripts, không push nếu chưa được yêu cầu.

## 3. Môi trường và kiểm tra

Windows, từ repo root, chạy trong **mỗi terminal** trước Python/npm/test:

```powershell
. .\scripts\use_project_storage.ps1
python -m pytest tests -q --basetemp=output/pytest-tmp
python -m ruff check src tests
```

Nếu sửa frontend, terminal riêng từ repo root:

```powershell
. .\scripts\use_project_storage.ps1
Set-Location -LiteralPath 'src/web'
npx tsc --noEmit
npm run build
```

Cache/model/temp/output trong data/ hoặc output/, không tạo mới trên C. Đường dẫn có dấu: đọc ảnh bằng PIL, ghi bằng cv2.imencode + Path.write_bytes.
Không tự đổi pagefile. Nếu TestClient bị sandbox chặn socketpair, xử lý quyền chạy đúng test; không sửa app để né test.

Docs-only: kiểm link, schema/DTO/task/gate consistency và git diff --check; không chạy GPU hoặc test app nặng chỉ vì sửa tài liệu.
Mock không chứng minh RLS/transaction/race/cloud/PII quality; deployment phải có evidence của đúng gate và môi trường.

## 4. Tự kiểm trước báo xong

- Thiết kế thay đổi đã được duyệt và cập nhật SoT trước code? Có tự cắt contract chưa được phép không?
- Mọi caller/route/test/config cần nối đã được xử lý? Giữ local regression và core/pixel/hash semantics?
- Đã đọc output kiểm tra mới nhất và báo giới hạn thật? Không dùng test xanh để claim chất lượng thuật toán chưa đo.
- Diff/staged chỉ chứa việc của lát, giữ thay đổi sẵn có của người dùng; commit tiếng Việt không dấu, tick PLAN, dừng; chưa push.

Chạy local và cấu hình cloud local: [README](../../../../README.md), [CLOUD_STORAGE](setup/CLOUD_STORAGE.md). Deployment chưa được triển khai bởi lát PL6.
