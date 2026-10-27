# Deployment pilot — UI, export PII/report và vận hành

Trạng thái: **bản chi tiết D0 để review; chưa triển khai hoặc chứng minh feasibility**.
Đọc theo thứ tự: AGENTS.md → PLAN.md → IMPLEMENT_GUIDE_V2 → CONTRACTS §14–21 → spec pilot → file này.
Nguồn thiết kế vẫn là CONTRACTS + architecture + UI_DESIGN; integration owner đưa các mục được duyệt vào đó trước code.
Không dùng file này để âm thầm sửa chữ ký local, sampling, pixel, FieldKey hoặc scoring.

## 1. Quyết định, seam và phạm vi

Đã chốt: Supabase email/password; operator cấp/reset tài khoản; signup đóng ở provider và API.
Đã chốt: owner xem ảnh đã che và report, xác nhận trước khi hệ thống tạo ZIP có thể tải.
Đã chốt: delete RESTRICT nguồn còn snapshot/phiên/job tham chiếu; pilot không release-pin, retirement hay cascade.
Đã chốt host: Render image-backed Web Service cho frontend SPA + Render API nhẹ (#100); Contabo AI worker; Kaggle GPU; private S3 + Supabase.
Evaluation phát triển thuật toán chạy local riêng; report nghiệp vụ dưới đây vẫn thuộc ứng dụng pilot.
Đã chốt mẫu là phiên/lịch sử để mọi account xem cách dùng, chỉ đọc; #83 chốt bắt đầu từ mẫu chỉ tạo phiên riêng trống cùng nguồn/cấu hình khởi tạo, không copy history hoặc tự đề xuất.
Hiện chưa có BDD subset/nano reference trên máy; người dùng muốn bootstrap deploy trước, tìm/provision sau.
#83 chốt dùng S3/Supabase/Kaggle hiện có, credential trong `.env` repo. Target/namespace/prefix/quyền phải preflight kiểm; scoped roles/config kỹ thuật vẫn draft D0, không suy credential đã đủ.

Đề xuất D0 chung: deploy /api/v2; local /api/v1 giữ nguyên và không mount trong deploy mode.
Resource HTTP là UUID ánh xạ tới core IDs cũ; không đổi Unit/spec/FieldKey để thêm owner.
ActorContext(user_id: UUID | None, request_id: UUID) do transport inject; None chỉ local, deploy bắt buộc subject đã verify/profile.active.
Body/query không có owner_id, actor, bucket, object key, host path hoặc credential.
Tên route/type/table trong file này là đề xuất D0, chỉ trở thành contract sau integration/review.
Số credit/caps/lease/deadline/URL TTL/confirmation expiry/PII quality do D1 đo và duyệt; không lấy số giả làm default deploy.

| Tài liệu | Sở hữu interface |
|---|---|
| DEPLOYMENT_IDENTITY_DATA_SPEC.md | Actor/authz, resource mapping, source/session/snapshot/schema/RLS và migration |
| DEPLOYMENT_RUNTIME_STORAGE_SPEC.md | JobDTO/QuotaDTO/MediaDTO, admission, job/quota RPC, upload/media/storage và fencing |
| File này | FE behavior, export-specific contract, PII/report artifact mapping và operator procedures |
| Integration owner | DTO/schema/migration/route chung, bootstrap, CONTRACTS/architecture/PLAN/decision log |

Không thêm auth provider, LLM/report service, public AI API, payment hoặc thư viện FE chỉ để làm pilot.
Fixtures không chứa secret/data thật; phép thử live/provider cần phạm vi riêng và quyền đã được cấp.

## 2. Màn hình và state frontend

Mọi fetch đi qua src/web/src/api.ts; giữ components/renderers hiện có và một nguồn state cho mỗi resource.
Deploy route đề xuất: /login, /library, /sessions/new, /sessions/:id, /exports/:id.
Không mở local import-folder, user dataset/model upload hoặc raw ZIP bằng việc gọi URL trực tiếp.

| State auth | UI và hành vi |
|---|---|
| CHECKING | Không render catalog cũ; xác minh provider session và GET /api/v2/me |
| SIGNED_OUT | Login email/password; không signup/anonymous; link hướng dẫn liên hệ operator để reset |
| SIGNED_IN | Có provider token và me.active; mọi request gắn Bearer token |
| REFRESHING | Provider refresh một lần đồng thời; không lặp vô hạn hoặc gửi write hai lần |
| DISABLED/EXPIRED | Hủy requests/polling, clear data/media; hiển thị lý do an toàn rồi login |

Login thành công chỉ điều hướng khi me hợp lệ; lỗi login không phân biệt tài khoản có tồn tại hay không.
Đổi password dùng provider flow; reset qua operator, không tự thêm SMTP/email onboarding.
Logout/đổi account tăng auth_generation; abort fetches, clear session/catalog/job/quota/URL/object-URL state.
Mọi response kiểm generation và user trước apply; response A tới sau khi login B phải bị bỏ.
Không lưu private catalog/media/ZIP vào localStorage, service worker hoặc cache public.
Provider quản lý token theo SDK được duyệt; không log token, password hoặc signed URL.

Catalog có hai nhóm “Của tôi” và “System/demo chỉ đọc”; danh sách/count/pagination từ server đã scope.
Thêm khu “Phiên mẫu” read-only có lịch sử proposal/keep-drop/snapshot để hiểu cách dùng.
Màn mẫu không có Keep/Drop/Undo/Propose/Export/Cancel của resource shared; mở lịch sử không enqueue compute.
Nút “Tạo phiên riêng từ mẫu” gọi command identity spec; server lấy cấu hình khởi tạo và tạo session trống với owner hiện tại, provenance liên kết template.
Routes identity: GET /api/v2/demo-templates, GET /api/v2/demo-templates/{id} (chi tiết/lịch sử) và POST /api/v2/demo-templates/{id}/fork {name?} + Idempotency-Key.
Sau tạo chuyển sang UUID phiên owner mới ở trạng thái chưa có đợt duyệt: kept/drop rỗng, counts proposal/decision/snapshot bằng 0, không history/job/export/metric. Hiển thị thao tác chủ động lấy đợt đầu, không auto POST propose khi navigate/reload. Proposal/snapshot/export/job phát sinh của A không xuất hiện với B.
Không copy legacy events/kết quả/confirmation, signed URL hoặc quyền ghi của template; không hiện raw export legacy của mẫu. Màn mẫu vẫn hiển thị history gốc chỉ đọc, không trộn vào màn riêng.
Model_guided/text_model_guided thiếu checkpoint/train provenance phải disabled với reason missing_model_training_provenance.
BDD reference chưa provision ghi “chưa có”; không gán COCO nano thành model train BDD hoặc đổi nghĩa reference.
Session writes giữ hàng đợi tuần tự; proposal/snapshot/export chờ các keep/drop được server xác nhận.
409 state_version conflict: tải state mới, giữ lựa chọn chưa gửi rõ ràng; không tự ghi đè quyết định của tab khác.
Job polling không xáo trộn proposal/order/focus đang duyệt hoặc gọi proposal lần nữa.

| Media state | UI và API behavior |
|---|---|
| Chưa yêu cầu | Lazy request thumbnail khi nhìn thấy; full frame/context chỉ khi mở viewer |
| PENDING | Placeholder + phase; poll GET receipt, không gửi POST lặp để “thử tải” |
| READY | Dùng URL tới expires_at; không suy URL từ source/unit/hash |
| URL expired | Xin capability mới cho cùng request qua route read/status đã duyệt; không đổi source |
| FAILED/403/404 | Hiện lỗi quyền/nguồn an toàn; không dùng raw URL/cache account khác |

POST /api/v2/media/requests body: {source_id,unit_id,variant:thumbnail|frame|context,max_side?}.
GET /api/v2/media/requests/{id} chỉ đọc status/cấp URL hợp lệ; không tự enqueue inference mới.
MediaDTO do runtime spec sở hữu: mọi fields id,state:PENDING|READY|FAILED,job_id,url,expires_at,sha256,error đều có; fields chưa có giá trị là null, không optional/omitted.
max_side chỉ áp dụng preview; scoring/export phải exact full-size pixel từ snapshot.
Browser giới hạn download concurrency bằng policy đã duyệt; không preload cả video/ZIP hoặc base64 ảnh trong DTO.
Metadata/capability responses dùng Cache-Control: private, no-store; CORS không thay authz.

| Job state | UI/action |
|---|---|
| QUEUED | Vị trí/phase nếu server có; Cancel, không hiển thị như inference đã chạy |
| WAITING_RESOURCE | waiting_reason và deadline/reset nếu có; không hứa ETA |
| RUNNING | phase + progress.done/total; cho giữ/bỏ nhưng result stale phải do server từ chối |
| CANCEL_REQUESTED | “Đang dừng/dọn”; không báo đã hoàn credit hoặc đã thả GPU |
| SUCCEEDED | Reload đúng resource/result; không đồng nhất với export READY |
| FAILED | Safe error + Retry nếu retryable; giữ input form, không đổi recipe |
| CANCELLED | Trạng thái terminal; retry là attempt/job mới theo runtime contract |

JobDTO: id,kind,state,state_version,phase,progress:{done,total},attempt_n,waiting_reason,error,resource_id,result_ref.
Poll bằng policy/backoff server/client đã duyệt; tab hidden/network offline pause hoặc giảm tần suất.
Network error không là job FAILED; reload dùng job receipt durable, không launch lại.
Cancel/retry chỉ qua endpoint owner-scoped; FE không được biết session GPU handle.
Quota hiển thị CPU/GPU riêng: base_available, bonus_available, held, next_reset_at và server_time.
admission.allowed/reason là quyết định server; FE clock không tự reset balance hoặc cho chạy.
429 giữ form/input và hiện reason/reset; global GPU hết vẫn chặn dù user còn credit.
Không biến cache hit thành compute debit mới; không tự fallback CPU/model khác.

## 3. API client và route/payload đề xuất

api.ts chuẩn hóa lỗi thành {status,code,message,retryable,next_reset_at?,request_id?}; không expose stack/path.
401: refresh token một lần; nếu thất bại clear auth. 403/404 không retry để dò resource.
409: cần reconcile state/input; 413/422: sửa input; 429: chờ policy; 503: backoff hữu hạn theo retryable.
Request write có Idempotency-Key UUID do client giữ theo action + frozen input; mất response giữ key để query/retry.
Payload đổi phải key mới; server thấy cùng key nhưng input hash khác trả 409 idempotency_conflict.
Không replay write sau refresh nếu không có idempotency/CAS contract; GET retry không tạo job.
Idempotency được scope owner/action; capability URLs không dùng làm idempotency key.

| Method /api/v2 | Input | Output / hiệu ứng |
|---|---|---|
| GET /me, GET /quota | Bearer token | me/QuotaDTO từ identity/runtime spec |
| POST /snapshots/{snapshot_id}/exports | {format:"pii_dataset_v1"} + Idempotency-Key | 202 ExportDTO + build job; actor/snapshot từ server |
| GET /exports/{id} | Không body | ExportDTO/status; không inference |
| GET /exports/{id}/review | Không body | Frozen review manifest + URLs cho derivative/report đã che |
| POST /exports/{id}/confirm | {payload_hash,review_receipt_id} + Idempotency-Key | 202 ExportDTO + export_finalize job mới |
| GET /exports/{id}/download | Không body | READY capability + SHA/size/expiry; chưa ready → 409 export_not_ready |
| POST /jobs/{id}/cancel, /retry | Theo runtime spec | JobDTO; confirmation không tự được reuse cho byte khác |

Deploy không mount legacy export/raw snapshot download. Compatibility handler nếu cần chỉ trả export_requires_job.
Không tự tạo CPU/PII job bằng GET download/review; POST create/confirm là mutation có admission.

CapabilityDTO `{url,expires_at,sha256,size_bytes}` chỉ cấp sau authz, không persist URL trong DB.
ReviewDTO `{export_id,review_receipt_id,payload_hash,report_core_hash,review_expires_at,n_members,contact_sheet:CapabilityDTO,report_html:CapabilityDTO,members:[{ordinal,unit_id,output_sha256,thumbnail:CapabilityDTO,full:CapabilityDTO}],next_cursor}`.
Review pagination/cursor giữ cùng receipt/payload; contact sheet gồm toàn bộ members. UI tải full chỉ khi mở viewer, không fetch tất cả URLs ngay. Receipt/preview/report thuộc cùng frozen core; timestamp URLs không tham gia payload hash.
DownloadDTO `{export_id,artifact_id,payload_hash,sha256,size_bytes,filename,url,expires_at}`; chỉ READY trả200, không URL null/partial. Output filename server sinh, artifact_id không cấp generic raw access.

~~~json
{"id":"<export-uuid>","snapshot_id":"<snapshot-uuid>","state":"NEEDS_CONFIRMATION",
 "build_job_id":"<job-uuid>","finalize_job_id":null,"payload_hash":"<sha256-hex>",
 "review_receipt_id":"<review-uuid>","review_expires_at":"<UTC-from-policy>",
 "n_members":2,"error":null}
~~~

ExportDTO states: REQUESTED, PROCESSING, NEEDS_CONFIRMATION, FINALIZING, READY, FAILED, CANCELLED, EXPIRED.
Output timestamps UTC; absent capability/hash before publication is null, not empty URL or guessed readiness.
Export FAILED includes safe {code,message,retryable}; required source_unavailable error can list authorized affected members.
Receipt expiry returns 409 review_expired; changed payload returns 409 payload_mismatch; already confirmed same hash returns existing result.
Review endpoint trả 409 export_not_reviewable nếu chưa đủ output hoặc thiếu policy/quality gate.
GET download chỉ READY; failed/partial/no approved quality gate không có raw fallback.

## 4. Export state machine và transaction seams

Owner chọn snapshot; export “kept hiện tại” phải chốt/resolve snapshot bằng command trước, cùng pipeline.
get_snapshot(actor,snapshot_id) trả metadata + ordered members; authz source/model/reference theo identity spec.
Freeze includes snapshot/context/members hashes, decision_seq, proposal_cutoff_n, ordered IDs và versions thực tế.
Freeze report decisions tới decision_seq và proposals/evidence tới proposal_cutoff_n đã ghi khi tạo snapshot; consistent DB read/transaction. Proposal mới sau snapshot dù không có decision mới vẫn bị loại.
Report chỉ dùng refs/definition versions/hashes trong snapshot.report_basis; evidence hoặc metric xuất bản muộn không thay basis. Missing value ghi unavailable theo basis, không infer/tính trên session hiện tại để điền.
Không gọi metrics của session hiện tại sau khi freeze; không infer thêm để làm report.
Thiếu lịch sử/version cần dựng report → block report_input_unavailable; không dùng số hiện tại thay thế.

| Transition | Điều kiện và atomic receipt |
|---|---|
| none → REQUESTED | Authorize + freeze + quota/storage reserve + insert export/job/pins/idempotency cùng transaction |
| REQUESTED → PROCESSING | Worker claim attempt/fence và slot theo runtime RPC |
| PROCESSING → NEEDS_CONFIRMATION | Full derivatives/report core validated; publish immutable payload receipt; build job SUCCEEDED |
| NEEDS_CONFIRMATION → FINALIZING | Confirm actor/hash/review receipt/expiry; reserve finalization + insert job/confirmation cùng transaction |
| FINALIZING → READY | Validate unchanged payload, report/manifest/members/ZIP; publish final artifact receipt + settle once |
| any work → FAILED/CANCELLED | Không READY/download; cleanup/usage theo runtime receipt, không refund đã dùng như chưa chạy |
| NEEDS_CONFIRMATION → EXPIRED | Không confirm cũ; policy cleanup derivative/temp được duyệt, raw/snapshot giữ nguyên |

Chờ owner không giữ CPU slot hoặc unused compute hold; storage reservation/artifacts vẫn accounted.
Finalizer là job mới, không giữ worker loop chờ click. Quota thiếu giữ NEEDS_CONFIRMATION và reason; không ghi confirm success giả.
Recheck active actor/resource rights và snapshot/source versions khi confirm/publish/download.
Disable account chặn cấp URL mới; URL cũ có bearer lifetime theo storage spec, không hứa revoke tức thì.
Hai tab confirm cùng hash/key chỉ tạo một confirmation/finalize job; khác hash hoặc receipt → conflict.
Finalization lỗi có thể retry cùng frozen bytes khi receipt/policy còn hợp lệ; không rerun PII ngầm.
Đổi model/policy/byte hoặc hết expiry tạo export/version mới và owner review lại.
Export READY bất biến; không overwrite report/ZIP/images của export cũ.

Migration đề xuất `008_pilot_export.sql` (003 storage/mẫu DEP4 #91; 004 object worker DEP4F #92; 005–007 DEP5 #94, #97), integration owner chốt SQL/RLS cùng runtime:

| Bảng | Columns/constraints draft |
|---|---|
| rav_pilot_export | id UUID PK,owner_id UUID FK profile,snapshot_id UUID,snapshot_decision_seq BIGINT,snapshot_proposal_cutoff_n INT,input_hash CHAR(64),state TEXT theo enum ExportDTO,state_version BIGINT>=1,idempotency_key TEXT,build_job_id UUID FK job,finalize_job_id UUID? FK job,payload_hash CHAR(64)?,review_receipt_id UUID?,review_expires_at TIMESTAMPTZ?,payload_ref UUID? FK artifact,output_receipt_ref UUID? FK artifact,error JSONB?,created_at/updated_at TIMESTAMPTZ; UNIQUE(id,owner_id), UNIQUE(owner_id,idempotency_key); composite snapshot-owner FK |
| rav_pilot_export_review_receipt | id UUID PK,export_id UUID,owner_id UUID,payload_hash CHAR(64),report_core_hash CHAR(64),manifest_hash CHAR(64),artifact_refs JSONB,issued_at/expires_at TIMESTAMPTZ; composite export-owner FK; immutable, no URLs; artifact_refs typed `{artifact_id,sha256,role}` list cho canonical payload/report/contact-sheet/member variants |
| rav_pilot_export_confirmation | id UUID PK,export_id UUID,owner_id UUID,payload_hash CHAR(64),review_receipt_id UUID FK review_receipt,actor_id UUID FK profile,confirmed_at TIMESTAMPTZ,idempotency_key TEXT; composite export-owner FK, actor_id=owner_id; UNIQUE(export_id,payload_hash), UNIQUE(owner_id,idempotency_key); immutable |

Export.review_receipt_id FK thêm sau review table để giải vòng FK. Payload/ref/hash/basis không được sửa sau NEEDS_CONFIRMATION; READY record và final receipt immutable.
payload_ref dùng artifact kind EXPORT_PREVIEW/variant canonical_payload, bytes SHA=payload_hash. output_receipt_ref trỏ EXPORT_BUNDLE; structured lineage/confirmation binding trong artifact.manifest, không UUID field đoán thành JSON.
RLS owner read qua safe projection; revoke user/anon direct WRITE, grants/RPC theo roles identity spec; worker only publish RPC. Tất cả FK ON DELETE RESTRICT, sources EXPORT pins bổ sung validator theo migration003.
Job/artifact/pin/hold schemas do runtime/identity spec sở hữu; không tạo job queue hoặc artifact authority thứ hai.
Chưa duyệt thời gian expiry/retention thì pilot/export readiness false; operator bootstrap có readiness riêng, không chọn default vô hạn.

## 5. Exact pixels, detector adapter và PII quality

Worker verify tất cả raw/provenance hashes trước tạo output; source thiếu/đổi → source_unavailable, không ZIP thiếu mẫu.
Video decode read_exact frame_idx theo decoder frozen, kể cả VFR; ảnh dùng EXIF/pixel convention frozen.
PII dùng full-size pixels; derivative giữ width/height, không thay raw/thumbnail/embedding input.
Source SHA/exact frame/spec + model SHA/runtime/preprocess + policy/params/output version là cache key.
Namespace cache/artifact private vẫn owner/resource-scoped bên ngoài core hash; không công bố cache private giữa A/B.
Model allowlist operator pin checkpoint/license/revision/task/class map; không deserialize user upload trên worker pilot.

Adapter đề xuất detect_pii(image,model_manifest,policy) → detections + processing evidence, không tự publish.
Detection: kind face|plate; box_px [x1,y1,x2,y2] integer half-open, clamped trong kích thước ảnh; confidence + model_id.
Tile mapping phải chuyển về full-image coordinates; ghi tile/full runs, model output và transform convention.
PII policy version chốt threshold, tile strategy, padding và blur/mask method sau D1; không tự lấy COCO nano làm face/plate.
Output evidence ghi model/runtime/SHA, params/policy version, detections, processing status và image SHA.
Model error/partial/missing derivative/invalid boxes → FAILED; không ghi no detections để che lỗi.
Zero detection là kết quả kỹ thuật, không chứng nhận ảnh không có PII; vẫn phải quality gate + owner review.
No detection không cho download raw shortcut; derivative receipt phải qua đầy đủ pipeline/gate.

Detector adapter và benchmark harness có thể giao độc lập sau input/output conventions/model fixture được khóa.
Fake detector chỉ kiểm coordinate mapping, cache, failure, publish/confirmation; không báo recall hoặc safety từ fake.
D1 quality dataset cần ground truth face/plate, ngày/đêm, nhỏ/xa/occluded, 720p/1080p và full+tiles.
Protocol freeze split/labels/metric/failure categories trước đo; lưu model/policy/seed/input hashes và actual outputs.
Numeric acceptance threshold và dữ liệu benchmark còn chờ duyệt/availability; thiếu thì block release quality, không bịa pass.
Owner confirmation là thêm human review, không thay detector benchmark hoặc cam kết xử lý mọi PII/OCR/NER.
Chỉnh ROI thủ công chưa thuộc pilot; không thêm editor hoặc âm thầm sửa output sau confirmation.

## 6. Payload hash, report và bundle

Payload v1 chứa: format_version, export_id, snapshot_id, snapshot context/members hashes, decision_seq, proposal_cutoff_n, ordered members,
raw-to-output mapping, model/policy/config versions, PII evidence, report_core và hashes của mọi ảnh được phép nhúng.
Không chứa confirmation actor/time, ZIP SHA, expiring URL, token, path host hoặc dynamic current-session fields.
~~~json
{"format_version":1,"export_id":"<uuid>","snapshot_id":"<uuid>","decision_seq":12,"proposal_cutoff_n":3,
 "context_hash":"<hex>","members_hash":"<hex>","model_manifest_hash":"<hex>","pii_policy_hash":"<hex>",
 "members":[{"ordinal":0,"core_unit_id":"<unchanged-id>","source_id":"<uuid>","raw_sha256":"<hex>",
             "frame_idx":8,"output_relpath":"images/000000.png","output_sha256":"<hex>","width":1280,"height":720}],
 "pii_evidence":[{"ordinal":0,"status":"processed","detections":[],"model_id":"<pinned-id>","policy_version":"<version>"}],
 "report_core":{"goal":"<frozen>","requested":16,"returned":1,"decision_seq":12,
                "metrics":[{"name":"<metric>","value":null,"status":"unavailable","definition":"<version>"}]}}
~~~
Ví dụ chỉ minh họa field bắt buộc; integration owner khóa schema đầy đủ, không dùng placeholder/hash/metric này làm dữ liệu thật.
Mỗi ordered member có ordinal,core_unit_id,source_id,raw_sha256,frame_idx/extraction,output_relpath,output_sha256,width,height.
Output relpath hệ thống sinh, slash '/', không absolute/..; duplicate member/path hoặc thiếu ảnh → validation failure.
PII/reference/contact-sheet images cùng rule derivative+hash; report metadata-only nếu không có approved derivative.

Canonical payload v1 là JSON UTF-8 không BOM/no trailing newline; object keys sort, compact separators; strings giữ bytes đã freeze.
Chỉ cho null/bool/string/integer/list/object. **Mọi** finite float ở bất kỳ độ sâu (recipe threshold/config, confidence, metric, evidence) được encoder export chuyển thành tagged decimal round-trip `{kind:"float64",value:"<roundtrip-decimal>",precision:"binary64_roundtrip",unit:null|str}` khi freeze.
Encoder này chỉ dùng cho payload/report export, giữ hash + typed config core gốc riêng; không đổi RecipeConfig/FieldKey/scoring serializer. Golden fixture phải có nested thresholds/confidence, không chỉ metric float.
Reject duplicate/non-string object keys, unknown fields, invalid Unicode/lone surrogate và non-finite values trước canonicalize.
Arrays giữ semantic order, members theo snapshot order; không sort lại để che ordered-ID mismatch.
Canonical serializer export duy nhất: json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).
payload_hash = lowercase SHA256 của canonical bytes; lưu chính bytes/hash, không reconstruct từ row mutable khi confirm.
Frontend gửi hash từ review receipt; server recompute từ bytes + verify artifact SHA, không tin hash browser tự khai.
Determinism kiểm bằng golden payload fixture; serialize cùng freeze hai lần cho cùng bytes/hash.

report_core bắt buộc goal/config/reference/model provenance; requested/returned/kept/dropped; metric definition/source/value or unavailable;
proposal/evidence boundaries; algorithm/runtime versions; source availability; PII scope/limits/mapping; snapshot/decision_seq.
Metrics phải snapshot-scoped/replay tới decision_seq; prediction không là annotation ground truth; thiếu ghi “chưa có”.
Không claim mAP/rare-event/accuracy uplift từ confidence, deployed status hoặc mock tests.
Render HTML deterministic offline, escape mọi user text, style inline; không JS, remote font/tracker/API hoặc raw image URL.
JSON chứa cùng report_core, frozen lineage và final confirmation fields; không LLM call/service mới.
Owner xem contact sheet của **toàn bộ export members** rồi mở full viewer kiểm vùng che và report trước confirm.
Contact sheet/full viewer dùng derivative đã hash; report preview cùng frozen core, chỉ confirmation đang pending.
UI cảnh báo phạm vi face/plate và yêu cầu owner kiểm ảnh; không có nút bypass review/download.
review_receipt binds actor/export/payload/member hashes/report core/expiry; record issued_at và confirm exact receipt.
Không coi telemetry “đã mở viewer” là bằng chứng owner đã phát hiện mọi lỗi; server chỉ enforce receipt/acknowledgement.

Final bundle: images/, manifest.json, manifest.csv, report.json, report.html, notices nếu license yêu cầu.
Finalizer thêm confirmation receipt actor/time/hash vào report/manifest; kiểm report_core/payload unchanged.
Manifest receipt liệt kê member/output/report hashes, format/version và snapshot lineage; không checksum tự tham chiếu.
ZIP SHA/size lưu **ngoài ZIP** trong immutable artifact receipt; report không ghi ZIP SHA của chính ZIP đang xây.
ZIP write streaming vào attempt temp; enforce actual byte cap từ approved policy mỗi write; quá cap fail toàn bộ.
Worker re-open archive verify paths/count/order/SHA/no raw/base64/remote links trước publish canonical private object.
Artifact receipt: export/job/attempt/fence, owner/snapshot/payload hash, object ref internal, SHA256,size,format,created_at.
Runtime sở hữu rav_pilot_artifact; export.output_receipt_ref là UUID FK artifact, còn artifact.manifest lưu artifact_id/snapshot_id/payload_hash/confirmation_id/format_version. Không thêm output_receipt JSON field/authority thứ hai.
Artifact READY chỉ nói object đã validate; EXPORT_PREVIEW/REPORT chỉ review authorized, EXPORT_BUNDLE còn cần Export READY.
Download server authz + READY + immutable receipt; URL/SHA/size/filename do server trả, không proxy ZIP vào FE RAM.
Object thiếu/hash/size khác receipt → artifact_unavailable, revoke issuance/block, không reconstruct từ current session.
No raw export path, raw clip, host path, secrets hoặc private source links trong bundle/report.

## 7. Env, build và entrypoint đề xuất

API/worker đọc environment đã validate theo từng host/role. Operator bootstrap được đọc credential `.env` repo khi chỉ định explicit; CLI draft thêm `--env-file <path>` cho các lệnh operator dưới đây, resolve từ working directory, không auto tìm file hoặc credential chain/account khác.
Tooling map tên biến baseline sang tên config đề xuất, kiểm tên/quyền thiếu và fail rõ; không coi mọi key trong bảng đã có trong `.env`, không dùng Supabase admin secret thay direct-PG LOGIN roles. Local operator examples chạy từ repo root với `--env-file .env`; Linux hosted runtime dùng injected restricted env.
Public FE chỉ chứa API/provider publishable config; mọi secret trong server platform/worker restricted env.

| Env đề xuất | API | Worker/operator | Public FE |
|---|---|---|---|
| RAV_DEPLOY_MODE=pilot, RAV_PILOT_STAGE, RAV_DEPLOYMENT_ID, RAV_PILOT_SCHEMA_VERSION | Stage OPERATOR_BOOTSTRAP/CLOSED_PILOT | Required, cùng stage/deployment ID | Không |
| RAV_API_ORIGIN, RAV_WEB_ORIGIN_ALLOWLIST | Required | API health target | VITE_API_URL |
| SUPABASE_URL, RAV_AUTH_ISSUER, RAV_AUTH_AUDIENCE | Required | Provider metadata cần thiết | VITE_SUPABASE_URL |
| Supabase publishable key | Public config nếu SDK cần | Không admin substitute | VITE_SUPABASE_PUBLISHABLE_KEY |
| RAV_DATABASE_URL / RAV_WORKER_DATABASE_URL | Direct TLS PG LOGIN rav_pilot_api; per-request verified-claims transactions | Direct TLS PG LOGIN rav_pilot_worker, EXECUTE allowlist | Không |
| RAV_OPERATOR_DATABASE_URL | Không | Migration/backup role; operator-only | Không |
| Migration/admin auth credential | Không API bootstrap | Operator tooling riêng | Không |
| RAV_S3_BUCKET, RAV_S3_REGION, RAV_PILOT_S3_PREFIX | Required signing config; prefix explicit | Required artifact/raw access cùng namespace | Không |
| Explicit AWS signing/access credentials | Least privilege | Task-scoped; delete riêng | Không |
| KAGGLE_API_TOKEN, RAV_KAGGLE_KERNEL | Không | Required GPU worker only | Không |
| RAV_WORKER_ID, RAV_CACHE_DIR, RAV_TMP_DIR | Không heavy dirs | Linux volume/ownership/cap | Không |
| RAV_MODEL_MANIFEST_PATH, RAV_PII_POLICY_PATH | Descriptor only | Pinned/offline manifests | Không |
| RAV_PILOT_POLICY_ID | UUID FK quota_policy.id, required khi mở pilot | Resolve immutable policy.version TEXT cho jobs/artifacts | Rendered server limits only |
| Backup target/encryption/retention config | Không | Operator only; approved | Không |

TTL/caps/credit/deadline/confirmation expiry nằm trong versioned policy theo runtime spec; unresolved policy → readiness false.
Không log env dump, URL query hoặc secrets; .env/data/output/cache/private DB/Git exclude Docker/build context.
Đề xuất artifacts: requirements-api.txt, requirements-worker.txt, Dockerfile.api, Dockerfile.worker, render.yaml và worker supervisor units.
Shared dependency/build files chỉ integration owner sửa; API import graph không torch/ultralytics/ML/decode/matrix load.
API deploy entrypoint đề xuất python -m uvicorn src.rav.api.pilot:app; worker python -m src.rav.worker --mode pilot.
Local src.rav.api:app giữ nguyên; import api/service/commands package phải lazy như runtime spec để pilot không khởi tạo App/dispatcher local cũ.
Worker entrypoint là phần chưa implement; không chạy API cũ như worker hoặc khởi động hai dispatcher.
Static build dùng pinned lockfile + npm ci + npm run build; chỉ deploy dist, không private artifacts.
Pin Python/runtime/PyAV/FFmpeg parity sau D1; image copy đủ included requirement files, health path thực tồn tại.
Public health process-only; internal readiness validates env/schema/DB/S3/model manifests bounded, không inference.
Render migration không chạy app boot/pre-deploy command trả phí; operator chạy một lần có lock/backup trên Contabo tooling.

## 8. Runbook bắt buộc trước mở pilot

Hai giai đoạn: OPERATOR_BOOTSTRAP để deploy hạ tầng/đo, rồi CLOSED_PILOT khi content/quality/capacity/cost gates đạt.
OPERATOR_BOOTSTRAP chỉ process/infra probe và setup để operator test; schema/policy/content thiếu trả blocked codes, không dispatcher claim hoặc mở business endpoints. Worker mode operator-bootstrap theo runtime spec; user/export/guided admission vẫn khóa.
Không cần giả BDD/nano để bật health; model/content readiness trả missing_assets rõ ràng.
CLOSED_PILOT phải có demo session+history được migration, BDD/model inventory phù hợp và gates tương ứng có evidence.
Bootstrap pass không tick BDD/guided/PII/full pilot; actual D1 chạy sau authorization, không tự chạy khi boot.

Command proposals dưới đây **chưa tồn tại**, integration owner triển khai/duyệt trước khi operator chạy.
Mutating commands mặc định dry-run; write bắt buộc --apply, --operator-confirm <plan_hash> và --config explicit, không credential fallback. Seed/import plan_hash là inventory_hash như identity spec; migration/restore plan_hash bind target/revision/backup/input plan đã dry-run, không flag boolean chung chung.
Preflight/readiness read-only; acceptance mặc định fixture/local, actual cloud cần --allow-live và quyền riêng đã cấp.
Config file ngoài Git chứa release/account/project/bucket/namespace/roles/allowlist của tài nguyên hiện có đã verify. `--env-file .env` chỉ truyền đường dẫn credential input, không password/key trong argv; receipt/log không ghi nội dung file. Config thiếu role/prefix phải báo và hoàn thiện trong tài nguyên hiện có, không tự tạo project/bucket/account khác.
Preflight fail nếu thiếu account mapping, namespace scope hoặc schema compatibility; không tự tạo/chuyển provider.

~~~powershell
. .\scripts\use_project_storage.ps1
python -m src.rav.cli pilot-preflight --config <operator-config> --env-file .env --mode operator-bootstrap
python -m src.rav.cli pilot-backup --config <operator-config> --env-file .env --release <approved-sha> --dry-run
python -m src.rav.cli pilot-migrate --config <operator-config> --env-file .env --backup-receipt <id> --dry-run
python scripts/pilot_seed_catalog.py --config <operator-config> --env-file .env --manifest <approved-source-manifest> --dry-run
python scripts/pilot_import_demo.py --config <operator-config> --env-file .env --inventory <approved-demo-inventory> --dry-run
python -m src.rav.cli pilot-readiness --config <operator-config> --env-file .env --mode operator-bootstrap
python -m src.rav.cli pilot-acceptance --config <operator-config> --env-file .env --fixture-manifest <manifest> --mode closed-pilot
python -m src.rav.cli pilot-restore-drill --config <isolated-target-config> --env-file .env --backup-receipt <id> --dry-run
~~~

Linux Contabo dùng cùng CLI/scripts sau inject restricted env; không dùng PowerShell storage script của máy dev.
Apply migration/seed/restore phải qua operator, lock, backup receipt + namespace guard và không overwrite live target.
Expected preflight output: safe release/schema/mode/roles/namespace/gates JSON, không secrets/connection strings.
Backup output: immutable receipt + SHA + schema/auth coverage; readiness output: each gate pass/blocked/reason.
Acceptance output: test IDs + evidence paths + actual/mock classification; failed gate exit nonzero, không mở admission.
Apply/build/restart CLI/units chưa define ở D0: D7 bổ sung exact installed commands + rollback receipt, không đoán service name.
Không provision cloud/model/GPU hoặc migrate chỉ vì đọc runbook.

1. Freeze release SHA + approved schema/policy/model manifests; verify actual Render/Contabo/S3/Supabase/Kaggle account/package/region.
2. Kiểm bill/allowance/quota freshness và forecast seed+raw+exports+backups; unknown → admission pause, không tự nâng plan.
3. Cấu hình private S3 prefix/IAM/CORS, DB roles/RLS, auth signup-off **trong tài nguyên hiện có** theo target đã preflight; kiểm credential scope/role thiếu, inject env theo host, không copy .env local lên image.
4. Pause admission; drain/reconcile old GPU/jobs; backup SQLite/Postgres/identity metadata theo migration plan.
5. Operator migration lock → apply version → validate counts/FKs/owner/core hashes; repeated run no-op; giữ local gốc.
6. Seed demo phiên+lịch sử allowlist cho bootstrap; BDD/model tìm/provision sau, verify hashes/license/train relation trước full gate.
7. Deploy worker supervisor với caps/offline models; verify readiness/one dispatcher/fencing; không public AI port.
8. Deploy API light + static FE, inject public config; verify /api/v1 không mount và legacy raw download bị chặn.
9. Operator tạo A/B/C, cấp password kênh riêng, kiểm signup direct bị chặn; không đặt password vào test artifact/log.
10. Bootstrap readiness cho operator trước; chạy acceptance + actual D1 capacity/PII/cost/content khi assets có, thiếu evidence không mở pilot.
11. Timer Contabo ping health best effort; monitor readiness/GPU cleanup/quota/disk/cost; không SLA và không DB keepalive giả.
12. Mở 2–3 accounts sau review evidence; ghi operator/release/policy/time và known limits, không public signup.

Backup: daily + trước migration là đề xuất; lưu encrypted operator-only và manifest SHA/schema/auth identity coverage.
Retention 7 daily/2 pre-migration, RPO ≤24h/RTO ≤4h trong pilot spec vẫn **đề xuất cần duyệt/drill**, không default đã chốt.
Restore drill: pause admission → stop/reconcile owned work → restore vào isolated target → identities/UUID/FK/ACL/hash checks.
Restore auth config/signing keys riêng; issuer/project đổi bắt login lại; business-only dump không là full identity backup.
Rollback: pause → reconcile owned sessions/process → backup → deploy compatible app/schema; không destructive down-migration.
Incompatible schema: forward fix hoặc restore theo user-approved RPO; ghi dữ liệu mất, không rollback SQLite bỏ quyền.
DB/S3 outage: safe 503/read-only/pause; model/PII/source lỗi: block publish; GPU launch uncertain: giữ slot, operator reconcile.
Key leak: rotate signing/admin secrets, disable compromised access, audit issuance; URL đã cấp theo TTL còn giới hạn.
Source delete còn session/snapshot/job: RESTRICT; archive không release pin, cleanup không xóa raw/system vì quota.

## 9. Acceptance fixtures và ownership giao agent

Fixtures đề xuất tests/fixtures/pilot/: actors A/B/disabled; source system/private; exact VFR; frozen snapshot + decisions;
payload-v1 golden; escaped goal HTML; zero-detection/error/partial PII; ready/corrupt ZIP receipt; expired review/URL.
Fixture fake/malformed/generated phải ghi rõ synthetic; quality ground truth inventory tách riêng, không claim từ synthetic.

| Gate | Test/assertion cụ thể | Evidence |
|---|---|---|
| FE-AUTH | A response tới sau logout/login B bị discard; expired/disabled clears media/state; no signup | Component/client tests + A/B smoke |
| FE-JOB | Reload/poll không POST launch; WAITING/CANCEL_REQUESTED khác RUNNING/CANCELLED; quota server reset | Fake HTTP fixtures + request log |
| FE-TEMPLATE | Mẫu shared/history chỉ đọc; tạo phiên riêng cùng nguồn/config, kept/drop/history/results rỗng; navigate/reload không auto propose; state A/B tách biệt, idempotency replay cùng session | Template/session HTTP fixtures + DB row counts + request log |
| EXP-FREEZE | Keep/drop, proposal mới hoặc metric/evidence late sau snapshot không đổi report basis/member/payload bytes; source changed fails; nested config floats encode deterministic | DB fixture + golden canonical SHA |
| EXP-CONFIRM | Wrong/expired receipt, other owner, concurrent confirm; only one finalizer; changed bytes review lại | Transaction/fault tests |
| EXP-PII | Exact pixels/bbox/tile mapping; error/partial không READY; no-detection không raw shortcut | Fake detector mechanism + separate real quality report |
| EXP-BUNDLE | HTML injection escaped; every image derivative hash; no raw/base64/remote link; ZIP count/SHA/cap | Archive/content assertions |
| EXP-DOWNLOAD | All legacy/new route paths owner/READY-only; missing/corrupt object no fallback | API routes + S3 fault fixtures |
| OPS | Light API build/import, no secret bundle, explicit .env operator input/injected host env, missing config/role báo tên không giá trị; guard existing target/prefix; restart/recovery, auth/FK/ACL restore, compatible rollback | Build/config logs + isolated restore/cutover drill |

Security/concurrency/parser changes cần meaningful tests; pytest/ruff và tsc/build khi đụng FE, theo AGENTS.
PowerShell trước lệnh Python/npm/test phải source scripts/use_project_storage.ps1; pytest basetemp output/pytest-tmp.
Không chạy model/cloud chỉ để unit test; live smoke báo riêng, D1 benchmark không suy từ mock pass.

Integration owner giữ shared DTO/routes/schema/migrations/build/bootstrap và chốt SoT trước implementation.
Một FE integration owner giữ api.ts/App/navigation; screen agents chỉ sửa hook/page/component/test được giao, không tự sửa common route/DTO definition.
Export orchestrator agent: đề xuất api/pilot_exports.py, service/pilot_export.py, pipeline/pii_export.py + orchestration tests; không sửa detector hoặc report renderer của agent khác.
Report agent độc lập: pipeline/pilot_report.py + renderer tests, chỉ nhận frozen DTO/artifact receipts.
Detector agent độc lập: pipeline/pii.py/model manifest validation/benchmark harness + riêng tests; không claim job/quota/publish.
Ops agent: runbook/config/build drafts và drill scripts; shared build/env changes qua integration owner.
Các paths module mới là ownership đề xuất, không lý do tạo abstraction ngoài CONTRACTS; integrator chốt trước code.
Gate tích hợp: identity+runtime fixtures → export mechanism → FE end-to-end → actual D1 quality/capacity/cost → D7 restore/build → D8.
