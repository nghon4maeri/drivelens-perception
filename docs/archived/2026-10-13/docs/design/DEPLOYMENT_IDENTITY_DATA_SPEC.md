# Pilot — identity, metadata, demo và xóa nguồn

Ngày: **2026-10-09**. Tài liệu triển khai chi tiết đi cùng [DEPLOYMENT_PILOT_SPEC](../../../../design/DEPLOYMENT_PILOT_SPEC.md).
**DRAFT D0 ĐỂ DUYỆT; chưa có code, SQL migration hoặc gate live đã đạt.**
Auth/password, RESTRICT, phiên trống từ mẫu và tái dùng tài nguyên hiện có đã được người dùng chốt (#81/#83).
`/api/v2`, UUID tài nguyên, ActorContext, schema/RPC và fork payload là **thiết kế đề xuất D0**.
Nguồn sự thật vẫn theo [docs/README](../../../../README.md); trước code phải đưa phần được duyệt vào CONTRACTS/architecture/UI.
Không dùng tài liệu này để đổi công thức, threshold, ordering, pixels, model revision hoặc FieldKey.

## 1. Quyết định và thứ tự làm

- Supabase email/password; operator cấp/reset account; tắt signup ở provider và transport, không có public signup.
- User đổi password bằng flow provider; operator không lưu password/plaintext token vào DB app, log hoặc backup.
- Private mặc định; system/demo chỉ đọc. Phiên và kết quả tạo trên system thuộc riêng user.
- Xóa nguồn còn phiên/snapshot/job sử dụng phải **RESTRICT**. Pilot không retirement/release pin cho người dùng.
- Vẫn có xóa nguồn private chưa được tham chiếu; không tự xóa raw/demo vì quota hoặc storage đầy.
- Demo local cần nguồn **và phiên/lịch sử mẫu**; import theo allowlist, không scan/chép toàn `data/` hoặc `output/`.
- Mọi account thấy mẫu và lịch sử ở khu SYSTEM chỉ đọc để học cách dùng; không mở phiên private của operator.
- Bắt đầu từ mẫu tạo **phiên PRIVATE trống**, dùng cùng nguồn/cấu hình khởi tạo hợp lệ; không copy lịch sử/kết quả hoặc tự đề xuất. A không thấy phiên của B và không sửa mẫu bằng direct ID.
- Chưa có BDD subset/checkpoint YOLO nano: tìm sau khi deploy bootstrap được; không giả asset hoặc relation train.
- Bootstrap hạ tầng/operator-only để đo được phép đi trước. BDD/nano/PII/parity/cost vẫn là gate trước full pilot.
- PII xem/xác nhận trước ZIP; hợp đồng export chi tiết ở [UI/export/ops spec](../../../../design/DEPLOYMENT_UI_EXPORT_OPS_SPEC.md).
- Queue/quota/storage/pins ở [runtime/storage spec](../../../../design/DEPLOYMENT_RUNTIME_STORAGE_SPEC.md); không tạo dispatcher thứ hai.

Bootstrap không mở private dữ liệu thật cho pilot accounts khi auth/media/export gates còn thiếu.
Dùng S3/Supabase/Kaggle hiện có; credential trong `.env` repo là input bootstrap được operator chỉ định explicit (#83).
Preflight kiểm exact project/account/bucket/region và namespace pilot riêng trước ghi; không in secret hoặc tự tạo tài nguyên mới.
Credential hiện có không chứng minh scoped direct-PG roles/config D0 đã đủ; thiếu thì báo đúng tên/quyền, không fallback admin.

## 2. Ownership file — đọc trước khi giao agent

| Owner | File được sửa trong lát implementation được giao | File chỉ đọc/đề nghị integration owner sửa |
|---|---|---|
| Integration owner | `docs/design/CONTRACTS.md`, `docs/architecture_diagram.md`, `docs/design/UI_DESIGN.md`, decision log, `PLAN.md`; `src/rav/commands/base.py`, `src/rav/commands/context.py`, `src/rav/api/routes.py`, `src/rav/api/__init__.py`, `src/rav/service/__init__.py`; toàn migrations/RPC và `src/rav/store/pilot_db.py` | Giữ một schema/interface/version authority |
| Identity/catalog agent | `src/rav/api/auth.py`, `src/rav/service/authorization.py`, `src/rav/service/pilot_catalog.py`, `src/rav/commands/pilot_catalog.py`; `tests/test_pilot_auth.py`, `tests/test_pilot_catalog.py` | Common files, SQL/RPC, runtime/storage và export files |
| Demo/migration agent | `scripts/pilot_import_demo.py`, `scripts/pilot_seed_catalog.py`, `src/rav/service/demo_templates.py`, `src/rav/commands/demo_templates.py`; `tests/test_pilot_demo.py`, `tests/test_pilot_import.py` | Không sửa SQLite/core identities, SQL hoặc upload worker |
| Delete agent | `src/rav/service/source_delete.py`, `src/rav/commands/source_delete.py`, `tests/test_pilot_delete.py`; handler delete theo runtime spec | Source lifecycle/RPC/schema do integration owner ghép |

Các đường dẫn mới ở bảng là đề xuất; integration owner chốt chúng một lần tại D0 và cập nhật bảng nếu đổi.
Mỗi agent chỉ làm work item được giao; gặp thiếu signature/schema phải báo seam, không tự tạo contract khác.
Một phiên implementation vẫn là một lát PLAN; test xanh, commit theo AGENTS, tick rồi dừng; không push.

## 3. Identity và trusted context

Đề xuất `src/rav/commands/context.py` không import App/Torch/store; chứa dataclass frozen:

```python
ActorContext(user_id: UUID | None, request_id: UUID)
# user_id=None chỉ hợp lệ trong runtime local; deploy phải có Supabase subject đã verify.
# actor string: deploy = "human:<user_id>"; local = "human:local".
Command.run(inp, services, actor: ActorContext) -> Output
```

1. `api/auth.py` nhận bearer token; verify chữ ký, issuer, audience, expiry và algorithm allowlist.
2. Dùng JWKS/SDK được pin; unknown key/expired token/verification outage không được decode payload rồi tin subject.
3. Integration owner chọn thư viện nhẹ/pin requirements và JWKS cache/rotation timeout; API không import ML.
4. Sau verify, đọc profile: tồn tại, active; sinh request_id phía server; transport inject ActorContext.
5. Actor/owner/admin trong body/header tự khai không có hiệu lực; input schema v2 reject trường owner/actor lạ.
6. Profile thiếu/disabled trả 401 safe code; không auto-provision account từ token vừa xuất hiện.
7. `services` của request có DB verified-claims context riêng; bearer không nằm trong ActorContext/DTO/log/error.
8. Không gán `app.actor` hoặc JWT vào object global dùng chung; test requests A/B xen kẽ bắt lỗi này.
9. Local CLI/v1 inject local context bằng factory chỉ trong mode local; pilot không có fallback local principal.
10. Worker không dùng ActorContext browser để thành admin: owner lấy từ job receipt DB; publish recheck active/authz.

`GET /api/v2/me` trả `{user_id,display_name,active}`; không email/password/quota quyền ghi hoặc provider token.
Operator tạo/reset/disable qua tooling riêng, admin credentials backend-only và audit actor/reason/request_id.
User không có endpoint tạo profile/operator, đổi `active`, quota policy hoặc admin privilege.
Disable tăng `authz_version`; command/URL mới bị chặn; signed URL đã cấp chỉ hết hiệu lực theo TTL thực.
Không hứa revoke tức thì file đã tải hoặc presigned capability đã cấp.

## 4. Resource UUID và core identity

- API v2 dùng UUID cho source/session/dataset/version/model/proposal/snapshot/template/export/job.
- `unit_id`, `core_source_id`, sample IDs, spec_hash, pool_hash và FieldKey giữ byte/ngữ nghĩa baseline.
- HTTP UUID không chèn vào công thức core hashes. Mapping có namespace owner/resource ở ngoài FieldStore.
- Source cùng bytes của A/B có UUID khác; core ID có thể giống; không dùng content hash làm quyền.
- Pilot không có scope PUBLIC/anonymous read; dataset có nguồn public vẫn được provision SYSTEM, auth bắt buộc.
- Pool không nhận hai resource cùng core_source_id: trả 422 `duplicate_core_source_in_pool`, không sửa unit IDs.
- Private cache prefix theo owner/resource; system cache chỉ chia sẻ input/model/spec immutable được phép đọc.
- Session spec/snapshot context vẫn giữ core pool/reference IDs theo CONTRACTS; DB relations giữ UUID tương ứng.
- Store validate mapping hai chiều trước tạo context; không resolve core ID toàn cục rồi mới kiểm quyền.
- DTO chỉ lộ UUID và metadata cần hiển thị; không raw_key, host path, prefix, legacy path hoặc credential.
- FieldStore adapter phải chứng minh ordered IDs/hashes/cache/pixel parity; không sửa thuật toán để đổi namespace.

## 5. Migration 001 — schema identity/catalog đề xuất

Integration owner tạo `src/rav/store/migrations/001_pilot_identity.sql`; deploy tooling chạy một lần có lock/backup.
API/worker boot kiểm schema compatible version; không tự chạy migration như SQLite local.
Tên bảng singular, prefix **`rav_pilot_`**; dùng UUID, `timestamptz`, `jsonb`, `bigint` và text CHECK.
Mọi `created_at` default DB time; hash SHA256 lowercase 64 hex; bytes >=0; counters/version >=1.
FK resource mặc định `ON DELETE RESTRICT`; không cascade xóa raw, history, snapshot hoặc version/member.

| Bảng | Cột bắt buộc và keys/checks |
|---|---|
| `rav_pilot_profile` | `user_id uuid PK REFERENCES auth.users(id) RESTRICT`, `active bool`, `display_name text`, `policy_id uuid`, `authz_version bigint`, `created_at`; policy FK được migration runtime bổ sung |
| `rav_pilot_source_asset` | `id uuid PK`, `core_source_id text`, `kind VIDEO / IMAGE_SET`, `scope PRIVATE / SYSTEM`, `owner_id uuid FK profile`, `protected bool`, `name text`, `raw_key text`, `raw_sha256 char(64)`, `size_bytes bigint`, `provenance jsonb`, `metadata jsonb`, `availability READY / UNAVAILABLE / DELETE_PENDING / DELETED`, `resource_version bigint`, `created_at` |
| `rav_pilot_dataset` | `id uuid PK`, `core_dataset_id text UNIQUE`, `source_id uuid FK source_asset`, `name`, `description`, `created_at`; source kind IMAGE_SET, SYSTEM/protected trong pilot |
| `rav_pilot_dataset_version` | `id uuid PK`, `core_version_id text UNIQUE`, `dataset_id FK`, `n int`, `manifest jsonb`, `manifest_hash char(64)`, `annotation_format none / yolo`, `classes jsonb`, `import_report jsonb`, `created_at`; UNIQUE(dataset_id,n), UNIQUE(dataset_id,manifest_hash) |
| `rav_pilot_dataset_member` | `version_id FK`, `ord int>=0`, `sample_id text`, `key text`, `relpath text`, `sha256 char(64)`, `size_bytes bigint`, `raw_key text`, `label_key text nullable`, `label_sha256 char(64) nullable`, `labels jsonb`; PK(version_id,sample_id), UNIQUE(version_id,ord), UNIQUE(version_id,key) |
| `rav_pilot_model_asset` | `id uuid PK`, `core_model_id text UNIQUE`, `scope SYSTEM`, `protected true`, `name text`, `raw_key text`, `sha256 char(64)`, `size_bytes bigint`, `checkpoint_revision text`, `task text`, `class_map jsonb`, `runtime_manifest jsonb`, `license jsonb`, `provenance jsonb`, `validation CONTAINER_ONLY / INFERENCE_VERIFIED`, `created_at` |
| `rav_pilot_model_dataset_declaration` | `model_id FK`, `dataset_version_id FK`, `relation TRAIN / FINETUNE`, `declared_by uuid FK profile`, `evidence jsonb`, `created_at`; PK(model_id,dataset_version_id); operator-only, immutable, không suy từ tên/bytes |
| `rav_pilot_session` | `id uuid PK`, `owner_id FK profile`, `name`, `goal`, `target_k int>0`, `spec jsonb`, `n_units int>=0`, `state_version bigint`, `started_from_template_id uuid nullable`, `created_at`; UNIQUE(id,owner_id) |
| `rav_pilot_session_source` | `session_id`, `owner_id`, `source_id`, `ord int>=0`; PK(session_id,source_id), UNIQUE(session_id,ord); composite FK(session_id,owner_id) -> session; source VIDEO |
| `rav_pilot_session_reference` | `session_id`, `owner_id`, `dataset_version_id`, `model_id nullable`; PK(session_id,dataset_version_id), composite session-owner FK; mỗi dataset tối đa một version |
| `rav_pilot_proposal` | `id uuid PK`, `session_id uuid`, `owner_id uuid`, `n int>=1`, `frozen_input jsonb`, `input_hash char(64)`, `picks jsonb`, `evidence jsonb`, `fields_used jsonb`, `state_version bigint`, `created_at`; UNIQUE(session_id,n), composite session-owner FK |
| `rav_pilot_decision` | `seq bigint generated always as identity PK`, `session_id uuid`, `owner_id uuid`, `unit_id text`, `action keep / drop / clear`, `actor text`, `goal text`, `proposal_n int nullable`, `decided_at timestamptz`, `shown_at timestamptz nullable`, `reason text nullable`, `confidence real nullable`, `extra jsonb`; UNIQUE(session_id,seq), composite session-owner FK |
| `rav_pilot_snapshot` | `id uuid PK`, `session_id`, `owner_id`, `n int>=1`, `created_by uuid FK profile`, `note`, `decision_seq bigint`, `proposal_cutoff_n int>=0`, `report_basis jsonb`, `members_hash`, `n_members int>0`, `context jsonb`, `context_hash`, `created_at`; UNIQUE(session_id,n), UNIQUE(id,owner_id), composite session-owner FK |
| `rav_pilot_snapshot_member` | `snapshot_id`, `owner_id`, `ord int>=0`, `unit_id`, `source_id uuid FK source_asset`, `core_source_id`, `source_kind video / image_set`, `provenance jsonb`; PK(snapshot_id,ord), UNIQUE(snapshot_id,unit_id), composite snapshot-owner FK |
| `rav_pilot_demo_template` | `id uuid PK`, `template_key text UNIQUE`, `legacy_session_id`, `manifest_hash UNIQUE`, `frozen_spec jsonb`, `source_mapping jsonb`, `provenance jsonb`, `created_at`; SYSTEM/read-only, không private session giả |
| `rav_pilot_demo_template_status` | `template_id uuid PK FK demo_template`, `availability IMPORTED / VERIFIED / BLOCKED`, `reason_code nullable`, `version bigint`, `updated_at`; operator/worker RPC cập nhật, user chỉ đọc safe status |
| `rav_pilot_demo_template_event` | `template_id FK`, `source_event_id text`, `ord int>=0`, `kind PROPOSAL / DECISION / SNAPSHOT`, `legacy_payload jsonb`, `payload_hash`; PK(template_id,source_event_id), UNIQUE(template_id,ord); immutable |
| `rav_pilot_import_receipt` | `id uuid PK`, `idempotency_key text UNIQUE`, `inventory_hash char(64)`, `mapping jsonb`, `counts jsonb`, `hashes jsonb`, `state STAGING / VERIFIED / COMMITTED / FAILED`, `operator_id FK profile`, `created_at`; không chứa local secrets |
| `rav_pilot_command_receipt` | `id uuid PK`, `owner_id uuid FK profile`, `action CREATE_SESSION / CREATE_SNAPSHOT / FORK_TEMPLATE`, `idempotency_key text`, `input_hash char(64)`, `safe_output jsonb`, `created_at`; UNIQUE(owner_id,action,idempotency_key), immutable, output only sanitized DTO; insert cùng transaction với resource/pins |
| `rav_pilot_source_delete` | `id uuid PK`, `source_id FK`, `owner_id FK`, `idempotency_key`, `expected_resource_version`, `state REQUESTED / RUNNING / SUCCEEDED / FAILED`, `receipt jsonb`, `created_at`; UNIQUE(owner_id,idempotency_key) |

Source CHECK: PRIVATE => owner_id NOT NULL; SYSTEM => owner_id IS NULL AND protected=true.
Source unique indexes: PRIVATE(owner_id,core_source_id); SYSTEM(core_source_id). Không global dedup private.
IMAGE_SET raw_key/hash là manifest immutable; raw ảnh/nhãn thật nằm ở dataset_member, không lấy manifest làm pixels.
Trigger chặn UPDATE/DELETE trên version/member/model/declaration/proposal/decision/snapshot/member/template/event.
Template status ở bảng riêng; trigger immutable không chặn cập nhật status, không sửa legacy payload.
Source raw_key/hash/core identity/scope/owner/protected immutable sau publish; name private/availability/version đổi qua RPC.
Session owner/spec reference immutable; chỉ add source theo contract, đổi name/state_version qua command được phép.
Session/template provenance FK và delete/job/artifact FKs bổ sung sau khi migration 002/003 định nghĩa bảng liên quan.
Schema không cấp quyền DELETE trực tiếp; delete request không xóa hàng source hoặc snapshot.
Decision proposal_n FK(session_id,proposal_n) -> proposal(session_id,n); snapshot decision_seq FK -> decision(session_id,seq).
Confidence null hoặc finite trong [0,1]; model label keys/hash phải cùng null hoặc cùng có; decision actor do RPC tạo.
Các cột id/owner/FK dùng uuid, name/goal/key/code dùng text, hash char(64), created/updated_at timestamptz; null chỉ khi ghi rõ.

## 6. RLS, grants và transactions

Enable RLS trên mọi bảng; `anon` không SELECT/INSERT/UPDATE/DELETE hoặc execute RPC nghiệp vụ.
Phương án D0 chọn **direct TLS Postgres** trên server, với LOGIN roles giới hạn `rav_pilot_api` và `rav_pilot_worker`; không triển khai cả REST lẫn direct DB để agent tự chọn.
Ordinary catalog/session calls chạy trong transaction của request: `BEGIN` → `SET LOCAL ROLE authenticated` → `set_config('request.jwt.claims', verified_claims_json, true)` parameterized → query/RPC → commit/rollback.
Claims chỉ chứa subject đã verify và role cố định authenticated; không lấy role/claims JSON từ body. Connection luôn reset/rollback trước trả pool; test A/B nối cùng connection không lộ claims.
API LOGIN được phép chuyển sang authenticated, không chuyển sang worker/admin/schema owner. Browser không giữ database URL/password.
API backend RPC dùng role API và trusted ActorContext kiểm scope cụ thể; ordinary authenticated RPC kiểm `auth.uid() = p_actor` nếu có tham số actor.
Worker LOGIN chỉ EXECUTE worker RPC được allowlist, không direct DML hoặc SET ROLE authenticated; operator credential riêng dùng migration/admin/backup.
Grant API/worker roles và authenticated theo từng bảng/function; không dùng service_role secret key thay vai trò giới hạn hoặc coi nó là bằng chứng quyền.
TLS/pooler/connection limits phải qua preflight và DEP1; không truy cập được thì BLOCKED, không fallback SQLite/REST/service_role.
Đây là lựa chọn thiết kế D0. Cơ chế transaction claims/RLS phù hợp mô hình [Supabase server Postgres client](https://supabase.com/docs/reference/server/middleware-withpostgresclient); Python adapter phải chứng minh cùng semantics bằng test DB thật.
Theo [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys), secret keys dùng service_role và bypass RLS; tạo hai secret keys không tự tạo hai DB roles giới hạn.
Mẫu SQL dưới là yêu cầu triển khai, integration owner chuyển thành policy/function đã test:

```sql
-- Bảng private có owner_id:
USING (owner_id = auth.uid() AND public.rav_pilot_is_active(auth.uid()))
WITH CHECK (owner_id = auth.uid() AND public.rav_pilot_is_active(auth.uid()))
-- Đọc source: active AND (owner_id=auth.uid() OR scope='SYSTEM'), chỉ READY/UNAVAILABLE theo hành động.
-- User không UPDATE SYSTEM; metadata UPDATE PRIVATE còn phải kiểm protected/columns qua RPC.
-- Child bảng thiếu owner_id: EXISTS parent authorized; không policy chỉ kiểm child UUID.
```

`rav_pilot_is_active` chỉ đọc profile theo subject, không cho caller chọn subject để lấy dữ liệu profile khác.
Profile self SELECT; user không tự UPDATE/INSERT. Model/dataset/template SYSTEM SELECT cho active accounts.
Template detail/history cho mọi active account qua DTO sanitized; không raw SELECT legacy_payload/internal keys.
Source catalog list/count dùng scoped query **trước** pagination; SELECT metadata không cấp raw_key tới browser.
Narrow column grants/RPC DTO views để `raw_key`/internal receipts không xuất hiện qua direct PostgREST mà browser có thể gọi bằng provider token.
Transfer/worker lấy key qua backend-role RPC kiểm explicit principal/resource/authz version; browser không execute RPC đó.
Snapshot/decision/version/proposal immutable: user chỉ SELECT + RPC append, không direct UPDATE/DELETE.
Special RPC SECURITY DEFINER pin `search_path`, qualify table names, revoke PUBLIC execute, kiểm auth.uid/profile.
Admin/worker RPC có DB role riêng, không authenticated execute; owner lấy trusted receipt và recheck authz version.
Cross-owner FK/check trigger kiểm source PRIVATE thuộc cùng owner hoặc SYSTEM; RLS một mình không thay FK integrity.
Thứ tự write lock chung: profile -> sources UUID sorted -> sessions UUID sorted -> receipt/pin; retry deadlock có giới hạn.
create session/add source/reference/snapshot và source pins phải commit cùng transaction; không check rồi insert riêng.
Append decisions lock session, kiểm expected_state_version/unit pool/target_k, append toàn lô và increment version.
Snapshot lock session, freeze max(seq) **và max(proposal.n)** cùng ordered members/context theo CONTRACTS; cùng hash thì reuse snapshot cuối và giữ cutoff gốc, không kéo lịch sử mới vào snapshot cũ.
proposal_cutoff_n là metadata report bên ngoài core context/hash; không đổi core hash để thêm owner/cutoff. Legacy import phải chứng minh cutoff theo event timeline đã freeze; không đủ bằng chứng thì export report_input_unavailable, không dùng current max(n).
report_basis version rav.report.basis.v1 freeze ordered `{proposal_id,input_hash,evidence_refs:[{artifact_id,sha256}]}` và `{metric_name,definition_version,artifact_id,sha256,status}` đã có tại snapshot. Missing metric/evidence ghi UNAVAILABLE; artifact sinh sau snapshot không tự chèn vào basis. Basis cùng snapshot immutable; chỉ metadata/hashes, không matrices/pixels hoặc URLs.
Nguồn/jobs/source_pin đến từ runtime migration 002; pin SESSION/SNAPSHOT/REFERENCE/EXPORT không release trong pilot.
DB/test chứng minh user không insert foreign-owner FK dù biết UUID; operator path cũng phải validate scope/mapping.

## 7. Store/service signatures để các gói ghép được

Đây là interface draft của `store/pilot_db.py`; integration owner là người duy nhất sửa implementation/signature.
`actor` đã trusted; store không nhận owner từ input; helpers trả dict/typed DTO nhẹ, không pixels/NPZ/model.
`ResourceRow` dùng UUID, core ID, availability/resource_version; key nội bộ chỉ tới server/service được phép.
Lỗi ngoài scope -> 404; resource thấy được nhưng action cấm ->403; stale/dependency ->409; provider unavailable ->503.

```python
get_profile(actor) -> ProfileRow
authorize_resource(actor, kind, resource_id, action) -> ResourceRow
list_sources(actor, kind, cursor, limit) -> Page[SourceDTO]
get_source(actor, source_id) -> SourceRow
list_dataset_versions(actor, dataset_id, cursor, limit) -> Page[DatasetVersionDTO]
get_dataset_version(actor, version_id) -> VersionRowWithMembers
list_models(actor, cursor, limit) -> Page[ModelDTO]
get_model(actor, model_id) -> ModelRow
resolve_reference(actor, version_ids, model_id) -> ResolvedReference
create_session(actor, pool_source_ids, goal, target_k, name, reference, idempotency_key) -> SessionDTO
get_session(actor, session_id) -> SessionRow
list_sessions(actor, cursor, limit) -> Page[SessionDTO]
add_session_sources(actor, session_id, source_ids, expected_state_version) -> SessionDTO
append_decisions(actor, session_id, changes, expected_state_version) -> ReviewStateDTO
create_snapshot(actor, session_id, note, expected_state_version, idempotency_key) -> SnapshotDTO
get_snapshot(actor, snapshot_id) -> SnapshotRowWithOrderedMembers
list_snapshots(actor, session_id, cursor, limit) -> Page[SnapshotDTO]
list_demo_templates(actor, cursor, limit) -> Page[TemplateDTO]
get_demo_template(actor, template_id) -> TemplateDTOWithOrderedHistory
fork_demo_template(actor, template_id, name, idempotency_key) -> ForkReceipt
request_source_delete(actor, source_id, expected_resource_version, idempotency_key) -> DeleteReceipt
get_source_delete(actor, delete_id) -> DeleteReceipt
```

`resolve_reference` kiểm SYSTEM version/model, prepared/inference readiness và declaration thật; thiếu trả safe reason.
Recipe reference_diversity/model_guided cần điều kiện tương ứng CONTRACTS §18; không gán BDD cho model COCO.
Không có checkpoint/declaration: guided unavailable; diversity/text recipe dùng điều kiện riêng hiện có vẫn giữ.
`authorize_resource` luôn kiểm nested session/proposal/snapshot/frame pool; unit/hash/core ID không tự cấp read.
Export gọi `get_snapshot` và runtime enqueue RPC; snapshot resolver không tự tạo ZIP hoặc inference khi GET.
DB credentials/backend failure không fallback SQLite, scan folder hoặc public shared cache trên deploy.

## 8. API v2 và compatibility

| Route draft | Input/output hoặc hành vi |
|---|---|
| `GET /api/v2/me` | Principal profile; authenticated, no-store |
| `GET /api/v2/sources`, `GET /sources/{id}` | Scoped metadata/page; UUID, readiness riêng từ runtime, không S3 key |
| `GET /api/v2/datasets`, `GET /datasets/{id}/versions` | SYSTEM references; read-only; không user import/upload route |
| `GET /api/v2/models`, `GET /models/{id}` | SYSTEM allowlist; readiness/declaration thật; không user model upload |
| `POST /api/v2/sessions` | UUID pool/reference/model -> validate mapping -> core spec; owner từ ActorContext |
| `GET /api/v2/sessions`, `GET /sessions/{id}` | Private; state_version và permission-scoped counts |
| `POST /api/v2/sessions/{id}/sources` | Add-only theo baseline, expected_state_version bắt buộc |
| `POST /api/v2/sessions/{id}/decisions` | changes + expected_state_version; mọi actor server tạo |
| `POST /api/v2/sessions/{id}/snapshots` | note + expected_state_version; immutable/reuse semantics baseline |
| `GET /api/v2/demo-templates`, `GET /demo-templates/{id}`, `POST /demo-templates/{id}/fork` | SYSTEM mẫu/lịch sử mọi user chỉ đọc; tạo PRIVATE session trống + receipt idempotent; không PUT/DELETE mẫu hoặc enqueue |
| `POST /api/v2/sources/{id}/delete-requests`, `GET /delete-requests/{id}` | UUID/version/idempotency; chỉ unreferenced private owner |

Prefix `/api/v2` áp dụng cho mọi route trong bảng; route rút gọn ở cột là cùng prefix.
Route heavy/media/upload/jobs/export xem hai spec liên quan; không giữ shape v1 khi hành vi đã thành async.
Local `/api/v1` giữ DTO/signature behavior và local model/dataset upload, benchmark/CLI hiện có.
Deploy mode không register v1 upload/raw/export/media path; gọi đường cũ trả 404, không bypass auth/PII.
Schema-compatible factory/light services và route registration do integration owner giữ; FE migrate v2 cùng lát.
Đầu vào API v2 dùng UUID resource; output picks giữ unit_id baseline và chỉ resolve media qua nguồn có quyền.

### 8.1 DTO/input tối thiểu để khóa fixtures D0

Tất cả resource IDs UUID; unit/core IDs string baseline. Input `extra=forbid`; body không owner/actor/path/key/credentials.
List input `{cursor?,limit,q?}`, Page `{items,total,next_cursor}`; total tính trên scope/filter trước pagination, next_cursor null cuối trang.
Metadata/payload response no-store. Parent tạo fixture Python/TS chung với UUID/hash thật của fixture trước giao task; không dùng tên type chưa khóa để agent tự bịa field.

| DTO / input | Fields và cách resolve |
|---|---|
| SourceDTO | `id,kind,scope,protected,name,availability,resource_version,metadata,dino:{ready,job_id},clip:{ready,job_id}`; metadata allowlist `width,height,duration_ms,n_frames,n_units`, null khi chưa xác minh; không raw_key/core lookup toàn cục |
| CreateSessionInput | `name,goal,target_k,pool_source_ids,reference:{dataset_version_ids,model_id}\|null`; VIDEO pool, SYSTEM references; source/core duplicate rule §4 |
| SessionDTO | `id,name,goal,target_k,pool_source_ids,reference,state_version,n_units,created_at,started_from_template_id` (nullable); không owner lấy từ request; provenance khởi tạo, không chứng minh copy history |
| AddSourcesInput | `source_ids,expected_state_version`; atomic add+pins+increment version |
| DecisionInput | `changes:[{unit_id,action,proposal_n,shown_at,reason,confidence,extra}],expected_state_version`; optional evidence fields null khi không có; actor và decided_at do DB tạo; extra typed safe schema, không actor override |
| ReviewStateDTO | `session_id,state_version,decision_seq,kept_ids,dropped_ids`; ordered IDs theo baseline replay; clear không giữ trạng thái cũ |
| SnapshotInput / SnapshotDTO | Input `{note,expected_state_version}`; output `id,session_id,n,n_members,decision_seq,proposal_cutoff_n,members_hash,context_hash,created_at,note`; detail thêm sanitized `context,members` theo CONTRACTS §14, không host path/raw key |
| TemplateDTO | `id,template_key,availability,reason_code,goal,target_k,source_ids,counts:{proposals,decisions,snapshots}`; detail thêm ordered history chứa versioned safe proposal/decision/snapshot projections; provenance mẫu chỉ allowlisted original IDs/time/actor |
| ForkInput / ForkReceipt | Input `{name?}` + Idempotency-Key; output `{session:SessionDTO,template_id,counts:{proposals:0,decisions:0,snapshots:0},replayed}`; không event mapping hay legacy payload; session khởi tạo state_version=1 |
| DeleteInput / DeleteReceipt | Input `{expected_resource_version}` + Idempotency-Key; output `{id,source_id,state,job_id,error}`; error resource_in_use chỉ safe dependencies owner biết, không path/resource của B |

DatasetVersionDTO/ModelDTO là safe projection của immutable schema §5 và CONTRACTS §14/16: IDs/version/hash/validation/class/provenance cần hiển thị; parent khóa allowlist JSON fixture, loại folder/path/raw_key/import secrets. Không generic `SELECT *` hoặc serialize JSON provenance chưa sanitize.
Readiness là receipt đã kiểm từ worker/runtime; thiếu asset/declaration/prepared field trả reason rõ, không suy readiness từ model tên hoặc file có tồn tại.
Create/fork/snapshot/delete có Idempotency-Key; decision/add-sources có CAS expected_state_version. Response mất sau CAS write: GET state reconcile, không replay bằng state_version mới rồi append cùng quyết định lần hai.
Create/fork/snapshot dùng command_receipt: cùng owner/action/key/input trả safe output cũ; khác input →409 idempotency_conflict; snapshot reuse vẫn ghi receipt cho key. Delete dùng source_delete receipt, jobs dùng runtime receipt; không ba authority cho cùng action.
Tên `fork`/`ForkReceipt` ở draft API chỉ gọi hành động bắt đầu phiên trống từ cấu hình mẫu; không có ngữ nghĩa clone event/state.

## 9. Seed/import mẫu và tạo phiên riêng trống

`scripts/pilot_seed_catalog.py --config <operator-config> --env-file .env --manifest <path> --dry-run` là CLI draft chỉ operator; credential input explicit theo ops spec, không download BDD/model lúc boot.
Manifest có seed_version, release/split/license, original IDs, selection seed, source/core IDs, SHA/bytes/class map.
BDD JSON conversion offline sang YOLO/none có provenance; không thêm converter ngầm vào API hoặc bịa nhãn.
BDD assets chưa có => inventory BLOCKED; bootstrap readiness được đo riêng, A03/full pilot không tick đạt.
Model allowlist pin bytes/revision/task/classes/license/runtime; declaration TRAIN/FINETUNE cần evidence operator.
Không seed model fake, số metric giả hoặc giả rằng nano pretrained COCO đã học BDD.

`scripts/pilot_import_demo.py --config <operator-config> --env-file .env --inventory <path> --dry-run` là CLI draft xuất plan/count/hash/mapping, chưa publish catalog.
Draft CLI default dry-run; apply dùng `--apply --operator-confirm <inventory_hash>` từ plan, không áp cả hai mode.
Config operator nằm ngoài Git/image, chỉ định target project/bucket/namespace hiện có; input credential từ `.env` repo khi chỉ định explicit, hoặc injected environment đã validate. Receipt/audit không ghi credential; không chép file secret vào seed.
Inventory allowlist exact nguồn/phiên/artifact metadata; `.env`, tokens, notebooks, cache credential và raw ZIP bị loại.
Freeze SQLite consistent backup; verify FKs/raw SHA/ordered units/context/member hashes và references trước copy.
S3 copy verified bytes -> receipt -> Postgres import transaction; rerun inventory_hash reuse receipt, crash không nhân đôi.
Local gốc giữ nguyên; không sửa SQLite IDs, timestamps, actor hoặc raw. Không load untrusted .pt khi import.
Source demo SYSTEM/protected; template payload immutable lưu legacy actor/time/event IDs làm provenance.
Template VERIFIED chỉ sau mọi source/reference mapping hợp lệ; thiếu asset/permission/hash -> BLOCKED, không cắt ngầm.
Template DTO chỉ metadata đã sanitize; không expose path/object key/private operator sessions hoặc export raw.

**#83 đã chốt tạo phiên trống.** Schema/DTO/transaction dưới đây vẫn là chi tiết draft D0; không triển khai luồng copy history:

1. Owner bấm explicit; lock profile → sources UUID sorted → template theo thứ tự chung, kiểm active/VERIFIED/readiness và quyền nguồn/reference/model. Kiểm command_receipt trong cùng transaction: cùng owner/action/key/input trả receipt cũ; khác input trả 409.
2. Lấy cấu hình **khởi tạo** từ frozen_spec: pool nguồn, reference/model hợp lệ, goal, target_k, recipe/config theo core contract. Không lấy review_state, counters, đề xuất hiện tại hoặc derived session_kept của mẫu làm input.
3. Tạo session PRIVATE UUID mới, owner từ trusted actor, `started_from_template_id=template_id`, `state_version=1`; name dùng input nếu có, nếu không dùng tên mẫu. `n_units` là số unit của pool, không phải số unit đã duyệt.
4. Tạo session_source/session_reference mappings và SESSION/REFERENCE pins cho input được phép cùng transaction. Giữ core source/unit/spec/FieldKey; có thể tái dùng prepared fields SYSTEM immutable được cấp quyền, không tái dùng kết quả riêng của operator.
5. Review state ban đầu `kept_ids=[]`, `dropped_ids=[]`, `decision_seq=0`. Số hàng proposal/decision/snapshot/job/export/confirmation/metric receipt của session mới đều **0**; không insert legacy events, không enqueue inference/đợt đầu/metrics/export.
6. Insert command_receipt với sanitized ForkReceipt rồi commit cả session/mappings/pins/receipt hoặc rollback toàn bộ. Counts trong receipt đều 0; không old→new event mapping, legacy actor/time hoặc signed URL.
7. FE mở session mới ở trạng thái chưa có đợt duyệt. User chủ động yêu cầu đợt đầu; các thao tác sau đó mới tạo proposal/decision/snapshot/job/export/metric riêng, theo quota/authz/PII bình thường.
8. A/B tạo độc lập; thao tác ở A không sửa template/B. Cùng idempotency key trả cùng receipt/session, key mới tạo session khác. Template và history gốc byte/hash/count không thay đổi.

Provenance tới mẫu chỉ là `started_from_template_id`; không tạo copied-events, không tính lịch sử mẫu vào review time hay metric của user.
Import mẫu vẫn phải kiểm legacy history để hiển thị chỉ đọc. Tạo phiên trống kiểm nguồn/cấu hình tương thích và status VERIFIED; không cần remap event IDs hoặc report_basis của mẫu. Thiếu asset/recipe config hợp lệ trả safe issue, không tự đổi nguồn hoặc cắt cấu hình.

## 10. Delete unreferenced — không retirement/release pin

Nguồn SYSTEM/protected ->403; nguồn B ngoài scope ->404; không nhận S3 key từ browser.
Delete RPC lock active profile + source theo thứ tự thống nhất, kiểm expected_resource_version và pins/dependencies.
Tìm SESSION/SNAPSHOT/REFERENCE/EXPORT pin còn tồn tại, JOB chưa cleanup/lease; child FK cũng phải check.
Còn dependency ->409 `resource_in_use`, safe list trong quyền user; **không đổi availability** hay revoke nguồn hợp lệ.
Không dependency -> insert delete receipt + mark DELETE_PENDING + increment resource_version trong cùng transaction.
DELETE_PENDING chặn read/URL/prepare/enqueue/session/snapshot mới; enqueue/URL issuer cùng lock/version guard.
Physical worker claim receipt/fencing theo runtime; recheck source/protected/version/pins/leases trước xóa đúng object.
Source DELETED chỉ sau storage delete receipt; mất mạng/crash reconcile HEAD/receipt, không báo success giả.
Giữ tombstone/audit/identity hash; không DELETE hàng nguồn, không sửa snapshot/version/member hoặc source lineage.
GET catalog default loại DELETED, UI có thể hiển thị receipt; URL đã cấp còn hạn không hứa thu hồi tức thì.
User không xóa session/snapshot/reference để release pin trong pilot; dependency giữ raw tới thiết kế tương lai được duyệt.
Job pin transient chỉ runtime cleanup được release khi executor/result references đã xử lý; không user release endpoint.
GC staging/temp/cache theo runtime policy riêng; không xóa canonical/private/system raw bằng prefix scan.

## 11. Work items và acceptance cho agent

| ID | Làm cụ thể | Fixture/test/gate phải có trước báo xong |
|---|---|---|
| I0 integration | Chốt docs/ActorContext/routes/store signatures và migrations 001->002->003; import nhẹ, compatibility/version checks | Review D0; API boot không ML; local v1 regression; chưa dùng draft như approved |
| I1 auth | `api/auth.py` + operator tooling contract; signup-off/profile active/JWT verify; không global actor | A/B JWT fixture; expired/forged/unknown issuer/audience/disabled/missing profile; direct signup blocked live A01 |
| I2 catalog | `authorization.py`, `pilot_catalog.py`, commands; owner/system/nested relation checks trước resolve/cache/pagination | A/B private cùng content/core_id khác UUID + SYSTEM fixture; list/count/search/direct-ID; A cannot use B source/ref/job; A02/A03 |
| I3 persistence | Integration owner implement migration/RLS/RPC/immutable rows và lock/version signatures | Real Postgres test roles anon/userA/userB/worker; RLS bypass attempts/FK spoof/trigger update-delete/stale CAS; atomic rollback |
| I4 seed/import | Hai scripts + import receipt; allowlist/dry-run/hash/owner mapping; BLOCKED assets rõ | Tiny video + 2 labeled images + legacy session events; rerun/crash/count/hash parity; không `.env`/raw ZIP/whole-folder copy; A11 |
| I5 từ mẫu | Template resolver + command; session riêng trống với cùng nguồn/cấu hình khởi tạo, mappings/pins/receipt atomic | A/B isolation; replay key trả cùng session, new key/concurrent owners tách session; state_version=1, decision_seq=0, kept/drop rỗng, mọi history/result/job row=0; no enqueue; template hash/count nguyên; thiếu nguồn/config fail; A03/A06 |
| I6 delete | `source_delete.py` + runtime handler dùng common RPC; RESTRICT không retirement | 2 connections race delete vs create_session/snapshot/enqueue/URL; private unreferenced success; dependencies/protected/B fail; A09 |

Fixtures chỉ tiny synthetic bytes/metadata; unit tests không load DINO/CLIP/YOLO hay gọi cloud thật.
RLS/transaction race tests cần Postgres thật của test namespace được phép; mock pass không thay chúng.
Trước Python/test PowerShell source `scripts/use_project_storage.ps1`; pytest basetemp trong output như AGENTS.
Khi implementation: pytest/ruff và tsc nếu đụng web; live auth/seed/media tests báo riêng, không tự launch model/job.
Full pilot còn cần A01–A11, BDD/nano inputs thật, PII quality/confirmation và cost/capacity gate của hai spec kia.
Bootstrap pass không gỡ LOCAL_SINGLE_USER_LIMITS, không claim tenant safety hoặc guided recipe quality đã đạt.

## 12. Seam cần integration owner chốt tại D0

- Duyệt UUID mapping/API v2/Command.run và session state_version cùng frontend/runtime signatures.
- Schema common/RPC policy: direct TLS PG với verified-claims ordinary transactions, API/worker LOGIN roles giới hạn, exact source_pin references và migrations order.
- Tạo phiên trống đã chốt #83; duyệt exact DTO/sanitization, cấu hình khởi tạo, transaction mappings/pins/receipt và provenance field; không thiết kế lại copied-events.
- Tài nguyên hiện có và `.env` bootstrap đã chốt #83; preflight xác minh mapping/account/namespace/quyền, scoped role thiếu và current embedding-job adoption/cutover. Không đổi project/bucket vì config chưa đủ.
- BDD/model inventory/relationship để sau bootstrap, nhưng gate full pilot/recipe unavailable phải explicit trong UI/PLAN.
- Operator auth backup/restore subject UUID và ACL parity tham chiếu ops spec; không business dump giả full auth backup.
