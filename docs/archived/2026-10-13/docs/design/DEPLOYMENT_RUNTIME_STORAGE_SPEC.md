# Deployment runtime/storage — bản giao việc D0

Ngày 2026-10-09. **DRAFT D0: schema, API v2 và chữ ký dưới đây là đề xuất, chờ duyệt trước implementation.**
Tài liệu này không chứng minh deploy/capacity đã đạt; không thay CONTRACTS §1–20 hoặc công thức thuật toán.
Đọc AGENTS.md → PLAN.md → IMPLEMENT_GUIDE_V2.md → CONTRACTS §14–21 → DEPLOYMENT_PILOT_SPEC.
Nguồn sự thật vẫn theo docs/README.md; integration owner đưa thiết kế được duyệt vào CONTRACTS/architecture trước code.

## 1. Phạm vi, thứ tự và chủ sở hữu

- Đã chốt: Render Static/API, Contabo worker, Kaggle GPU, S3 private; 2–3 account; video upload; reset base 4 giờ.
- Auth Supabase password/operator thuộc DEPLOYMENT_IDENTITY_DATA_SPEC.md; PII owner confirm trước ZIP thuộc DEPLOYMENT_UI_EXPORT_OPS_SPEC.md.
- Delete RESTRICT khi nguồn còn dùng; không user release-pin/retirement nguồn có tham chiếu trong pilot.
- Local `/api/v1` giữ behavior hiện có; deploy `/api/v2` không dùng lại response type đồng bộ cho hành động async.
- Prefix Postgres `rav_pilot_`; HTTP resource UUID map tới core IDs, không viết lại Unit/FieldKey/spec/frame IDs.
- Chưa có subset BDD/checkpoint nano đã provision. Tìm/seed sau khi bootstrap deploy chạy được; thiếu asset chặn guided workflow liên quan.
- Bootstrap operator-only trước để đo D1 bằng fixture/local demo được kiểm inventory; chưa mở pilot hoặc tuyên bố BDD/nano sẵn.
- #83 chốt tái dùng S3/Supabase/Kaggle hiện có, credential trong `.env` repo. Operator chỉ định file explicit để bootstrap; preflight kiểm exact account/bucket/project/region và namespace pilot riêng, không tự tạo/xóa tài nguyên mới.
- Integration owner sở hữu shared schema/RPC/DTO, bootstrap/config và migrations `001_pilot_identity.sql`, `002_pilot_runtime.sql`, `003_pilot_storage.sql`, `004_pilot_worker_objects.sql`, `005_pilot_features.sql`, `006_pilot_curation.sql`, `007_pilot_analysis.sql`, `008_pilot_export.sql`.
- Agent storage, queue/quota và GPU adapter chỉ sửa file thuộc gói được giao; không tự sửa shared contracts/migrations.
- Agent được dùng contract-only mocks trước integration barrier; không coi mocks là RLS/transaction/cloud acceptance.
- Mỗi phiên implementation một lát PLAN; tài liệu này không cấp phép gộp D0–D8 thành một phiên.

## 2. Ranh giới bắt buộc

API làm authz/metadata/RPC/sign URL; không decode, load model, đọc matrix/NPZ, ZIP hoặc điều phối Kaggle.
Worker nhận job DB; mọi CPU/GPU job đi cùng admission/lease/fencing; không public inference endpoint trên Contabo.
DB là authority metadata/queue/quota; S3 là authority bytes; disk worker chỉ cache tái tạo.
Một queue authority claim physical slots; coordinator embedding cũ không được chạy cạnh dispatcher mới.
Enqueue, quota hold, storage hold, pending limit và source pin phải một DB transaction.
Publication, usage settlement và receipt phải idempotent; S3 PUT riêng được reconcile, không giả distributed transaction.
Lease hết hạn chỉ chặn publication; phải xác minh process/session cũ đã dừng trước tái cấp physical slot.
Source/checkpoint/runtime/ordered IDs/hash/context giữ parity; không giảm frames/candidate/flip để qua quota.

### 2.1 Shared types và lock order

`ActorContext(user_id: UUID | None, request_id: UUID)` do transport verified inject; None chỉ local, mọi pilot RPC reject None; input HTTP cấm owner/actor/admin.
`WorkerContext(worker_id: UUID, host_boot_id: UUID)` do worker credential giới hạn inject; user không truyền worker ID.
Identity migration sở hữu `rav_pilot_profile(user_id, active, authz_version)` và `rav_pilot_source_asset`.
Source columns dùng: `id UUID`, `core_source_id TEXT`, `kind VIDEO|IMAGE_SET`, `scope PRIVATE|SYSTEM`, `owner_id`,
`protected`, `raw_key`, `raw_sha256 CHAR(64)`, `size_bytes BIGINT`, `availability READY|UNAVAILABLE|DELETE_PENDING|DELETED`, `resource_version BIGINT`.
IMAGE_SET key/hash là immutable manifest; từng ảnh/nhãn dùng member raw key/hash, không decode manifest như ảnh.
Mọi RPC ghi lấy DB time; thứ tự lock: profile → sources UUID tăng dần → logical resource → admission/quota/account → job → slots.
Worker publish cũng lock profile/source trước job; delete/enqueue/snapshot dùng cùng source-row locks.
Không giữ transaction DB mở trong HTTP S3/provider/model inference; tất cả network work chạy giữa các receipt.
Deadlock/serialization retry hữu hạn với cùng idempotency key; số retry/backoff chốt config, không tạo job mới để retry RPC.

## 3. Logical schema cho migration 002

Mỗi bảng có `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`; field timestamp khác nullable chỉ khi ghi `?`.
UUID PK backend sinh; FK profile/source từ migration 001; mọi FK nghiệp vụ `ON DELETE RESTRICT`, không CASCADE.
Amounts BIGINT không âm; time usage/credit lưu integer milliseconds, bytes BIGINT; không float hoặc số lần bấm.
JSONB phải validate payload_version/shape bằng adapter và CHECK tối thiểu; không nhét FK/owner/amount vào JSON thay columns.
Các column/version/index sau là giao diện cần duyệt; integration owner viết DDL/RLS/RPC và kiểm cả ba tài liệu khớp.
Mọi runtime table ENABLE RLS; revoke anon/authenticated direct WRITE. Queue/attempt/slots/ledger/snapshots chỉ backend role được đọc/ghi.
User job/media/upload status chỉ safe owner-scoped view/RPC, không raw row payload; worker role chỉ permitted RPC/task object access.

### 3.1 Job và physical resources

| Bảng | Columns bắt buộc và ràng buộc |
|---|---|
| `rav_pilot_job` | `id UUID PK`, `owner_id UUID FK profile`, `kind TEXT`, `resource_id UUID?`, `retry_of UUID? FK job`, `input_hash CHAR(64)`, `payload_version TEXT`, `payload JSONB`, `idempotency_key TEXT`, `policy_version TEXT FK quota_policy(version)`, `state TEXT`, `state_version BIGINT >=1`, `attempt_n INT >=0`, `phase TEXT`, `done BIGINT >=0`, `total BIGINT >=0`, `waiting_reason TEXT?`, `queue_deadline TIMESTAMPTZ`, `error JSONB?`, `result_ref UUID? FK artifact`, `updated_at TIMESTAMPTZ` |
| `rav_pilot_job_attempt` | `id UUID PK`, `job_id UUID FK job`, `attempt_n INT >0`, `fence BIGINT >0`, `worker_id UUID`, `host_boot_id UUID`, `state TEXT`, `lease_until TIMESTAMPTZ`, `started_at TIMESTAMPTZ?`, `finished_at TIMESTAMPTZ?`, `process_group JSONB?`, `launch_state TEXT`, `launch_marker TEXT?`, `provider_account TEXT?`, `session_id TEXT?`, `operation_name TEXT?`, `cleanup_done BOOL DEFAULT false`, `session_stop_verified BOOL DEFAULT false`, `usage_state TEXT`, `usage JSONB`, `updated_at TIMESTAMPTZ` |
| `rav_pilot_resource_slot` | `slot_key TEXT PK`, `kind CPU\|GPU\|MEDIA`, `account_or_host TEXT`, `state FREE\|HELD\|QUARANTINED`, `generation BIGINT >=0`, `attempt_id UUID? FK attempt`, `lease_until TIMESTAMPTZ?`, `reconcile_evidence JSONB?`; slot inventory explicit operator config |
| `rav_pilot_dispatch_cursor` | `queue TEXT PK`, `last_owner_id UUID?`, `revision BIGINT`; DB round-robin cursor, không dựa RAM process |

Job kinds: `VALIDATE_UPLOAD`, `RENDER_MEDIA`, `PREPARE_DINO`, `PREPARE_CLIP`, `PROPOSE`, `YOLO_BATCH`, `METRICS`, `EXPORT_BUILD`, `EXPORT_FINALIZE`, `DELETE_SOURCE`.
Job states: `QUEUED|WAITING_RESOURCE|RUNNING|CANCEL_REQUESTED|SUCCEEDED|FAILED|CANCELLED`; `done <= total`.
Attempt states: `CLAIMED|WORKING|STOPPING|CLOSED|LOST`; launch: `NONE|INTENT|LAUNCH_UNCERTAIN|ATTACHED|STOP_PENDING|STOPPED`.
Usage states: `OPEN|UNKNOWN|FINAL`; job terminal không đồng nghĩa GPU slot/usage đã được release.
Unique `(owner_id,kind,idempotency_key)`; cùng key khác input_hash trả conflict, cùng input trả receipt cũ.
Partial unique active `(owner_id,kind,resource_id,input_hash)` trên QUEUED/WAITING_RESOURCE/RUNNING/CANCEL_REQUESTED.
Unique attempt `(job_id,attempt_n)`, `(job_id,fence)`; unique `launch_marker` khi khác NULL.
Index `(state,queue_deadline,created_at)`, `(owner_id,state,created_at)`, attempt `(lease_until,state)`.
Không UNIQUE active theo content hash xuyên owner; system-cache hit chỉ từ permitted immutable system input.

### 3.2 Compute quota và GPU account budget

| Bảng | Columns bắt buộc và ràng buộc |
|---|---|
| `rav_pilot_quota_policy` | `id UUID PK`, `version TEXT UNIQUE`, `config JSONB`, `enabled BOOL`; immutable policy, config validator không cho thiếu D1 caps/base/headroom; migration 002 thêm profile.policy_id FK policy(id) |
| `rav_pilot_quota_window` | `id UUID PK`, `owner_id UUID FK profile`, `resource CPU\|GPU`, `window_start TIMESTAMPTZ`, `window_end TIMESTAMPTZ`, `base_ms BIGINT`, `held_ms BIGINT`, `consumed_ms BIGINT`; UNIQUE owner/resource/start; held+consumed <= base |
| `rav_pilot_quota_bonus` | `id UUID PK`, `owner_id UUID FK profile`, `resource CPU\|GPU`, `granted_ms BIGINT`, `held_ms BIGINT`, `consumed_ms BIGINT`, `operator_id UUID`, `reason TEXT`, `adjustment_key TEXT UNIQUE`; held+consumed <= granted |
| `rav_pilot_quota_hold` | `id UUID PK`, `job_id UUID FK job`, `resource CPU\|GPU`, `generation INT`, `reserved_ms BIGINT`, `consumed_ms BIGINT`, `released_ms BIGINT`, `state OPEN\|FINAL`, UNIQUE job/resource/generation; consumed+released <= reserved |
| `rav_pilot_quota_hold_part` | `hold_id UUID FK hold`, `part_n INT`, `window_id UUID? FK window`, `bonus_id UUID? FK bonus`, `reserved_ms BIGINT`, `consumed_ms BIGINT`, `released_ms BIGINT`, PK hold/part_n; exactly one of window_id/bonus_id |
| `rav_pilot_usage_receipt` | `id UUID PK`, `seq BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE`, `job_id UUID FK job`, `attempt_id UUID FK attempt`, `resource CPU\|GPU`, `settlement_key TEXT UNIQUE`, `actual_ms BIGINT`, `evidence JSONB`, `closed_at TIMESTAMPTZ`; UNIQUE attempt/resource; immutable |
| `rav_pilot_gpu_budget_snapshot` | `id UUID PK`, `provider_account TEXT`, `observed_at TIMESTAMPTZ`, `remaining_ms BIGINT`, `reset_at TIMESTAMPTZ`, `usage_baseline_seq BIGINT`, `external_session_active BOOL`, `operator_id UUID`, `evidence_ref TEXT`, `policy_version TEXT` |
| `rav_pilot_gpu_hold` | `id UUID PK`, `attempt_id UUID UNIQUE FK attempt`, `snapshot_id UUID FK snapshot`, `reserved_ms BIGINT`, `consumed_ms BIGINT`, `released_ms BIGINT`, `state OPEN\|FINAL`; giữ OPEN khi launch/stop chưa xác minh |

Policy base reset cửa sổ UTC cố định: `start = floor(epoch(DB now)/14400)*14400`, end=start+14400 giây; đây là đề xuất D0.
Lazy create window trong read/enqueue transaction; không cộng dồn base, không chuyển hold sang window mới khi reset.
Debit base hiện hành trước rồi bonus FIFO created_at/id; hold_part nhớ đúng nguồn; refund base cũ chỉ về window cũ.
extend_hold append hold_parts từ window hiện hành/bonus theo cùng rules; không rewrite origin của phần đã reserve trước reset.
Bonus không expire trong pilot đề xuất; operator adjustment có reason/idempotency, không sửa balance bằng UPDATE tay thiếu receipt.
CPU actual = tổng user+system CPU ms của process group/chunks thuộc attempt; include decode/text/scoring/PII/ZIP.
GPU actual = session wall ms từ provider create/start được ghi nhận tới stop verified, gồm startup/IO/load/cleanup/retry.
Không biết actual sau crash: usage UNKNOWN, giữ hold; reconciliation/operator evidence trước FINAL, không tự refund toàn bộ.
GPU available = remaining snapshot trừ usage sau baseline, OPEN holds và headroom; snapshot stale/unknown hoặc external active chặn launch.
Baseline ghi usage sequence đã được snapshot bao gồm; không trừ usage cũ hai lần hoặc bỏ session ngoài app.
Global budget tuần/ngày/account và freshness cấu hình sau D1; không hardcode entitlement Kaggle hoặc tự tạo account khác.

Policy config phải có typed required keys (mọi numeric value sau measurement D1, không default):
`cpu_base_ms`, `gpu_base_ms`, `pending_per_user`, `heavy_active_per_user`, `slot_inventory`, `storage_user_bytes`, `storage_global_bytes`;
`max_video_bytes`, `max_duration_ms`, `max_width`, `max_height`, `max_decoded_pixels`, `max_zip_bytes`, `max_media_requests`;
`lease_ms`, `heartbeat_ms`, `queue_deadline_ms`, `run_deadline_ms_by_kind`, `stop_deadline_ms`, `cleanup_deadline_ms`;
`user_get_ttl_ms`, `upload_ttl_ms`, `remote_url_ttl_ms`, `staging_grace_ms`, `gpu_snapshot_fresh_ms`, `cost_snapshot_fresh_ms`;
`gpu_headroom_ms`, `forecast_admission_usd`, `profiles_by_kind`, `worker_rss_bytes`, `worker_temp_bytes`, `chunk_bounds_by_kind`.
All integer capacities/times >0; heartbeat < lease; remote TTL đủ job bounded deadline; missing/inconsistent config không enable policy.
`profiles_by_kind[kind]` freeze estimator version + cold overhead + per-unit/per-MiB CPU/GPU ms và worst-output bytes, measured evidence ref.
Estimate rounding luôn ceil; actual vượt estimate phải extend_hold trước chunk tiếp, không tự charge quá reserved hoặc âm balance.

### 3.3 Storage, nguồn và artifacts

| Bảng | Columns bắt buộc và ràng buộc |
|---|---|
| `rav_pilot_storage_account` | `account_key TEXT PK` (`USER:<uuid>` hoặc `GLOBAL`), `limit_bytes BIGINT`, `persisted_bytes BIGINT`, `reserved_bytes BIGINT`; persisted+reserved <= limit |
| `rav_pilot_storage_hold` | `id UUID PK`, `owner_id UUID FK profile`, `job_id UUID? FK job`, `upload_id UUID?`, `reserved_bytes BIGINT`, `staging_observed_bytes BIGINT`, `persisted_bytes BIGINT`, `released_bytes BIGINT`, `state OPEN\|FINAL`, `receipt_key TEXT UNIQUE`; loại owner/job/upload phải resolve rõ |
| `rav_pilot_source_pin` | `id UUID PK`, `source_id UUID FK source_asset`, `consumer_kind SESSION\|SNAPSHOT\|REFERENCE\|JOB\|EXPORT`, `consumer_id UUID`, `input_role TEXT`, `source_version BIGINT`, `released_at TIMESTAMPTZ?`; UNIQUE source/consumer_kind/consumer_id/input_role |
| `rav_pilot_upload` | `id UUID PK`, `owner_id UUID FK profile`, `reserved_source_id UUID UNIQUE`, `idempotency_key TEXT`, `declared_bytes BIGINT >0`, `filename_display TEXT`, `state CREATED\|UPLOADED\|VALIDATING\|READY\|FAILED\|ABORTED`, `staging_key TEXT UNIQUE`, `capability_expires_at TIMESTAMPTZ`, `canonical_key TEXT?`, `verified_sha256 CHAR(64)?`, `verified_bytes BIGINT?`, `storage_hold_id UUID FK storage_hold`, `validation_job_id UUID? FK job`, `error JSONB?`; UNIQUE owner/idempotency_key |
| `rav_pilot_artifact` | `id UUID PK`, `owner_id UUID? FK profile`, `scope PRIVATE\|SYSTEM`, `source_id UUID? FK source_asset`, `job_id UUID? FK job`, `attempt_id UUID? FK attempt`, `kind TEXT`, `variant TEXT`, `input_hash CHAR(64)`, `object_key TEXT UNIQUE`, `sha256 CHAR(64)`, `size_bytes BIGINT`, `state STAGED\|READY\|DELETED`, `version INT >0`, `policy_version TEXT?`, `manifest JSONB`; scope/owner rule giống source |
| `rav_pilot_media` | `id UUID PK`, `owner_id UUID FK profile`, `source_id UUID FK source_asset`, `unit_id TEXT`, `variant THUMBNAIL\|FRAME\|CONTEXT`, `params JSONB`, `input_hash CHAR(64)`, `state PENDING\|READY\|FAILED`, `job_id UUID? FK job`, `artifact_id UUID? FK artifact`, `error JSONB?`; UNIQUE owner/input_hash |
| `rav_pilot_cost_snapshot` | `id UUID PK`, `observed_at TIMESTAMPTZ`, `period_end TIMESTAMPTZ`, `actual_usd NUMERIC`, `forecast_usd NUMERIC`, `baseline_next_period_usd NUMERIC`, `usage JSONB`, `evidence_ref TEXT`, `operator_id UUID`, `policy_version TEXT` |
| `rav_pilot_admission` | `id TEXT PK` (`GLOBAL`), `state OPEN\|PAUSED`, `reason TEXT?`, `cost_snapshot_id UUID? FK cost_snapshot`, `revision BIGINT`, `updated_at TIMESTAMPTZ`; stale billing tự PAUSED bằng RPC |

Artifact kinds: FEATURE/MEDIA/EXPORT_PREVIEW/EXPORT_BUNDLE/REPORT/BACKUP; export record/evidence/confirmation do UI-export spec sở hữu.
Artifact READY không đồng nghĩa export READY; preview/report qua owner review gate, EXPORT_BUNDLE chỉ sign khi export READY+confirmation hợp lệ.
Artifact manifest freeze core source SHA/spec/model/runtime/ordered IDs/output variant; không signed URL/secret/host path.
Persisted bytes settle theo unique object receipt; cache hit không double charge storage; estimate gồm output worst-case.
Transient JOB pins chỉ service release sau terminal và cleanup/process/session reconciliation; source-row lock trước release.
SESSION/SNAPSHOT/REFERENCE/EXPORT pins không user release/GC trong pilot; profile disable không xóa pins.
Index pins `(source_id) WHERE released_at IS NULL`; physical delete RESTRICT nếu còn một pin hoặc protected.
Pin consumer mapping: SESSION/REFERENCE → session.id; SNAPSHOT → snapshot.id; JOB → job.id; EXPORT → export.id (migration 004).
Consumer insert trigger/RPC kiểm parent tồn tại/owner/read source và relation thật; không trực tiếp cấp INSERT pin cho user.
`input_role` dùng POOL|REFERENCE|INPUT|EXPORT_SOURCE; REFERENCE pin consumer session phải khớp session_reference/version→dataset.source_id.
Storage_hold.upload_id FK thêm sau tạo bảng upload để giải vòng FK; upload/job NULL không được dùng làm hold vô chủ.
Job.result_ref FK thêm sau artifact; migration 004 bổ sung pin EXPORT validator khi có export table; không mở route export trước migration 003.
Worker cache eviction không đổi persisted bytes của S3; staging bytes đang tồn tại không coi đã giải phóng trước delete receipt.

## 4. Shared versioned payloads và HTTP DTO

Input Pydantic strict `extra=forbid`; JSON canonical UTF-8 sorted keys, không whitespace để hash; timestamps ISO UTC.
Hash algorithm giữ core hiện có; `input_hash` runtime thêm owner/resource/version ngoài core hash, không thay core hash.

```python
@dataclass(frozen=True)
class ActorContext:
    user_id: UUID | None  # None chỉ local; pilot reject trước resolve/RPC.
    request_id: UUID

class EnqueueSpec(BaseModel):
    kind: JobKind
    resource_id: UUID | None
    idempotency_key: str
    payload_version: Literal["rav.runtime.job.v1"]
    payload: dict  # resolve qua tagged model theo kind, không dict tùy ý
    expected_resource_version: int | None

class FrozenInput(BaseModel):
    source_id: UUID
    core_source_id: str
    source_version: int
    raw_sha256: str
    raw_key: str
    member_manifest_hash: str | None  # IMAGE_SET phải pin cả member hashes

class AttemptLease(BaseModel):
    job_id: UUID
    attempt_id: UUID
    attempt_n: int
    fence: int
    worker_id: UUID
    slot_key: str
    slot_generation: int
    lease_until: datetime
    payload: dict
```

Payload mọi kind: `version`, `inputs: FrozenInput[]`, `core_spec`, `ordered_unit_ids`, `bindings`, `context`, `output`, `policy_version`.
Helper DTO fields là contract: Progress `{sequence:int>=0,phase:str,done:int>=0,total:int>=done}`; ReserveDelta `{cpu_ms:int>=0,gpu_ms:int>=0,storage_bytes:int>=0}`.
Usage `{cpu_ms:int?,gpu_ms:int?,state:OPEN|UNKNOWN|FINAL,evidence_hash:str,evidence:dict}`; FINAL cấm NULL resource đã dùng.
ObjectReceipt `{artifact_id:UUID,kind:ArtifactKind,variant:str,sha256:str,size_bytes:int,scope:PRIVATE|SYSTEM,source_id:UUID?}`.
OutputManifest `{version:"rav.runtime.output.v1",input_hash:str,artifacts:list[ObjectReceipt],result:dict,cleanup_done:bool}`; result kind-tagged.
CleanupEvidence `{process_exit_verified:bool,session_stop_verified:bool,remote_cleanup_done:bool,observed_at:datetime,proof:dict}`.
LaunchResolution tagged FOUND `{handle:{session_id,operation_name},evidence}` / ABSENT_VERIFIED `{evidence}` / UNKNOWN `{reason}`.
All receipt DTO chứa `{id:UUID,replayed:bool}` và row/version đã persist; adapter không tự tạo receipt ID sau network success.
PREPARE payload feature DINO|CLIP, exact FieldKey, checkpoint/revision/source SHA/runtime/pixel probes; missing fields fail validation.
PROPOSE payload session UUID/core ID, state_version/decision_seq/proposal_count/config/seed/pool/reference/model bindings frozen.
VALIDATE_UPLOAD payload upload UUID, staging key, declared bounds/reserved source UUID; không coi browser checksum là verified.
MEDIA payload server-resolved source/anchor/context decoder spec, variant/size policy; browser không gửi path/frame_idx tùy ý.
EXPORT payload version/receipt/hash/snapshot theo UI-export spec; model/input freeze trước estimate, không infer thêm trong report renderer.
DELETE payload source UUID/version và authorized delete receipt; user không chọn S3 prefix.
DB không lưu presigned URL; worker cấp capability theo attempt ngay trước transfer, TTL bounded bởi deadline.

```json
{"id":"uuid","kind":"PREPARE_DINO","state":"RUNNING","state_version":1,"phase":"decode","progress":{"done":8,"total":16},"attempt_n":1,"waiting_reason":null,"error":null,"resource_id":"uuid","result_ref":null}
```

JobDTO chỉ các field trên; error nullable `{code,message,retryable}`; không session handle/object key/signed URL.
QuotaDTO `{server_time,next_reset_at,cpu:{base_available,bonus_available,held},gpu:{base_available,bonus_available,held},admission:{allowed,reason}}`.
QuotaDTO amounts cùng integer ms; UI đổi đơn vị display, không dùng balance client để quyết định admission.
UploadDTO `{id,state,validation_job_id,source_id,error}`; create thêm `transfer:{method:"POST",url,fields,expires_at}`; header private/no-store.
MediaDTO `{id,state:"PENDING|READY|FAILED",job_id,url,expires_at,sha256,error}`; URL chỉ READY và permission vẫn còn đúng.
Envelope error `{error:{code,message,retryable,request_id,next_reset_at?}}`; status 401/403/404/409/413/422/429/503 theo pilot spec.
Codes cố định: `idempotency_conflict`, `resource_in_use`, `source_unavailable`, `stale_input`, `lease_lost`, `quota_exceeded`,
`storage_exceeded`, `policy_unconfigured`, `dependency_unavailable`, `billing_stale`, `admission_paused`, `gpu_budget_unverified`, `launch_uncertain`, `provider_stop_pending`,
`upload_invalid`, `pixel_parity_mismatch`, `result_invalid`, `proposal_context_changed`, `export_requires_job`.
Outside scope 404; visible forbidden 403; safe message không library traceback/host path/secret.

### 4.1 HTTP callers của runtime — cùng prefix /api/v2

Mọi POST create/retry dùng `Idempotency-Key` header; transport đưa key vào spec nội bộ, không cho owner/key/actor trùng trong body.
Limit/cursor và polling theo policy; list query luôn scope owner trước count/pagination. UUID result chỉ là handle, không quyền truy cập.

| Route | Body / output bắt buộc |
|---|---|
| `POST /sources/{id}/preparations` | `{feature:"DINO"\|"CLIP"}` →202 JobDTO; READY cache hit →200 `{source_id,feature,ready:true,job_id:null}`; feature DINO gồm DINO+pHash |
| `POST /sessions/{id}/proposals` | `{recipe,config?,batch_size,seed?,expected_state_version}` →202 JobDTO; config theo RecipeConfig/Params hiện hành, source/model/seed/input freeze server-side |
| `POST /proposals/{id}/detections` | `{model_id}` →202 JobDTO hoặc200 safe cached evidence handle; chỉ ordered picks của proposal theo CONTRACTS §17/18 |
| `POST /sessions/{id}/metrics` | `{metrics,on,expected_state_version}` →202 JobDTO; names/on theo contract metric, không tự mở benchmark phát triển thuật toán |
| `GET /jobs`, `GET /jobs/{id}` | Page[JobDTO]/JobDTO owner-scoped; GET không compute |
| `POST /jobs/{id}/cancel` | `{expected_state_version}` →200 JobDTO; version là JobDTO.state_version |
| `POST /jobs/{id}/retry` | `{}` + Idempotency-Key →202 JobDTO mới, retry_of nội bộ |
| `GET /jobs/{id}/result` | SUCCEEDED → safe tagged ResultDTO; chưa terminal/success →409 result_not_ready; không general artifact/raw proxy |
| `GET /quota` | QuotaDTO và admission reason; không API top-up cho user |
| `GET /proposals/{id}`, `GET /proposals/{id}/evidence` | Authorized persisted proposal/safe evidence DTO theo contract; không đọc matrices/decode trên API |
| `GET /uploads/{id}`, `POST /uploads/{id}/complete`, `POST /uploads/{id}/abort` | UploadDTO; complete `{}` →202 validation job/receipt (replay cùng job); abort `{}` →200 receipt, cleanup có thể pending |

Job.result_ref là FK artifact nội bộ. `GET result` resolve manifest receipt sau owner/authz checks, trả logical IDs từ **persisted validated publication**:
PROPOSE `{kind,session_id,proposal_id}`; PREPARE `{kind,source_id,feature,ready}`; YOLO_BATCH `{kind,proposal_id,model_id,evidence_ready}`;
METRICS `{kind,session_id,metrics_receipt_id}`; EXPORT_BUILD/FINALIZE `{kind,export_id}`; VALIDATE_UPLOAD `{kind,upload_id,source_id}`; DELETE_SOURCE `{kind,delete_request_id,source_id}`.
Media results dùng MediaDTO route riêng. Không trả object_key/raw_key/session GPU handle, arrays embedding hoặc signed URL từ generic job result.
API chỉ đọc receipt/safe projection; worker phải persist metrics/evidence JSON projection có version cùng publication, không để GET render lại từ Context.
Migration 003 mới mở export result kinds. Parent khóa JSON fixtures cho mỗi tagged result trước giao FE; không agent tự đổi result_ref thành proposal FK.
Queue mapping server-owned: PREPARE_DINO/CLIP → GPU; RENDER_MEDIA → MEDIA; các kind còn lại → CPU. MEDIA vẫn đo/charge CPU; không tạo ngân sách user thứ ba.

## 5. RPC transaction contracts — integration owner viết một nơi

RPC names prefix `rav_pilot_`; JSON returns validate typed adapters; ordinary user không EXECUTE worker/admin RPC.
Tên bảng dưới shorthand: `enqueue` là PostgreSQL function `public.rav_pilot_enqueue`, tương tự mọi RPC khác; không tạo alias authority thứ hai.
Privileged functions pin search_path, revoke PUBLIC/anon EXECUTE; kiểm principal/profile/ownership explicit, không tin service-role đã authz.
Public API đưa trusted user_id/request_id qua server RPC path; body owner bị từ chối; worker role không được đổi account/bonus.

| RPC signature | Receipt trả về |
|---|---|
| `enqueue(p_actor UUID,p_request UUID,p_spec JSONB) -> JSONB` | JobDTO + hold/pin IDs server-only |
| `claim(p_worker UUID,p_host_boot UUID,p_queue TEXT) -> JSONB?` | AttemptLease hoặc NULL/reason; claim job/physical slot cùng transaction |
| `heartbeat(p_attempt UUID,p_fence BIGINT,p_worker UUID,p_slot_generation BIGINT,p_progress JSONB) -> JSONB` | renewed lease/progress monotonic |
| `extend_hold(p_attempt UUID,p_fence BIGINT,p_extra JSONB) -> JSONB` | increment receipt; không âm balance |
| `record_launch_intent(p_attempt UUID,p_fence BIGINT,p_marker TEXT) -> JSONB` | durable intent, existing marker replay |
| `record_session(p_attempt UUID,p_fence BIGINT,p_marker TEXT,p_handle JSONB) -> JSONB` | immutable owned session receipt |
| `publish(p_attempt UUID,p_fence BIGINT,p_artifacts JSONB,p_usage JSONB,p_result JSONB) -> JSONB` | publication receipt + JobDTO |
| `finish_cleanup(p_attempt UUID,p_fence BIGINT,p_evidence JSONB,p_usage JSONB) -> JSONB` | closed cleanup/usage/slot receipt |
| `cancel(p_actor UUID,p_request UUID,p_job UUID,p_expected_version BIGINT) -> JSONB` | JobDTO; request cooperative stop |
| `retry(p_actor UUID,p_request UUID,p_job UUID,p_key TEXT) -> JSONB` | new job receipt referencing retry_of, fresh admission |
| `reconcile_slot(p_worker UUID,p_slot TEXT,p_generation BIGINT,p_evidence JSONB) -> JSONB` | quarantine/free decision, audit receipt |
| `create_upload(p_actor UUID,p_request UUID,p_key TEXT,p_bytes BIGINT,p_name TEXT) -> JSONB` | UploadDTO + server transfer spec |
| `complete_upload(p_actor UUID,p_request UUID,p_upload UUID) -> JSONB` | existing/new validation JobDTO+UploadDTO |
| `publish_upload(p_attempt UUID,p_fence BIGINT,p_verified JSONB) -> JSONB` | source UUID READY + storage receipt |
| `abort_upload(p_actor UUID,p_request UUID,p_upload UUID) -> JSONB` | aborted/cancel-requested receipt; cleanup pending |
| `request_media(p_actor UUID,p_request UUID,p_spec JSONB) -> JSONB` | media/job receipt or existing READY artifact |
| `read_quota(p_actor UUID) -> JSONB` | QuotaDTO after lazy window refresh |
| `operator_adjust(p_operator UUID,p_key TEXT,p_adjustment JSONB) -> JSONB` | bonus/cost/provider snapshot receipt + audit |

### 5.1 Enqueue steps — toàn bộ atomic

1. Lock profile active/authz_version; resolve resource/relations; reject outside scope trước cache/key lookup.
2. Lock sources sorted UUID; require READY/read/external-processing rights; freeze source versions/hashes/member manifest/model binding.
3. Resolve idempotency receipt; same input returns existing job; different input conflict, không reserve lần hai.
4. Lock GLOBAL admission; validate fresh cost/policy, pending/active bounds, input envelope; no D1 config → 503 policy_unconfigured.
5. Lock quota window/bonus rows và USER/GLOBAL storage accounts theo key; compute estimate từ frozen payload+policy.
6. Reserve base→bonus hold_parts, output storage hold và pending entitlement; check capacity không âm/over limit.
7. Insert job QUEUED + source JOB pins + receipts; commit. Validation/media/export wrappers gọi cùng helper transaction.
8. Sau commit trả receipt; failure bất kỳ rollback toàn bộ; không enqueue trước rồi reserve sau bằng RPC khác.
Pending entitlement = đếm active/queued jobs dưới profile-row lock; không cần bảng count độc lập dễ lệch sau crash.
GPU snapshot stale có thể WAITING_RESOURCE bounded; không launch trước dispatch snapshot fresh, không hold vô hạn qua queue_deadline.
DELETE_SOURCE dùng preflight từ identity spec trong cùng transaction: no protected/live pins → tombstone DELETE_PENDING + job.
Delete job không tạo source JOB pin cho chính nguồn cần xóa; không giữ pin tự chặn mình, mọi job/source use mới bị DELETE_PENDING chặn.

### 5.2 Claim, work và publish

1. Claim lock owner/profile/sources trước job; recheck source versions/permissions, queue deadline, policy; stale → fail+safe release.
   DELETE_SOURCE require DELETE_PENDING đúng receipt/version/no protected/no live pins; không dùng READY guard của job thường.
2. Pick owner round-robin then FIFO bằng cursor DB; lock physical slot; FREE mới claim, exhausted → WAITING_RESOURCE reason.
3. GPU claim còn check fresh account snapshot/global reserve; create attempt/fence/slot generation trong cùng transaction.
4. Freeze payload trên attempt; heartbeat chỉ cùng worker/fence/slot generation; progress sequence/done không được giảm.
5. Work chunks kiểm cancel/deadline/lease/resource cap; trước chunk over-estimate gọi extend_hold, reject thì stop rõ.
6. Write artifact S3 key attempt riêng; full SHA/size/result validation; artifact metadata chưa READY.
7. Publish/settle transaction dùng cùng thứ tự §2.1: profile → sources → logical resource → admission/quota/storage accounts → job → slot; require lease+fence current, context/state/hash/authz khớp.
   DELETE_SOURCE kiểm authorized delete receipt/current tombstone resource_version/no live pins và scoped physical-delete evidence.
   Delete publish dùng DELETE_PENDING guard, không require raw còn READY; atomic source DELETED + source_delete receipt + job/usage.
   Lỗi/cancel sau physical delete giữ DELETE_PENDING để reconcile; không tự restore READY khi bytes có thể đã mất.
8. Proposal stale không lưu proposal/evidence; prepare chỉ READY khi đủ DINO+pHash hoặc CLIP đúng ordered IDs, finite/L2/pixel/runtime.
9. Publish result/artifact receipt, storage delta một lần; chỉ insert immutable usage receipt khi actual FINAL, usage OPEN ghi attempt JSON.
   Remote cleanup_done required theo CONTRACTS §19–20; settlement_key replay phải cùng actual/evidence hash, khác payload trả conflict.
10. GPU stop chưa verified giữ slot/global hold/usage OPEN và STOP_PENDING; retries/new launch account bị chặn.
11. finish_cleanup với evidence stop/process exit verified settle final usage/release unused compute/JOB pins/slot; receipt replay no-op.
12. Worker cache chỉ publish từ READY artifact receipt; lỗi DB không sửa readiness local để giả success.

Cancel QUEUED: terminal CANCELLED, release unused compute/pending; staging/artifact bytes chỉ release sau cleanup receipt.
Cancel RUNNING: CANCEL_REQUESTED, worker cooperative stop rồi supervisor kill process group nếu cần; charge consumed usage.
Retry explicit tạo job mới `retry_of`; không sửa payload/job cũ; valid feature cache tái dùng có authz.
Confirmation không copy sang export khác/byte hoặc policy khác. Ngoại lệ EXPORT_FINALIZE retry giữ **cùng export/payload/confirmation còn hạn** theo UI-export spec, recheck owner/rights/quality/expiry và fresh quota; không rerun PII. Hết hạn hoặc bytes/policy đổi phải export mới và review lại.
User retry không tự giải phóng launch-uncertain/stop-pending account; system error adjustment chỉ operator có audit.

## 6. Upload và media implementation sequence

### 6.1 Direct presigned POST

`POST /api/v2/uploads` body `{filename,declared_bytes}` + `Idempotency-Key` header → create_upload; server reserve staging/raw bytes.
Hold upload ít nhất declared staging + worst-case canonical bytes; không chỉ giữ một bản khi hai object cùng tồn tại.
API signer cấp POST đúng staging key/content-length-range/TTL; không filename làm shell/path; multipart resumable Hướng tới.
`POST /uploads/{id}/complete` authz → HEAD bounded timeout → complete_upload; request không decode/tính full SHA.
Complete HEAD không chứng minh bytes hợp lệ; retry/crash trả cùng validation job; browser có thể replay staging tới hết hạn.
Worker tải bounded stream vào attempt workspace; full SHA → probe/decode sandbox non-root → validate type/duration/pixels/frame limits.
Worker upload chính file local đã verify vào canonical key khác, never browser-writable; không CopyObject từ staging mutable.
Rehash trước/sau upload + HEAD checksum/size; freeze verified receipt; canonical object thiếu/mismatch không source READY.
publish_upload atomic source insert/core ID mapping/storage settle/job receipt; cùng upload không tạo hai source/charge hai lần.
Crash sau canonical PUT trước DB: reconcile receipt/key/hash; quarantine orphan nếu chưa đủ evidence, không auto nhận catalog.
Fail/abort giữ staging cleanup task; replay còn hiệu lực nghĩa cleanup không được coi final trước capability expiry+configured grace.
Không xóa raw source để trả quota; source READY không upload lại tại canonical key; đổi byte là resource/version mới theo identity contract.
Upload validation không chạy DINO/CLIP/YOLO/PII; upload invalid trả FAILED, không READY giả.

### 6.2 Media request/status

`POST /api/v2/media/requests` body `{source_id,unit_id,variant:"thumbnail|frame|context",max_side?}` → request_media.
Server resolve Unit exact/source membership, permitted context và decoder spec; variant nhỏ không thay pixel exact scoring/export.
Cache READY hit: authz lại rồi signed GET; miss: atomic enqueue RENDER_MEDIA với MEDIA slot/budget/rate guard, trả PENDING.
Từ DEP7-Frames (CONTRACTS §21.20): thumbnail/frame lookup frame set READY, thiếu → NOT_PREPARED, không RENDER_MEDIA; chỉ context còn enqueue.
`GET /media/requests/{id}` chỉ đọc/authz/sign READY URL; không tạo decode/inference job bằng GET.
`GET /jobs/{id}`, `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/retry` owner-scoped, list scope trước pagination/count.
Context ±vài giây giữ baseline; không bỏ context clip vì nhẹ máy; clip raw chỉ preview riêng, không export PII bundle.
Response URL private/no-store; TTL từ policy, không hứa instant revoke; download capability reusable đến expiry.
Logout/account switch frontend xóa URL/query/cache theo UI spec; không public immutable cache cho private media.
Resource pending/delete/disable chặn URL mới; already-issued capability được giải thích theo pilot spec.

### 6.3 S3 namespace và cache keys

`RAV_PILOT_S3_PREFIX` bắt buộc operator cấp trong bucket hiện có, không default root hoặc dùng chung prefix baseline. Tái dùng bucket đã chốt #83; preflight kiểm target/prefix/IAM/CORS trước ghi.
Staging là prefix trong bucket hiện có. Preflight đọc trạng thái versioning thực; không tự tạo bucket khác hoặc tắt versioning. Versioning enabled mà chưa có approved version-meter/cleanup → upload not-ready, báo đúng gate cần bổ sung ở D0/D1.
Key builder only server: `<prefix>/system/<seed_version>/...`; `private/<owner_uuid>/<source_uuid>/raw/<sha>/<ext>`;
`private/<owner_uuid>/artifacts/<artifact_uuid>/<sha>`; `staging/<owner_uuid>/<upload_uuid>/video`;
`private/<owner_uuid>/frames/<frame_set_uuid>/<frame_idx>/<variant>/<sha>.jpg`; `system/<seed_version>/frames/<core_source_id>/<frame_set_uuid>/...` (§21.20);
`jobs/<job_uuid>/<attempt_uuid>/{manifest,progress,result}`; `backups/<receipt_uuid>/...`.
Mọi fragment private/staging/jobs/backups cũng luôn join `<prefix>/`; manifest/progress dùng .json, result feature dùng .npz.
Không accepts bucket/key/prefix user; normalize/allowlist server key, deny traversal/absolute path/cross namespace.
Raw/member/checkpoint URLs GET; remote result/progress PUT đúng attempt key; remote không AWS/Supabase/Kaggle admin secrets.
Private cache root partition owner+resource bên ngoài core Part/FieldKey; system source shared chỉ immutable+permitted read.
Feature authoritative artifact manifest map HTTP UUID→core source/spec/FieldKey/ordered IDs, restore mmap/cache atomic trên worker.
Cache eviction chỉ temp/cache do service quản lý có manifest, không raw sole copy, live attempt workspace hoặc pinned artifacts.
GC kiểm receipt/lease/age/capability expiry; không bulk-delete prefix và không startup quét workspace worker khác.

## 7. Recovery, launch uncertainty và runtime entrypoints

### 7.1 Kaggle lifecycle

Trước create_session: claim GPU slot/global hold → persist launch intent với marker `pilot:<deployment>:<job>:<attempt>:<fence>`.
Adapter phải có `reconcile_launch(marker)` trả FOUND(handle)|ABSENT_VERIFIED(evidence)|UNKNOWN(reason).
Provider discovery capability phải được xác minh tại integration; không tìm được API/marker đáng tin → UNKNOWN, không đoán ABSENT.
create_session timeout/crash trước record_session → LAUNCH_UNCERTAIN; giữ slot/holds, block retry launch và báo operator.
FOUND attach cùng owned session; ABSENT_VERIFIED được coordinator hiện hành retry create cùng marker; UNKNOWN không tạo session mới.
Record handle immutable; session execute launch worker có remote lock trong chính session, không coi lock này giải create-session gap.
Worker S3 progress pin job/attempt/fence/manifest/monotonic sequence; signed URLs không DB/history/log.
Result upload trước terminal cleanup_done; validate NPZ SHA/shape/dtype/IDs/finite/L2, DINO pHash và pixel/model/runtime hashes.
Detach/lease loss worker cũ không publish/stop session tùy ý; coordinator mới reconcile handle rồi attach/stop theo authority.
Stop errors → STOP_PENDING; provider verified stopped mới global usage FINAL/GPU slot FREE, không chỉ HTTP stop request accepted.
CPU slot expired → QUARANTINED; supervisor check host boot/process start token/group exit trước reconcile_slot; tránh PID reuse.
DB outage: pause claims/publication, existing bounded chunk stop/detach policy; không local fallback bỏ authz/quota.

### 7.2 Concrete Python adapter signatures (file mới là đề xuất)

```python
# src/rav/store/pilot_runtime.py: chỉ DB/RPC + typed receipt; không ML imports.
def enqueue(actor: ActorContext, spec: EnqueueSpec) -> JobDTO: ...
def claim(worker: WorkerContext, queue: str) -> AttemptLease | None: ...
def heartbeat(lease: AttemptLease, progress: Progress) -> AttemptLease: ...
def extend_hold(lease: AttemptLease, extra: ReserveDelta) -> HoldReceipt: ...
def publish(lease: AttemptLease, outputs: OutputManifest, usage: Usage) -> PublicationReceipt: ...
def finish_cleanup(lease: AttemptLease, evidence: CleanupEvidence, usage: Usage) -> CleanupReceipt: ...
def cancel(actor: ActorContext, job_id: UUID, expected_version: int) -> JobDTO: ...
def retry(actor: ActorContext, job_id: UUID, idempotency_key: str) -> JobDTO: ...
# src/rav/worker/gpu.py: provider credentials chỉ worker; no direct DB UPDATE.
def reconcile_launch(marker: str) -> LaunchResolution: ...
def run_gpu(lease: AttemptLease, inputs: FrozenInputs, ports: WorkerPorts) -> OutputManifest: ...
# src/rav/store/pilot_storage.py + service/pilot_media.py
def build_key(namespace: str, owner: UUID | None, receipt: ObjectReceipt) -> str: ...
def verify_canonical(lease: AttemptLease, upload: UploadReceipt) -> VerifiedRaw: ...
def request_media(actor: ActorContext, request: MediaRequest) -> MediaDTO: ...
def get_media(actor: ActorContext, media_id: UUID) -> MediaDTO: ...
```

API entry `python -m uvicorn src.rav.api.pilot:app`; worker entry `python -m src.rav.worker --mode pilot`.
DEP1 dùng `RAV_PILOT_STAGE=OPERATOR_BOOTSTRAP`; worker `--mode operator-bootstrap` chỉ process/infra probes, **không dispatcher claim/recovery/AI**. Mode pilot chỉ bật sau migrations/policy/task gates liên quan.
Bootstrap process vẫn sống khi schema/model/policy chưa có: /health process-only; operator readiness trả riêng bootstrap_ready/pilot_ready và blocked codes, không fake PILOT_READY hay crash-loop. Schema thiếu chặn business endpoints bằng503 schema_not_ready.
Bounded measurement harness chạy riêng bởi operator sau kiểm namespace/input/test-envelope/credit authorization; không bootstrap tự launch GPU. Production D1 policy cần evidence sau đo; fixture envelope không được dùng làm credit/cap mở user admission.
Local `python -m uvicorn src.rav.api:app` và CLI hiện có giữ nguyên; không tự chuyển actor local vào deploy.
Integration barrier: import submodule vẫn chạy package __init__; API target mới một mình không tắt App/recovery cũ.
Integrator làm api/service/commands package initializers nhẹ/lazy trước: legacy app/create_app/COMMANDS chỉ load khi entry local yêu cầu.
`service.App.spec`/fields và local command registrations import pipeline/FieldStore trong nhánh local, không top-level import ở pilot.
Gate fresh API-profile process: import pilot + health/list/job không có torch/ultralytics/av/numpy/legacy recovery thread trong sys.modules/process.
API profile `requirements-api.txt`: FastAPI/HTTP/auth/config/schema/S3 signing; no Torch/Ultralytics/PyAV/numpy inference imports.
Worker profile `requirements-worker.txt`: runtime pinned PyAV/FFmpeg/Pillow/numpy/ML/provider deps; model manifests offline có SHA/license.
Integration owner chọn exact versions/lock sau parity; file requirements chưa được tạo bởi lát docs này.
DB transport theo identity spec: direct TLS PG, API ordinary transactions có verified claims/RLS, backend/worker RPC roles giới hạn; không đưa DB credential sang browser.
Config bắt buộc: deployment ID, DB roles/schema version, S3 prefix/bucket/region, worker ID/cache/tmp roots, slots, D1 policy.
Credential bootstrap từ `.env` repo khi operator chỉ định explicit (CLI draft `--env-file .env`); host API/worker dùng injected environment đã validate theo role. Inventory chỉ báo tên config/quyền thiếu, không giá trị secret; credential hiện có không thay scoped PG roles D0 còn thiếu.
Missing config/ref/model/policy → safe pilot-not-ready; bootstrap missing-content gates được báo riêng như trên. Không download model lúc API boot, tự chọn env file/account khác hoặc fallback credential admin.

### 7.3 Cutover dispatcher local/cloud cũ

1. Operator freeze admission và backup; inventory legacy embedding jobs/session handles/progress/results, không xóa resource cũ.
2. Baseline API cũ ngừng enqueue và recovery; drain/verified-stop owned sessions; UNKNOWN session làm cutover GPU BLOCKED.
3. Import metadata/core mapping; artifact đã đủ validation mới readiness, không migrate queued row thành done bằng cảm tính.
4. Integration owner record cutover receipt; deploy API không instantiate local App/prepare_queue/start_recovery hoặc scan local catalog.
5. Bật một pilot worker authority namespace; legacy table không còn dispatcher chạy. New API chỉ enqueue pilot RPC.
6. Smoke restart API/worker và verify one account/session; rollback pause pilot admission và reconcile pilot sessions trước đổi runtime.
7. Không bật lại legacy dispatcher khi pilot owned session chưa verified stop; local demo riêng không được nhận pilot queue.

## 8. Gói giao agent, deterministic fixtures và gates

| Gói/file ownership | Input contract → output | Test bắt buộc và barrier |
|---|---|---|
| Integration: shared types/DTO, `store/migrations/001_pilot_identity.sql`, `002_pilot_runtime.sql`, `003_pilot_storage.sql`, `004_pilot_worker_objects.sql`, `005_pilot_features.sql`, `006_pilot_curation.sql`, `007_pilot_analysis.sql`, `008_pilot_export.sql`, API/service bootstrap/config | D0 approved → DB/RLS/RPC/runtime ports | Real disposable Postgres transaction/RLS; chưa có D1 config thì no launch; source/core mapping parity; chỉ integrator sửa shared files |
| Storage: `store/pilot_storage.py`, `service/pilot_upload.py`, `service/pilot_media.py`, `api/pilot_upload.py`, `api/pilot_media.py`, tests riêng | trusted Actor + runtime RPC → canonical/media receipts | Replay staging, canonical mismatch, crash PUT→DB, A/B/system scope, VFR exact/context; mock RPC trước barrier, real storage+DB sau |
| Queue/quota: `store/pilot_runtime.py`, `worker/dispatcher.py`, `worker/supervisor.py`, tests riêng | approved RPC/types → job/slot/hold/usage lifecycle | Concurrent enqueue/reset/bonus/settle/cancel/delete; fence stale; expired lease process alive; no duplicate settlement/launch; không tự sửa SQL |
| GPU/cache: `worker/gpu.py`, `pipeline/remote_s3_embed.py`, `pipeline/s3_clip_worker.py`, scoped cache adapter, tests riêng | AttemptLease+frozen sources/global hold → validated result/cleanup evidence | FOUND/ABSENT/UNKNOWN launch; ACK lost; stop pending; corrupt NPZ/progress/runtime/pixel; same session recovery; local baseline regression |

Integration order: identity/shared types → queue/quota RPC/dispatcher → storage/media/templates runtime integration → heavy curation/export.
Storage/media/GPU có thể viết contract-only mocks song song; không vượt barrier bằng local queue hoặc reservation rời transaction.

Fixture F1: users A/B active, system video S, A video VA và B identical bytes VB; UUID khác, core hash giữ; dedup không lộ B.
F2: VFR numbered frames + RGB probes đầu/giữa/cuối; assert exact frame_idx/core IDs/FieldKey/spec/hash bằng baseline.
F3: DB clock trước/sau reset; open base+bonus hold qua cửa sổ; settlement replay không refund base cũ vào base mới.
F4: hai enqueue cùng key/input → một job/hold/pin; khác input cùng key → conflict; hai enqueue/delete source lock race không mất nguồn.
F5: worker W1 lease mất, W2 claim; W1 publication/heartbeat bị chặn; physical process sống thì W2 chưa có slot chạy.
F6: provider create succeeded/ACK lost → UNKNOWN giữ slot; FOUND attach đúng handle; stop accepted nhưng chưa verified không release.
F7: staging overwrite sau verification; canonical bytes và source SHA vẫn đúng file đã verify; repeated complete một source/charge.
F8: export build success→NEEDS_CONFIRMATION riêng; không CPU slot/unused hold chờ owner; confirm enqueue FINALIZE job mới theo payload hash.
F9: DELETE_SOURCE với active/persistent pin → resource_in_use, availability không đổi; transient cleanup không đụng snapshot pin.
F10: cost/provider snapshot stale, missing policy/model, DB/S3 outage → safe reason, no fallback/raw delete/second GPU launch.
Mỗi fixture mock có expected receipt/amount/state/fence cụ thể; integration owner điền amounts fixture, không coi fixture numbers là production quota.

## 9. D0 review và D1 numbers — không hỏi nhầm loại quyết định

D0 duyệt names/schema/RPC/DTO/APIv2, window anchor/bonus policy, state transitions, source pins, cloud namespace và cutover ownership.
D1 đo actual host/API/GPU/media/profile; chốt base_ms, bytes/user/global, duration/resolution/decoded pixels, chunk/RSS/disk/rate,
lease/queue/run/stop/cleanup deadlines, URL TTL/grace, pending/media slots/fairness/headroom, provider/cost snapshot freshness.
Không đưa giá trị đề xuất pilot thành approved default; config thiếu measurement gate thì operator-only/not-ready, không mở user compute.
Giữ 500 MiB và guard thuật toán hiện có theo CONTRACTS khi đo; thay envelope/algorithm cần quyết định người dùng, không agent tự hạ.
Bootstrap operator-only có thể pass trước BDD/nano; full pilot cần inventory/licence/model relation/input eligibility được duyệt và end-to-end gates.
Local demo seed gồm phiên+lịch sử mẫu SYSTEM/read-only mọi user xem để học; không tạo session hoặc copy history tự động vào mỗi account.
Bắt đầu từ mẫu explicit chỉ tạo PRIVATE session trống cùng nguồn/cấu hình khởi tạo (#83); core source identities giữ nguyên, provenance chỉ liên kết template. Không copy proposal/decision/snapshot/job/export/metric/confirmation, không enqueue tự động.
Không chia sẻ mutable private review state; mappings/pins/receipt và seed/import do identity spec, worker không tự scan mọi SQLite record thành system.
Chưa đạt readiness: policy unconfigured, migrations chưa approved, launch discovery chưa verified, source/model unavailable, hoặc live acceptance chưa chạy.
Docs-only verification: link/name/schema seam đồng nhất, diff chỉ owned doc; không install/cloud/model/test nặng/commit/push trong lát này.
