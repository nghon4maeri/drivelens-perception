# Tài liệu RAV-26

## Nguồn sự thật (quyết định #25)

| Cấp | File | Vai trò |
|---|---|---|
| 1 — sản phẩm | `PRODUCT_STORY.md`, `design/RAV26_ALIGNMENT.md`, `research-pack/06_project_state/01_confirmed_decisions.md` | Làm gì, vì sao, đã chốt gì |
| 2 — thiết kế | `design/CONTRACTS.md` (interface) + `architecture_diagram.md` (tầng, luồng, API) + `design/UI_DESIGN.md` (giao diện web) | **Code phải theo** |
| 3 — dẫn xuất | `components/*` (chỉ trích dẫn CONTRACTS), `../PLAN.md` (tiến độ), code | Theo cấp 2 |

Muốn code khác cấp 2 → sửa docs cấp 2 trước (cùng commit), hỏi người dùng nếu đổi thiết kế. Không tự cắt phạm vi contract (#26).
Luật cho agent: `../AGENTS.md`.

## Đọc gì trước

0. `PRODUCT_STORY.md` — câu chuyện sản phẩm ngắn, mỗi đoạn trỏ tới tài liệu nguồn.
1. `../PLAN.md` — scope MVP và checklist lát cắt.
2. `design/CONTRACTS.md` + `architecture_diagram.md` — thiết kế code phải theo.
3. `research-pack/06_project_state/01_confirmed_decisions.md` — các quyết định đã chốt.

## Cấu trúc

| Thư mục / file | Nội dung | Trạng thái |
|---|---|---|
| `design/LOCAL_SINGLE_USER_LIMITS.md` | ⚠️ Giả định 1 người dùng/local và việc phải làm trước khi host | **Đọc trước khi deploy** |
| `design/RAV26_ALIGNMENT.md` | Checklist alignment với đề bài RAV-26 gốc | **Rà trước mỗi mốc** |
| `design/CONTRACTS.md` | Kiểu dữ liệu, extension point, luồng vòng đề xuất, đổi recipe, command layer cho agent | Đang dùng |
| `design/DEPLOYMENT_PILOT_SPEC.md` | Spec pilot 2–3 tài khoản cấp thủ công: quyền private/system, API/worker, S3/job/quota, PII export/report, seed/migration, vận hành và acceptance | **Để duyệt**; yêu cầu #79 đã chốt, thiết kế chi tiết/D0–D8 chưa implement |
| `backlog/DEPLOYMENT_EXECUTION_PLAN.md` | Rà docs thiếu, ranh giới phải chốt chung, task/file ownership cho subagent, DEP0–DEP8, task brief/test/gates | **PL6, draft D0**; không permission code/deploy |
| `design/DEPLOYMENT_IDENTITY_DATA_SPEC.md` | Principal/ownership/Postgres, readonly template + phiên riêng trống, seed/migration/RESTRICT | **Draft D0**, hai lựa chọn sản phẩm #83 đã chốt; interface còn cần duyệt |
| `design/DEPLOYMENT_RUNTIME_STORAGE_SPEC.md` | Direct S3 upload/media, queue/quota atomic, worker/attempt/fencing, Kaggle cutover/recovery | **Draft D0**, numeric policy chờ measurement |
| `design/DEPLOYMENT_UI_EXPORT_OPS_SPEC.md` | API/UI states, snapshot PII/report/confirmation, env/build/runbook và acceptance fixtures | **Draft D0**, không claim cloud/quality gates đã đạt |
| `design/UI_DESIGN.md` | Thiết kế giao diện web: bố cục, điều hướng, màn hình, token, thành phần dùng chung, cách giữ state | Đang dùng |
| `components/` | Hướng dẫn viết từng kind component, khớp 1–1 với `src/rav/components/` và CONTRACTS §5 | Đang dùng |
| `IMPLEMENT_GUIDE_V2.md` | Read-order, task/file ownership, lệnh và tự kiểm hiện hành; bản khởi tạo lại MVP đã archived | Đọc theo AGENTS |
| `architecture_diagram.md` | Kiến trúc hệ thống, luồng vòng đề xuất, trực quan hoá (deliverable BTC, giữ đúng đường dẫn) | Đang dùng |
| `backlog/` | Ý tưởng chưa lên lịch (vd rà nhánh chưa gộp 2026-10-03: B1–B5, cảnh báo số mAP giả ở `nam-init`) | Tham khảo, chưa phải thiết kế |
| `design/PHASE1_DATASET_SNAPSHOT.md` | Chi tiết schema local và snapshot/dataset được CONTRACTS §14 dẫn; slices S1–S8 đã triển khai, S11 cần BDD | Chi tiết tham chiếu local; tiến độ theo PLAN |
| `setup/CLOUD_STORAGE.md` + SQL jobs C1 | Runbook S3/Supabase/Kaggle của local runtime đang chạy | Đang dùng local; chưa là multi-user deployment |
| [setup/DEPLOYMENT_STATUS.md](../../../setup/DEPLOYMENT_STATUS.md) | Bàn giao hosting IrishCoffee/T026: URL, images, env, update/rollback, monitor/quota và các blocker | Snapshot 2026-10-11, FE/API gộp Live; chưa mở đầy đủ pilot |
| [setup/DOCKER_IMAGE_RELEASE.md](setup/DOCKER_IMAGE_RELEASE.md) | Build/smoke/push Docker Hub private và deploy image Render | Hai image cũ, phục vụ rollback |
| [setup/COMBINED_WEB_RELEASE.md](../../../setup/COMBINED_WEB_RELEASE.md) | FE/API chung image, rollout/monitor/suspend và rollback | #107 Live; API riêng suspended, worker/cloud E2E còn thiếu |
| [setup/PILOT_RUNTIME_RUNBOOK.md](../../../setup/PILOT_RUNTIME_RUNBOOK.md) | Migration/roles/policy, worker, GPU, import và smoke pilot | Hướng dẫn; gate cloud chưa hoàn tất theo DEPLOYMENT_STATUS |
| `evaluation/V2_CRITERIA.md` | Giao thức, nhãn thị giác và kết quả kiểm V2 theo câu tìm; tách đúng contract khỏi chất lượng ngữ nghĩa | Đã đánh giá, ngữ nghĩa chưa đạt |
| `research-pack/` | Đề bài gốc, cách hiểu đã chốt, paper và decision log; OSS/market audit cũ sang archive | Sản phẩm/nguồn/bằng chứng theo nhãn từng file |
| [archived/README.md](../../README.md) | Plan đã thay thế, PRD origin, audit snapshot và Technical Book/khung từ template; có lý do + link thay thế | **Lịch sử/tham khảo, không contract hiện hành** |

Deliverable khác nằm ngoài `docs/`: `README.md`, `JOURNAL.md`, `WORKLOG.md`, `eval/results/report.md`, `presentation/`.

## Quy ước

- Thêm giả định chỉ đúng khi chạy local → thêm dòng vào `design/LOCAL_SINGLE_USER_LIMITS.md` + comment `# local-only:` trong code.
- Quyết định mới → thêm vào `research-pack/06_project_state/01_confirmed_decisions.md`.
- Thay đổi contract → sửa `design/CONTRACTS.md` / `architecture_diagram.md` trước (cùng commit với code), hỏi người dùng nếu đổi thiết kế.
- Theo yêu cầu người dùng PL6, docs lỗi thời/đã thay thế chuyển vào `archived/`, cập nhật index và links; giữ decision/evidence/proposal tương lai, không tự bỏ scope.
