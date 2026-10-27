# Bàn giao Antigravity — hoàn thiện luồng cơ bản

Ngày 2026-10-13. Chỉ tiếp tục lát **DEP7-Basic-Stability**.

## Mục tiêu

Upload → kiểm video → chuẩn bị ảnh S3 → xem thumbnail/frame/clip → xóa/phục hồi chạy đúng và có bằng chứng test local. Sau đó kiểm candidate cô lập trên Contabo. Chưa mở rộng dataset/model tham chiếu trong lát này.

Đọc theo thứ tự: `AGENTS.md` → `PLAN.md` → `docs/IMPLEMENT_GUIDE_V2.md` → `docs/design/CONTRACTS.md` §21.20–21 → `docs/architecture_diagram.md` → `docs/archived/2026-10-13/docs/backlog/BASIC_FLOW_STABILITY_PLAN.md` → tài liệu này.

## Trạng thái nhận bàn giao

- Checkout hiện tại: `codex/basic-flow-stability`, từ `origin/thien` tại `5e05c1c8a53e86da17aeafc6754dc8ddbaf90bfc`.
- Thay đổi nằm trong working tree, **chưa commit**. Chạy `git status --short` và đọc toàn diff trước khi sửa. Không reset/clean/checkout main/pull đè lên thay đổi này.
- Task 1: đã sửa shutdown có thời hạn, gồm chặn interpreter exit bị treo khi child còn sống. Báo cáo Windows/spawn và Docker Linux ở `output/basic-stability/task-1-*.md`; lượt Linux ghi nhận 19 test đạt. Giữ implementation, kiểm lại khi tích hợp.
- Task 2: đã đưa S3 DELETE/HEAD ra ngoài transaction giữ khóa, kiểm lại fence/lease/cancel trước confirmed/publish. Báo cáo scoped ở `output/basic-stability/task-2-*.md`, ghi nhận 21 test PostgreSQL/moto đạt. Giữ implementation, kiểm lại khi tích hợp.
- Task 3: importer SYSTEM đã có code nháp, **chưa nghiệm thu**. Đọc `output/basic-stability/task-3-handoff.md` nếu có; nếu chưa có, coi toàn diff hai file importer là work in progress.
- Browser smoke từng thấy thumbnail legacy vẫn báo chưa chuẩn bị sau khi bấm toolbar. **Chưa xác nhận bug** vì lần đó chưa đợi backend READY. Cần tái hiện đúng ở bước B.
- Chưa chạy regression tổng sau toàn bộ thay đổi, chưa test candidate Contabo, chưa deploy.

Các số test trên là bằng chứng lịch sử của từng phần, không chứng minh working tree hiện tại đã xanh.

## A. Hoàn thiện SYSTEM importer và test lỗi

File chính: `scripts/pilot_prepare_system_frames.py`, `tests/test_pilot_frames_prepare.py`.

Thiết kế đã được người dùng duyệt và ghi CONTRACTS §21.21/quyết định #121. Không thiết kế lại một dịch vụ import:

1. Kiểm policy enabled, source SYSTEM READY, raw/spec/encoding, giới hạn ảnh/disk và GLOBAL trước PUT.
2. Encode vào temp có giới hạn, tính inventory chính xác: key/hash/byte. Ghi receipt bền và giữ GLOBAL reserved trong transaction trước PUT đầu tiên.
3. Chỉ một writer cho cùng source/raw/spec/encoding; khóa advisory theo session, không giữ transaction DB qua network I/O.
4. Publish đủ frame set và đổi reserved → persisted cùng receipt COMMITTED trong một transaction. Không tính USER storage cho SYSTEM.
5. Lỗi phải chờ mọi PUT đang chạy kết thúc trước cleanup. DELETE + HEAD đúng inventory, nhả reserved chỉ sau khi xác nhận sạch; lỗi cleanup giữ receipt/reservation để phục hồi.
6. Recovery phải chứng minh writer cũ đã dừng: cùng host kiểm PID + start token; khác host cần operator proof gắn receipt/process identity. Không cho proof bỏ qua writer đang sống, không quét/xóa prefix, không xóa bộ READY đã được nhận.
7. Giữ dry-run, inventory hash và operator-confirm. Apply lại cùng bộ READY không PUT/tính byte lần hai.

Code nháp có `--recovery-proof`; cần review correctness trước khi coi cơ chế này hoàn tất. Đọc connected storage/schema/receipt helpers để kiểm transaction và quyền, không tự tạo bảng/role mới.

Điểm phải sửa ngay từ handoff Task 3: test thiếu GLOBAL hiện UPDATE config policy bất biến, phải tạo policy mới qua fixture `pg.policy(config=...)` rồi dùng version đó. Header importer còn mô tả flow cũ; cập nhật theo thiết kế đã duyệt. Ba test preflight chỉ chạy tới setup rồi lỗi sandbox TCP `10013`, chưa có RED/GREEN thật. Khi test local cần quyền socket phù hợp; không sửa app để né môi trường hoặc gọi lỗi setup là bug sản phẩm.

Test PostgreSQL thật + moto bắt buộc:

- Policy thiếu/disabled và GLOBAL không đủ: không PUT ảnh, không leak reservation.
- PUT lỗi sau một object; DB publish lỗi; cleanup DELETE/HEAD lỗi: không READY partial, kế toán vẫn đối soát được.
- Retry sau cleanup thành công trong cùng process; retry sau crash có/không có bằng chứng writer dừng; writer còn sống bị chặn.
- Hai importer cùng nguồn; COMMITTED/READY chạy lại idempotent; nguồn/raw/spec đổi giữa chừng không publish.
- Kiểm storage reserved/persisted và receipt/object thực tế sau từng nhánh, không chỉ kiểm exception.

## B. Tái hiện rồi sửa cập nhật thumbnail nếu có lỗi

File cần đọc: `src/web/src/screens/PilotLibraryScreen.tsx`, `src/web/src/components/MediaImg.tsx`, `src/web/src/media.ts`, `src/web/src/pilot.test.ts`.

1. Dùng source legacy READY nhưng chưa có frame set; mở Kho dữ liệu, bấm **Chuẩn bị ảnh** ở toolbar.
2. Đợi backend báo frames READY, rồi đợi đủ chu kỳ polling giao diện (hiện khoảng 5 giây).
3. Thumbnail phải xuất hiện mà không reload trang, không cần bấm nút riêng trên ảnh. Nếu không, ghi RED bằng browser screenshot/network trước khi sửa.
4. Nghi vấn: hook giữ lỗi NOT_PREPARED vì media ref không đổi sau toolbar preparation. Kiểm chứng; sửa nhỏ nhất theo luồng state hiện có (ví dụ remount/retry khi trạng thái ảnh chuyển READY nếu phù hợp). Không xây cache/event framework mới.
5. Thêm regression có ý nghĩa và chạy lại browser GREEN; kiểm error/cancel/retry, logout và đổi tài khoản không giữ URL riêng của người trước.

Harness thử nghiệm có sẵn trong `output/basic-stability/ui-local-stack.py`, `ui-smoke.cjs`, `ui-tests/`. Đọc trước khi dùng: chỉ token test, PostgreSQL/moto/API/worker local. `ui-session.json` cũ không phải session đang sống. Khởi động lại stack và Vite với API port mới; không dùng token/env production. Browser harness dùng Edge headless profile mới, chặn request ngoài localhost.

## C. Gate local và đo thực tế

Trước mỗi terminal Python/npm/test: `. .\scripts\use_project_storage.ps1`. Output/cache/temp ở `data/` hoặc `output/` trên ổ repo.

PostgreSQL portable đã được dùng trên `127.0.0.1:54329`, test URL `postgresql://postgres@127.0.0.1:54329/postgres`; kiểm trạng thái trước khi dùng. Fixture tạo/xóa DB test riêng. Binary nằm `data/tools/postgresql-16.15/pgsql/bin`, cluster test `data/pg-test-dep4f`. Có thể còn alias `R:` trỏ repo để tránh đường dẫn có dấu. Không coi DB test là DB ứng dụng/cloud.

S3 mock deps ở `output/thien-test-deps`; chỉ bổ sung PYTHONPATH nếu cần theo harness. Thiếu fixture/dependency phải báo, không biến test PostgreSQL thành skip để báo xanh.

```powershell
. .\scripts\use_project_storage.ps1
$env:RAV_PILOT_TEST_DATABASE_URL = 'postgresql://postgres@127.0.0.1:54329/postgres'
python -m pytest tests/test_pilot_frames_prepare.py tests/test_pilot_delete.py tests/test_pilot_frames.py tests/test_pilot_worker_entry.py -q --basetemp=output/pytest-agy-focused
python -m pytest tests -q --basetemp=output/pytest-agy-full
python -m ruff check src tests scripts/pilot_prepare_system_frames.py
```

`tests/test_codex_hook.py` có lỗi PowerShell/path có dấu đã biết; ghi đúng test/log và phân biệt với lỗi mới. Đọc mọi skip. Chạy spawn/E2E thật, không chỉ mock entrypoint.

Frontend từ terminal root đã source storage script, sau đó vào `src/web`: `npm run test`, `npx tsc --noEmit`, build cả `VITE_RAV_MODE=local` và `pilot`.

E2E: upload video thật → READY và đủ ảnh → ảnh đầu/lưới/frame → clip ±3 giây → legacy backfill → owner A/B → xóa. Chạy CPU và MEDIA thực; kiểm MEDIA vẫn đáp ứng khi CPU bận. Ảnh phục vụ từ S3 đã chuẩn bị, clip giữ lấy từ raw. Không đổi lưới 4 fps thành 5 fps hoặc keyframe vì sẽ làm lệch unit IDs/features hiện có.

Đo cùng video/settings với baseline: ít nhất 5 lượt video tổng hợp, video thật `data/videos/commaai-sample.mp4`, thêm video 5 phút `data/youtube5m/6WTF1JV5knI.mp4` nếu file có. Ghi cold/warm riêng, queue/READY/grid/frame/clip, object/byte count và RAM. Nếu thiếu dữ liệu thì ghi gate chưa chạy. Baseline ở `output/thien-local-test/output/LOCAL_TEST_REPORT.md` và `output/thien-local-test/output/frames-bench/`; local S3 mock không chứng minh cloud nhanh hơn.

Linux shutdown phải chạy trên Linux thực. Docker candidate và deps test có sẵn; đọc báo cáo Task 1 để tái dùng lệnh. Windows pass không thay thế Linux SIGTERM-ignore/interpreter-exit test.

## D. Test Contabo sau khi local xanh

Người dùng cho phép truy cập để **test trước**. Đọc runbook/runtime/deployment status tìm cách SSH hiện có, không in secret. Preflight chỉ đọc CPU/RAM/disk/images và worker live.

Candidate/test DB/S3 mock phải cô lập, không nhận production queue/credentials, không thay service/container live, không migrate/backfill cloud, không chạy Kaggle hoặc đổi Render. Nếu không đủ tài nguyên để chạy cô lập, báo giới hạn và xin lựa chọn; không tự dừng worker live. Ghi kết quả host riêng, phân biệt với local.

## E. Báo cáo và điểm dừng

Tạo `output/basic-stability/AGY_COMPLETION_REPORT.md`: file thay đổi, test/lệnh/log, bảng trước/sau cùng điều kiện, lỗi còn lại, skip, gate local/browser/Linux/Contabo. Review toàn diff với CONTRACTS, kiểm `git diff --check`.

Chỉ khi đủ gate của lát: cập nhật PLAN đúng việc đã kiểm, commit tiếng Việt không dấu theo AGENTS rồi dừng. Không tự merge main/push/deploy. Nếu gate thiếu, giữ unchecked và bàn giao phần còn lại; không ghi hoàn tất cả pipeline.

Sau test dừng các process API/worker/moto/Vite/PostgreSQL **do test này tạo**, gỡ alias R: chỉ nếu thuộc phiên test. Không kill hàng loạt python/node hoặc process người dùng.

Dataset/model tham chiếu, recipe cần provenance, giảm round trip DB và quality/lineage là các lát tiếp theo: đọc phần thứ tự trong BASIC_FLOW_STABILITY_PLAN, chỉ đề xuất sau khi gate này đạt, chưa implement ở đây.
