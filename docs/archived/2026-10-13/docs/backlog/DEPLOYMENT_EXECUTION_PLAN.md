# Deployment — kế hoạch thực thi và chia việc cho coding agent

Ngày: **2026-10-09**. Tài liệu từ **PL6**, làm rõ ở **PL7 / #83**, chỉ tài liệu.

## 0. Trạng thái: đọc trước khi code

- Scope sản phẩm theo quyết định #79–#81 và #83; host theo #80. Không hỏi lại các lựa chọn đã chốt.
- #83: mẫu/lịch sử gốc shared readonly; từ mẫu chỉ tạo phiên riêng **trống**, cùng nguồn/cấu hình khởi tạo, không copy events/kết quả hoặc tự đề xuất. Dùng S3/Supabase/Kaggle hiện có, credential operator bootstrap trong `.env` repo được chỉ định explicit; preflight vẫn phải kiểm target/namespace/quyền/config thiếu.
- Các giao diện kỹ thuật dưới đây là **DRAFT D0 ĐỂ DUYỆT**, chưa là permission để tự sửa contract/code.
- `/api/v2`, resource UUID, `ActorContext`, schema/RPC và cách tách runtime là phương án cụ thể để review. Chỉ dùng khi D0 đã ghi quyết định duyệt vào CONTRACTS/architecture/UI_DESIGN.
- Các lượng credit, duration/pixel/RSS caps, timeout, TTL và quality threshold phải được đo/chốt ở DEP1. Không tự điền số “hợp lý” rồi mở deploy.
- “Bản thử 2–3 người” là nghĩa của pilot. Bootstrap hạ tầng để đo và bản cho user dùng có **hai gate khác nhau**.
- Người dùng muốn tìm BDD/nano sau khi deploy hạ tầng được. BDD, model provenance và PII quality vẫn là gate trước mở đầy đủ pilot; bootstrap không tick A01–A11.
- Mỗi phiên một lát trong PLAN. Có thể dùng nhiều subagent **bên trong lát đó**, mỗi agent có file ownership riêng; parent tích hợp/test/commit rồi dừng.

Luật và thứ bậc: [AGENTS](../../../../../AGENTS.md), [docs/README](../../../../README.md). Không đẩy code/đăng public dữ liệu hoặc chạy GPU thật chỉ vì một task dùng mock đã đạt.

## 1. Tài liệu đã có và mức còn thiếu

| Tài liệu | Đã có | Phải hoàn thiện trước giao code |
|---|---|---|
| [DEPLOYMENT_PILOT_SPEC](../../../../design/DEPLOYMENT_PILOT_SPEC.md) | R01–R13, topology, invariants, A01–A11, D0–D8 | Chốt thiết kế chi tiết; cập nhật auth/confirmation/RESTRICT đã được trả lời, demo templates và thứ tự bootstrap rồi tìm dữ liệu |
| [CONTRACTS §21](../../../../design/CONTRACTS.md) | Target và các phần phải D0 duyệt | Actor/command signature, resource mapping, schema/transaction, DTO/version, job/export/delete interfaces |
| [architecture](../../../../architecture_diagram.md) | Local command/service/pipeline + target host | Phân biệt local v1 và deploy; API/worker entrypoint/import graph; sequence upload/job/export/fork; data authority |
| [UI_DESIGN](../../../../design/UI_DESIGN.md) | UI local; bullet phạm vi deploy | State/DTO/auth guard/media expiry/job/quota/export confirmation và màn xem mẫu readonly |
| [LOCAL_SINGLE_USER_LIMITS](../../../../design/LOCAL_SINGLE_USER_LIMITS.md) | Danh sách blocker local | Mỗi blocker phải gắn task/gate; giữ nguyên giới hạn tới khi gate code đạt |
| [PLAN](../../../../../PLAN.md) | PL1–PL5 và slices local | Thêm slices deployment rõ đầu ra, dependencies và tests; tránh tên D1 trùng lát chuyển cache #74 |
| `docs/setup/SUPABASE_EMBEDDING_JOBS.sql` | Job embedding C1, lease/RLS backend-only | Chưa có auth/catalog/session/snapshot/quota pilot. Không sửa script cũ để giả rằng đã migrate toàn app |

Ba bản thiết kế bổ sung trong PL6:

1. [Identity/data](../../../../design/DEPLOYMENT_IDENTITY_DATA_SPEC.md): principal, owner/system, Postgres, template/fork, seed/migration/delete.
2. [Runtime/storage](../../../../design/DEPLOYMENT_RUNTIME_STORAGE_SPEC.md): upload/media/cache, queue/attempt/fencing, quota và Kaggle cutover.
3. [UI/export/ops](../../../../design/DEPLOYMENT_UI_EXPORT_OPS_SPEC.md): DTO/state UI, PII/report/confirmation, env/build/runbook/acceptance.

Đây là bản thiết kế đề xuất có task đủ cụ thể để review; chưa thay nguồn sự thật. Không tạo tài liệu mô tả code lệch để hợp thức hóa.

## 2. Những ranh giới phải chốt cùng nhau

### 2.1 Principal + owner + resource mapping

Một người tích hợp chốt cùng lúc:

- Trusted actor từ token, không từ body/header role người dùng tự khai.
- Ownership của source/session/proposal/snapshot/job/export và scope system readonly.
- UUID ngoài HTTP ánh xạ core source/unit IDs cũ; không sửa FieldKey/scoring/pixel identity.
- Direct TLS PG trên server: ordinary transaction gắn verified claims/RLS, API/worker LOGIN roles giới hạn; credential/operator chỉ ở host được phép.
- Local v1 vẫn chạy được với actor local; deploy tuyệt đối không fallback local khi thiếu auth/DB.

Không chia “auth middleware” và “tenant DB” cho hai agent tự chọn contract riêng. Sau khi chốt, hai implementation có thể tách file.

### 2.2 Job enqueue + quota hold + source pin

Đây là **một transaction DB**, cùng idempotency key và cùng authority:

1. Kiểm principal/input/source state.
2. Khóa profile → sources UUID tăng dần → session/resource → admission/quota/storage accounts → job → slot theo runtime spec.
3. Reserve credit/bytes/pending slot và pin nguồn.
4. Insert job/receipt; commit cả lô hoặc rollback cả lô.

Queue và quota không được commit qua hai HTTP request độc lập. Cancel/settle/retry/reset cũng phải dùng schema/receipt chung.

### 2.3 API–worker + recovery C1/C2

- Render xử lý auth/quyền, metadata, keep/drop, transaction, admission/job enqueue/status và cấp URL.
- Contabo xử lý raw/decode/inference/scoring/result validation/PII/report/ZIP và điều phối Kaggle.
- API deploy không start `prepare_queue`/cloud recovery cũ. Worker mới không claim job đang thuộc dispatcher local.
- Worker schema, attempt/fence/lease, GPU launch receipt/stop cleanup và slot/quota settle phải thống nhất trước cutover.

### 2.4 Snapshot freeze + PII + report + confirm + download

- Export/report đọc cùng snapshot, cùng `decision_seq`, cùng ordered members và artifact mapping.
- Hash payload loại confirmation receipt và ZIP hash; confirm đúng byte đã xem.
- Finalize không infer lại; chỉ READY bundle đã che mới được cấp URL tải.
- Mọi route export cũ đều qua gate hoặc bị tắt trên deploy. Xem report không tạo đường lộ raw.

Không giao report agent đọc kept hiện tại còn export agent đọc snapshot.

### 2.5 Source dependency + delete + publication

- System/demo luôn protected.
- Nguồn còn session/reference/snapshot/job dùng phải trả `resource_in_use`, không release pin trong pilot.
- Snapshot/job tạo pin và delete kiểm pin dùng cùng source lock. Cấp URL/publish kiểm trạng thái nguồn sau authorization.
- Metadata tombstone/physical cleanup có receipt riêng; không sửa/xóa manifest snapshot.

## 3. Giao việc song song: luật file ownership

Một parent/integrator giữ các file chung:

`docs/design/CONTRACTS.md`, `docs/architecture_diagram.md`, `docs/design/UI_DESIGN.md`, `PLAN.md`, decision log;
`src/rav/commands/base.py`, `src/rav/service/__init__.py`, `src/rav/api/__init__.py`, route registration;
schema migration/RPC, các DTO chung và config runtime.

Agent khác chỉ thay những file nêu trong task. Nếu cần shared file, gửi parent đoạn patch/requirement; không tự sửa đồng thời.

Trong một lát chỉ chạy tối đa **3 coding subagent + parent**. Mỗi task con nên có một hành vi và 1–3 file implementation; nếu lớn hơn thì parent chia task tiếp. Dùng worktree riêng khi code; docs PL6 hiện viết file riêng trong cùng checkout.

| Gói | Có thể làm độc lập sau khi gì đã khóa? | File ownership đề xuất | Phần parent tích hợp |
|---|---|---|---|
| Auth adapter/tests | ActorContext + user-context store | mới `api/auth.py`, tests auth riêng | bootstrap/command signature, profile DB/RLS |
| Catalog/session persistence | store methods + mapping/source authz | mới `service/pilot_catalog.py`, `commands/pilot_catalog.py`, tests riêng | `store/pilot_db.py`, schema/migration và callers local/deploy |
| Template/seed tooling | template/tạo phiên trống DTO + canonical storage receipt | mới `scripts/pilot_seed_catalog.py`, `scripts/pilot_import_demo.py`, `service/demo_templates.py`, tests seed | SQL và route/menu mẫu |
| S3 upload/media | Upload/Media DTO + pin/job RPC | mới upload/media services, S3 adapter/tests | route/config/common error DTO |
| Queue/quota | atomic RPC/receipts + worker handler contract | mới `store/pilot_runtime.py`, `worker/dispatcher.py`, `worker/supervisor.py`, tests | common SQL; parent không tách transaction thành hai phần |
| Kaggle adapter | parent attempt/slot/launch/reconcile contract | remote adapter + cloud tests có ownership duy nhất | dispatcher stop/settle authority |
| Curation CPU handlers | hydrated Context + authorized resource map + job API | worker handlers YOLO/text/proposal, tests parity | API→job routing, cache publication |
| PII adapter/bench | detector interface/pinned fixture manifest | mới `pipeline/pii.py`, `eval/pilot/` | quality acceptance/caps, không fake model |
| Report renderer | frozen report DTO + artifact mapping | mới `pipeline/pilot_report.py`, renderer tests | export freeze/payload hash/download gate |
| FE screens | DTO fixtures/version/routes đã khóa | auth/template/job/quota/export screens và hooks riêng | chỉ một FE integration owner viết `api.ts`/App/navigation, parent review |
| Build/runbook | config/runtime role/entrypoint đã khóa | dependency profiles, Dockerfiles, deploy scripts/tests | final cloud config, credential injection |

Không chạy hai agent cùng sửa `api.ts`, `db.py`, shared SQL/RPC hoặc old cloud dispatcher. Agent benchmark/renderer/mock-FE có thể chuẩn bị fixture/test trước DB thật; phần tích hợp phải chờ prerequisite.

## 4. Các lát đề xuất và thứ tự thực hiện

Tên **DEP0–DEP8** tránh đụng D1 chuyển cache trong PLAN. Đây là cách chia việc đề xuất từ D0–D8 của spec, chưa tick là đã triển khai.

| Lát | Kết quả cụ thể | Việc song song trong cùng lát | Gate rồi dừng |
|---|---|---|---|
| DEP0 — khóa docs | Duyệt bộ spec; SoT có exact signatures/schema/DTO/state/error/ownership; checklist task | Draft identity/runtime/UI-export; parent hợp nhất | Không còn lựa chọn D0 để coding agent tự quyết |
| DEP1 — bootstrap/đo | API health nhẹ + worker mode operator-bootstrap/readiness probes/build, không claim queue khi chưa schema; harness riêng có envelope operator duyệt để đo | API/import/build; worker/benchmark harness; env/cost/asset inventory | bootstrap_ready riêng pilot_ready; số đo/policy nào chưa đủ ghi BLOCKED, chưa mở user admission hoặc fake readiness |
| DEP2 — auth/persistence | Closed auth, Postgres catalog/session/state/snapshot, owner/system, migrations | Auth adapter; store/session adapter; fixtures/RLS tests | A01/A02 liên quan; local regression; metadata bền sau restart |
| DEP3 — queue/quota | Durable jobs/attempts/resource slots/quota/global GPU/cancel/recovery; source pin và storage hold RPC có trước callers | Job/worker adapter; Kaggle adapter; race/fault tests | A05/A08/A10 phần runtime; atomic SQL chung, API off không mất job |
| DEP4 — storage + samples | Direct upload validation/canonical, private media/cache, readonly template/history và tạo phiên riêng trống cùng nguồn/config, allowlist migration | Upload/storage; media/cache; seed/template tooling | A02/A03/A04 và parity/idempotency trên RPC thật DEP3; no inherited history/result/job hoặc auto propose, template nguyên; một parent chốt schema/source locks |
| DEP5 — curation | YOLO/text/proposal/metrics nặng ở worker; FE dùng job state | CPU handlers; FE job/quota states; parity/replay tests | A06; 3-user review không bị heavy job chặn |
| DEP6 — export/delete | PII exact snapshot, frozen report, owner confirm, private READY bundle; protected-source delete | PII adapter; report/FE confirmation; lifecycle tests | A07/A09; legacy raw bypass đóng; quality chưa đạt thì không mở export |
| DEP7 — vận hành release | Deploy configs hoàn chỉnh, backup/restore/rollback, usage/cost monitor, ping | Build/env; backup/runbook; cloud failure tests | A10/A11, restore drill và secret/build checks |
| DEP8 — mở bản thử | Accounts A/B/C end-to-end; đầy đủ BDD/nano/reference được phép, limits/cost đo thật | Parent điều phối acceptance; không concurrent migration/provision | A01–A11 đạt hoặc scope thay đổi được user duyệt/ghi Hướng tới |

DEP3 cần DEP2 và pin/receipt contract đã khóa tại DEP0; DEP4 cần DEP2+DEP3. Storage/media/template agent có thể viết adapter bằng frozen fixtures trước, nhưng chỉ đạt acceptance khi queue/quota RPC thật đã có. DEP5 cần DEP3+DEP4; DEP6 cần DEP5 và PII gate. Build scaffold có thể chuẩn bị sớm ở DEP1, release đầy đủ vẫn DEP7.

Không chạy nhiều lát độc lập trong cùng phiên rồi tick toàn bộ. Nếu muốn gộp lát vì nguồn lực, chỉnh PLAN và nêu đầu ra/gate trước code, giữ scope contract.

## 5. Tài liệu/artifact chưa có: phải tạo ở đúng lát

| Chưa có | Owner/lát | Nội dung phải có; không chỉ câu “implement X” |
|---|---|---|
| SQL migrations pilot chạy được | parent DEP2/DEP3/DEP6 | `001_pilot_identity.sql` → `002_pilot_runtime.sql` → `003_pilot_storage.sql` → `004_pilot_worker_objects.sql` → `005_pilot_features.sql` → `006_pilot_curation.sql` → `007_pilot_analysis.sql` → `008_pilot_export.sql`; columns/types/FK/check/index/RLS/grants/immutable triggers/RPC; schema version/lock/rollback-safe migration; DB integration tests |
| DTO + contract fixtures dùng chung | parent DEP0/DEP2 | JSON mẫu/version, ready/pending/error, request→response, field nullable, core↔resource map; Python/TS đọc cùng fixture |
| Policy + measurements | DEP1 | Hardware/region/revision/input, CPU/wall/RSS/IO/bytes, quota account/reset/freshness, model/hash/license; numeric caps/credit/quality acceptance |
| Seed allowlist + migration receipts | DEP4 | Source/template/session IDs được chọn, owner/system, hash/provenance/cache map; dry-run counts, rerun/crash, không copy `.env` hoặc mọi output |
| Template-start fixtures | DEP2/DEP4 | Mẫu có history nhưng session mới state_version=1, kept/drop rỗng, decision_seq=0; counts history/result/job=0, same sources/config, no enqueue, A/B isolation, idempotency và rollback pins/receipt |
| Account A/B/C + fault fixtures | từng slice | Expected auth/isolation/source protection; concurrent reset/enqueue/delete; stale fence/launch ACK loss/expired URL/corrupt NPZ/PII failure |
| PII ground truth và checkpoint manifest | sau bootstrap, trước export release | Full image/tiles/day/night/small face/plate, label convention, checkpoint SHA/license/runtime; quality khác mock mechanism tests |
| Cloud config/runtime runbook | DEP1 scaffold, DEP7 release | Required env theo host, public/secret, build/start/migrate/supervisor/ping/backup/restore/rollback commands + expected output |
| Evidence acceptance/cost | DEP8 | Bảng A01–A11→test/result/artifact/revision, bill/forecast/as-of, 3-user resource test; không dùng mock pass thay cloud pass |

Các file trên được tạo khi implementation slice bắt đầu; PL6 không tạo SQL/app/mock services để giả đã sẵn sàng deploy.

## 6. Mẫu task giao coding agent yếu

Parent điền **tất cả** trường, không giao chỉ một câu “làm auth”:

```text
Task ID: DEPx-Tn. Một hành vi cần thực hiện: ...
Base commit/worktree: ...
Approved spec sections + revision: ... (phải có gate D0 đã duyệt)
Prerequisites đã đạt và artifact path: ...
File được sửa/tạo: ...; file shared không được sửa: ...
Input: exact request/function signature + fixture JSON path.
Output: exact DTO/type/state + fixture; fields/error/nullability.
Các bước: 1... 2... 3... (nêu caller phải nối, không chỉ helper).
Invariant: ownership, local regression, pixel/core identity, transaction... cụ thể.
Test phải thêm/chạy: tên case, input, expected status/state/DB rows/bytes.
Lệnh: source storage helper; pytest targeted --basetemp output/...; ruff;
       tsc/build nếu FE; real Postgres test nếu SQL/RPC.
Done: ...; không claim real cloud/model quality bằng fixture giả.
Nếu thiếu dữ liệu/signature/policy: trả BLOCKED + đúng thiếu gì;
không tự fallback, bỏ validation, đổi scope hoặc sửa thuật toán.
Report: files/diff, test output, remaining blockers, no push.
```

Mỗi task kết thúc bằng task review: spec compliance và code quality; parent chạy gate tích hợp trên diff đã hợp nhất. Không coi self-review của implementer là review cuối.

## 7. Ví dụ task nhỏ: auth adapter

**Chỉ sau ActorContext/JWT verification contract DEP0 được duyệt.**

1. Tạo `src/rav/api/auth.py`; input `Authorization: Bearer <token>` và auth config đã validate.
2. Verify signature/issuer/audience/expiry; lấy user subject, query profile active theo role được phép.
3. Output trusted ActorContext; không đọc `owner_id`/`actor` từ body, không dùng `X-Role`.
4. Không nối route registration trong task này; parent inject dependency sau khi adapter tests đạt.
5. Test bằng pinned fake issuer/JWKS fixture: missing/expired/forged/wrong issuer/disabled → 401; valid active → đúng subject; body chọn owner khác không đổi principal.
6. Provider lỗi/config thiếu → safe error theo approved envelope, không fallback `human:local` trên deploy.
7. File ownership: chỉ auth adapter + auth tests; parent giữ bootstrap, DTO và SQL.

Ví dụ này định rõ hành vi; implementer không cần quyết định auth provider hoặc thiết kế lại command layer.

## 8. Verification cho mỗi lát

PowerShell tại repo root, trước Python/npm:

```powershell
. .\scripts\use_project_storage.ps1
python -m pytest tests -q --basetemp=output/pytest-tmp
python -m ruff check src tests
```

Nếu đụng FE, terminal riêng từ repo root:

```powershell
. .\scripts\use_project_storage.ps1
Set-Location -LiteralPath 'src/web'
npx tsc --noEmit
npm run build
```

Agent worktree có basetemp riêng dưới `output/`; parent full gate sau hợp nhất. Test HTTP TestClient trên Windows cần socket nội bộ; nếu sandbox chặn asyncio/socketpair thì xin tool escalation cho đúng test thay vì sửa app/test để né.

SQL/RPC bắt buộc thêm real Postgres transaction/RLS tests; mock HTTP không chứng minh rollback/race/grants. D1/live tests phải báo môi trường thật, account/region/workload; không đưa secrets/raw/signed URL vào artifact.

Docs-only PL6/PL7: kiểm relative links, schema/DTO/state consistency, requirement→task→gate coverage và `git diff --check`; không chạy GPU/test app lại chỉ vì sửa tài liệu.

## 9. Còn cần làm rõ trước implementation

- Duyệt đề xuất `/api/v2`, resource UUID mapping, ActorContext, schemas/RPC và runtime profiles trong ba spec; chưa tự biến thành code contract.
- Tái dùng S3/Supabase/Kaggle hiện có đã chốt #83; credential trong `.env` repo là input explicit cho operator bootstrap. Inventory/preflight kiểm mapping/account/region/namespace và tên config/quyền còn thiếu, không in secret hoặc tự tạo tài nguyên mới; scoped DB roles D0 vẫn cần triển khai/kiểm.
- Danh sách demo local cụ thể đưa vào allowlist lúc inventory; phiên/lịch sử mẫu chung readonly, tạo phiên riêng trống cùng nguồn/config đã chốt #83. Không còn câu hỏi copy-history policy; exact DTO/pins/receipt vẫn thuộc DEP0.
- BDD/nano chưa có: tìm/provision sau bootstrap; khai báo train/reference phải dựa dữ liệu thật. Không mở guided recipe với quan hệ giả.
- Pin PII checkpoint/license/runtime, fixture ground truth và quality threshold; nếu chưa đạt, chặn release export.
- Measurement-derived caps/credits/deadlines/TTL/cost freshness/backup retention/RPO/RTO: giữ gate DEP1/DEP7, không user preference giả thành số đo.

## 10. Dọn docs cũ trong PL6

Người dùng yêu cầu đưa docs đã cũ/outdated vào archived. [Archive index](../../../README.md) ghi nhóm, lý do và tài liệu thay thế; không đọc archived như contract.
Guide khởi tạo MVP và product/FE intro cũ có bản lưu, cùng đường dẫn active được viết lại để AGENTS/read-order không trỏ vào lệnh lịch sử.
Giữ SoT, details local dataset/snapshot, component guides, cloud local setup và evaluation vì còn được dùng; cập nhật đoạn stale theo CONTRACTS, không đổi code để khớp docs cũ.
Template Technical Book/PRD origin/audit OSS giữ lịch sử riêng. Proposal goal_intent_flow và thay đổi có sẵn của người dùng được giữ nguyên.

Không cần hỏi lại: host Render/Contabo/Kaggle; private multi-user; video-only upload trên deploy; auth Supabase password; PII khi export và owner confirm; RESTRICT nguồn đang dùng; reset quota 4 giờ; mẫu readonly và tạo phiên cá nhân trống; dùng tài nguyên S3/Supabase/Kaggle hiện có và credential bootstrap `.env` repo.
