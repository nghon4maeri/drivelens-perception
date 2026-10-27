# Từ MVP RAV-26 đến curation platform — plan nháp

> **LƯU TRỮ 2026-10-09.** Bản gốc: `docs/backlog/CURATION_PLATFORM_ROADMAP.md`. Chỉ tra lịch sử/tham khảo; đọc [index hiện hành](../../README.md), không dùng các lệnh/hiện trạng cũ như contract.


Ngày rà: **2026-10-09**. Baseline: nhánh `duy-mvp/v2`, mốc `4d6f6a1`.

**Trạng thái: đề xuất để thảo luận, chưa duyệt thiết kế, chưa triển khai.** Tài liệu này không thay thế CONTRACTS, architecture hoặc các quyết định đã chốt. Khi chọn một lát triển khai, phải cập nhật tài liệu cấp 2 trước code theo [quy tắc nguồn sự thật](../../README.md). Việc đăng nhập đã được để sau ở quyết định #77.

Đích đầu tiên là platform cho các nhóm được mời: quản lý dữ liệu, tính feature theo yêu cầu, tuyển chọn theo mục tiêu, phối hợp duyệt và phát hành tập dữ liệu có truy vết. SaaS mở đăng ký công khai là mốc sau. Bảo mật cần có tiêu chí kiểm chứng, giám sát và xử lý sự cố; không thể cam kết không bao giờ bị tấn công.

## 1. Có gì rồi, còn thiếu gì?

| Mảng | MVP hiện tại | Cần bổ sung để thành platform |
|---|---|---|
| Curation | Recipe/component, phiên, proposal từng batch, keep/drop, evidence, snapshot bất biến, export | Collection, nhiệm vụ, phân công, review/approval, dataset release/version |
| Video và feature | Upload lưu raw S3; DINO+pHash và CLIP là hai job explicit, trạng thái riêng | Quyền trên từng asset, upload tiếp tục được, quota, validation cách ly, lifecycle |
| GPU | Kaggle tải S3, tính feature, đẩy result/progress S3, dọn file tải về | Hàng đợi dùng chung, giới hạn theo workspace, cancel, admission control và worker độc lập API |
| Tiến độ | BE đọc S3 rồi lưu job Supabase; lease/recovery đã có | Quyền xem/hủy job, lịch sử attempt, cảnh báo và đối soát chi phí |
| Model tham chiếu | Upload `.pt`, SHA, liên kết dataset train khai báo; YOLO chạy trên picks của batch | Model registry, phiên bản, nguồn gốc, quy trình tin cậy và runtime cách ly |
| Database | Catalog/session/feedback/snapshot/model vẫn SQLite; Supabase chỉ lưu embedding job | Postgres cho nghiệp vụ, workspace/project ownership, transaction và migration |
| Truy cập | API/command/media chưa có người dùng và kiểm quyền | Identity, session đăng nhập, membership, authorization trên mọi tài nguyên |
| Triển khai | Chạy local, một số queue/lock/cache nằm trong process | Staging/prod, API và worker tách nhau, quan sát, backup/restore, giới hạn tải |

Bằng chứng local: [API routes](../../../src/rav/api/routes.py), [media](../../../src/rav/api/media.py), [database](../../../src/rav/store/db.py), [job SQL](../../setup/SUPABASE_EMBEDDING_JOBS.sql), [YOLO runtime](../../../src/rav/pipeline/batch_yolo.py). Đặc biệt, frame response đang có cache `public, immutable`; dữ liệu riêng tư cần chính sách cache mới. Media hiện có comment `local-only` về việc không kiểm soát truy cập.

Giữ lớp command/service/pipeline và cơ chế mở rộng thuật toán. Phần phải mở rộng mạnh là identity, ownership, persistence, storage access và execution. Đăng nhập đơn lẻ không giải quyết việc một người đọc dữ liệu của người khác.

Tham chiếu giới hạn: [LOCAL_SINGLE_USER_LIMITS](../../design/LOCAL_SINGLE_USER_LIMITS.md), [CONTRACTS §19–20](../../design/CONTRACTS.md), [architecture](../../architecture_diagram.md). Chất lượng tìm kiếm V2 cũng còn gate riêng: [đánh giá ngữ nghĩa chưa đạt](../2026-10-13/docs/evaluation/V2_CRITERIA.md). Chạy đúng pipeline chưa chứng minh chọn đúng ảnh hay cải thiện detector.

## 2. Cấu trúc sản phẩm đề xuất

```text
User ── membership ── Workspace
                         └─ Project ── project membership
                              ├─ Assets: video, ảnh, dataset/version, model/version
                              ├─ Collections và mục tiêu tuyển chọn
                              ├─ Tasks / sessions / proposals / feedback
                              ├─ Snapshots → review → dataset releases → exports
                              └─ Jobs / attempts / artifacts / audit events
```

- **Workspace** là ranh giới dữ liệu của một tổ chức/nhóm; một người có thể thuộc nhiều workspace.
- **Project** là ranh giới cấp quyền làm việc bên trong workspace. Thành viên workspace thường không tự được đọc mọi project.
- **Asset** có chủ sở hữu theo project, nguồn gốc, checksum, version và trạng thái kiểm tra. Dataset tham chiếu là dữ liệu train/fine-tune của YOLO tham chiếu do người dùng khai báo; không tự suy ra quan hệ từ tên hoặc số lớp.
- **Collection** gom dữ liệu và mục tiêu cần tuyển chọn; **task** phân công một phần việc; **session** giữ hành vi vòng đề xuất hiện có.
- **Snapshot** đóng băng lựa chọn và provenance; **release** là snapshot đã được duyệt kèm version, nguồn, schema nhãn và giới hạn sử dụng. Không sửa release tại chỗ.

V1 dùng project private và mời người có danh tính. Chia sẻ liên workspace, link công khai, SSO doanh nghiệp, billing tự động, annotation editor và training platform để sau. Tích hợp công cụ gán nhãn bên ngoài bằng export/import version, giữ curation là trọng tâm.

## 3. Đăng nhập và ranh giới FE/BE

**Đề xuất dùng Supabase Auth cho identity; FastAPI làm BFF, tức BE đứng giữa trình duyệt và các dịch vụ.** Supabase không thay thế logic phân quyền nghiệp vụ.

Luồng dự kiến:

1. FE gọi BE để đăng nhập, đăng xuất, nhận danh tính hiện tại hoặc bắt đầu OAuth. Ban đầu dùng lời mời + email đã xác minh; có thể chọn email/password hoặc Google.
2. BE xử lý callback/PKCE, kiểm tra state và đích redirect; dùng nonce nếu luồng OIDC yêu cầu. Không tự xây cơ chế lưu mật khẩu.
3. BE giữ access/refresh token Supabase trong kho session phía server được mã hóa và kiểm soát truy cập; browser chỉ nhận cookie session ngẫu nhiên của ứng dụng với `HttpOnly`, `Secure`, `SameSite`, phạm vi host chặt. Rotate session sau login; có timeout, logout và thu hồi mọi thiết bị.
4. FE gọi API cùng origin qua reverse proxy, dùng cookie + CSRF token cho thao tác thay đổi. FE không dùng SDK để đọc database nghiệp vụ và không giữ Supabase refresh token trong localStorage. Refresh và xử lý refresh đồng thời là trách nhiệm BE.
5. Mọi request phải kiểm app session còn hiệu lực, user/membership hiện tại và quyền trên tài nguyên. Owner/admin dùng MFA; đổi quyền, chuyển ownership và thao tác nhạy cảm cần xác thực lại theo policy.

Đây là **BFF session do ứng dụng quản lý**, không giả định SDK Supabase trên browser tự hoạt động với token HttpOnly. Supabase phân biệt access token và refresh token; JWT còn hạn không tự chứng minh session chưa bị thu hồi. Nếu xác thực JWT, BE phải kiểm chữ ký, algorithm allowlist, issuer/audience/thời hạn và chỉ lấy JWKS từ cấu hình tin cậy; thao tác nhạy cảm cần kiểm trạng thái session. [Supabase sessions](https://supabase.com/docs/guides/auth/sessions), [Supabase JWTs](https://supabase.com/docs/guides/auth/jwts).

AWS/Kaggle/Supabase admin secret chỉ ở BE quản trị/orchestrator, không nằm trong FE, cookie hay notebook. Key `sb_secret_...` có thể vượt RLS và không phải JWT để dùng làm khóa xác minh người dùng. [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys).

## 4. Phân quyền đề xuất

Hai phạm vi vai trò, tránh một role toàn hệ thống vô tình mở mọi project:

| Vai trò workspace | Quyền |
|---|---|
| Owner | Ownership, chính sách dữ liệu/ngân sách, bổ nhiệm admin; không được xóa owner cuối cùng |
| Admin | Mời/thu hồi thành viên, tạo project, quota, audit; không chuyển ownership |
| Member | Chỉ dùng project được cấp quyền; không xem project khác |

Owner/Admin được quản trị và truy cập các project trong workspace theo đề xuất V1; quyền rộng này phải hiển thị rõ khi mời thành viên. Cần chốt riêng nếu muốn project mà admin tổ chức cũng không được đọc.

| Hành động trong project | Manager | Curator | Reviewer | Viewer |
|---|---|---|---|---|
| Xem metadata, thumbnail/preview được cho phép | Có | Có | Có | Có |
| Upload video/dataset, khai báo model chờ duyệt | Có | Có | Không | Không |
| Tạo session, chọn recipe, chạy job trong quota | Có | Có | Không | Không |
| Thực hiện keep/drop và tạo snapshot nháp | Có | Có | Không | Không |
| Nhận xét, chấp thuận/từ chối snapshot | Có | Không | Có | Không |
| Phát hành release đã đủ approval | Có | Không | Không | Không |
| Quản lý thành viên project, archive asset | Có | Không | Không | Không |
| Tải raw/export dataset | Có | Cấp riêng | Cấp riêng | Không mặc định |
| Duyệt độ tin cậy checkpoint để cho chạy | Không mặc định | Không | Không | Không |

`download_original`/`export.create` là capability cấp riêng, có audit; preview bản thân cũng là quyền đọc dữ liệu, không được coi là vô hại. Duyệt model để chạy là quyền quản trị bảo mật riêng. Worker/service account chỉ được thực hiện job đã nhận, không có role người dùng để tự duyệt hoặc chia sẻ dữ liệu.

BE áp dụng **role + quan hệ membership + phạm vi resource + trạng thái workflow**. Với mỗi hành động: xác thực actor → lấy resource trong phạm vi → kiểm membership/capability → kiểm trạng thái → thực hiện. Danh tính actor do BE dựng, không nhận `user_id/role` trong body làm căn cứ quyền. Mọi ID tham chiếu kèm theo đều phải được kiểm tra, kể cả model, dataset train, snapshot và export. UUID/hash không phải quyền truy cập. Đây là ứng dụng đề xuất nguyên tắc least privilege, deny by default và kiểm quyền mỗi request của [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).

Không chỉ chặn ở route: command/service là ranh giới chung cho HTTP, CLI, worker và agent tương lai. API trả capability để FE hiển thị nút; BE vẫn quyết định quyền khi thực thi. Không giữ đường CLI/service bypass để user dùng trên deployment.

## 5. Các luồng quản lý cần xây

### 5.1 Thành viên và project

Owner tạo workspace → Admin tạo project → gửi invite có hạn, gắn email/role/project → người nhận xác minh danh tính → nhận membership. Invite nhận một lần, không cho tự đổi role. Thu hồi membership phải ngăn request mới và kiểm lại job đang chờ; cập nhật quyền có transaction và audit. Job đang chạy cần policy hủy hoặc hoàn tất dưới quyền project được chỉ định, không để người bị thu hồi đọc kết quả.

Có màn quản lý thành viên, invite chờ, project, quota, audit, archive/restore. Platform operator không tự đọc raw của khách hàng; truy cập hỗ trợ cần cơ chế cấp quyền có hạn và audit.

### 5.2 Upload và asset catalog

BE kiểm quyền/quota → tạo upload session thuộc project → upload multipart có thể tiếp tục → finalize → worker validation cách ly → asset ready/error. Video ready **chưa có DINO/CLIP**; validation không tự xếp embedding.

Đề xuất truyền bytes trực tiếp browser → S3 bằng URL từng part do BE cấp sau kiểm quyền. Browser không có credential AWS hay quyền list bucket. Nếu yêu cầu mọi byte phải qua BE, dùng proxy streaming, đổi lại phải chịu băng thông/timeout ở BE; cần chốt khi thiết kế lát upload.

Key do BE sinh theo workspace/project/upload ID, không nhận key/path tùy ý từ client. Upload vào staging, kiểm kích thước/checksum và nội dung trong sandbox, rồi publish sang key final riêng không có URL ghi đã cấp cho client. Không dùng ETag multipart như SHA-256 toàn file và không tin SHA client là bằng chứng validation đã chạy. Chặn archive path traversal/symlink, số file quá lớn và decompression bomb; giới hạn duration/resolution/frame count/thời gian decode ngoài dung lượng bytes. Dọn upload bỏ dở và multipart chưa hoàn thành.

S3 presigned URL có thể dùng lại tới hết hạn và PUT có thể ghi đè key hiện có; URL không phải token dùng một lần. TTL download ngắn, không log URL; URL đã cấp có thể còn hiệu lực sau thu hồi membership. Nếu cần thu hồi truy cập ngay, media phải qua BE kiểm session/quyền hoặc cơ chế phân phối có kiểm quyền tương đương. [AWS presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).

### 5.3 DINO/CLIP, YOLO và job

Giữ nguyên trải nghiệm đã chốt: video chỉ lưu; người có quyền bấm DINO hoặc CLIP khi cần. Readiness tách theo feature/spec/model version. YOLO trên batch chỉ chạy picks của proposal đã chọn; không tự chạy toàn dataset.

Mở rộng job: `queued → running → validating_result → succeeded`, thêm `failed/cancel_requested/cancelled`; attempt lưu input hash, runtime/model version, stage/progress/heartbeat, lease, người yêu cầu và chi phí ước tính. Tên state cụ thể cần vào contract sau khi duyệt.

Queue và worker dùng Postgres durable trước; chưa cần thêm broker riêng nếu tải chưa đòi hỏi. Có quota job đồng thời theo workspace, giới hạn frame/GPU time, idempotency trong tenant, deadline, retry explicit và cancel. Quota phải reserve bằng transaction để hai request đồng thời không vượt ngân sách; checkpoint/resume từng batch là mở rộng riêng, không giả định MVP đã có.

Kaggle tiếp tục là executor thử nghiệm. API không giữ luồng điều phối GPU trong process HTTP khi deploy; orchestrator chạy như worker riêng. Khi cần throughput hoặc SLA, thay executor bằng GPU worker quản lý được mà giữ input/result/progress contract; không đưa thuật toán riêng vào từng executor.

**Không cần W&B để làm nguồn sự thật tiến độ.** Giữ Kaggle → S3 progress/result → orchestrator → Supabase/Postgres; FE xem qua BE. Chỉ cần HTTPS ra ngoài, không phải mở cổng vào máy local. Nếu cần progress cập nhật khi orchestrator offline, cân nhắc Edge Function nhận capability riêng của job, kiểm job/attempt/sequence/expiry và chỉ cập nhật trường cho phép. Không cấp Supabase admin secret cho Kaggle. W&B có thể dùng sau cho thí nghiệm, không thay job DB.

Worker nhận quyền trên đúng input và artifact của attempt. Result phải qua validation IDs/shape/hash/runtime/ownership trước publication; attempt cũ hoặc job bị hủy không được ghi đè result mới. Dọn dữ liệu tải về trên worker và stop đúng session thuộc job; raw S3 và embedding đã publish giữ theo policy, không xóa cùng temp.

### 5.4 Cộng tác tuyển chọn, review và release

Project → chọn assets + dataset train/model tham chiếu → collection/goal → session → proposal batch → review keep/drop → snapshot → reviewer approval → manager release → export có quyền.

Ban đầu phân công task để giảm việc nhiều người sửa cùng phần dữ liệu; vẫn phải có optimistic version/transaction, trả conflict thay vì mất cập nhật. Mỗi feedback ghi actor, thời điểm, proposal và lý do; snapshot giữ recipe/config, FieldKey, ordered unit IDs, source/model/dataset versions và lineage. Approval gắn đúng snapshot hash; sửa lựa chọn tạo snapshot mới và yêu cầu review lại. Đề xuất không tự duyệt release do chính mình tạo khi bật chế độ kiểm duyệt hai người.

Có màn task/assignee, review queue, comment, history và release comparison. Export/import nhãn từ CVAT/Label Studio tạo dataset version mới, kiểm schema lớp và ánh xạ unit/source; không sửa dataset train tham chiếu đã đóng băng. Archive/soft delete trước, xóa vật lý sau retention và kiểm mọi snapshot/release còn tham chiếu.

### 5.5 Model registry

Lưu model version, checksum, nguồn, loại task, class schema, runtime và dataset train tham chiếu; phân biệt liên kết do người dùng khai báo với provenance đã xác minh. Checkpoint thử YOLO nano COCO không được tự gắn vào dataset upload như thể đã train trên dataset đó.

**Không chạy `.pt` tùy ý ngay trong process API.** Kiểm ZIP/container/SHA ở MVP không chứng minh checkpoint an toàn. V1 chỉ cho model nguồn tin cậy được quản trị duyệt; chạy trong worker cách ly không có secret rộng, filesystem hạn chế, giới hạn CPU/RAM/GPU và outbound network. Không tự cài package/code từ model. Upload chưa tin cậy giữ quarantine; không đưa dữ liệu nhạy cảm vào model chưa tin cậy. PyTorch coi việc chạy model không tin cậy tương đương chạy code không tin cậy; sandbox không thay thế việc chọn nguồn tin cậy. [PyTorch security policy](https://github.com/pytorch/pytorch/security/policy).

## 6. Database, storage và migration

| Nhóm metadata đề xuất | Nội dung |
|---|---|
| Identity/access | Profile liên kết Supabase Auth, app sessions, workspace/project, memberships, invites |
| Assets | Upload sessions, asset/version, dataset manifest, model version/train-reference, validation status |
| Curation | Collection/goal, task, session, proposal, feedback, snapshot, approvals, release |
| Execution | Job, attempt, lease, progress/event, artifact, reservation/quota usage |
| Governance | Audit event, retention/deletion request, export/grant |

Mọi resource nghiệp vụ thuộc workspace/project; tham chiếu dùng constraint/FK đảm bảo không nối sai project, không chỉ kiểm ở FE. Các bảng workspace-level có scope workspace riêng. Query list/search/metric phải cùng phạm vi với đọc một resource.

Chuyển nghiệp vụ SQLite sang Supabase Postgres trước khi mở nhiều instance/người dùng. BE sở hữu truy cập DB. Ưu tiên runtime role Postgres riêng **không `BYPASSRLS`**, với tenant/actor context do BE thiết lập trong từng transaction; reset context khi dùng pool. Bảng nghiệp vụ không cấp quyền trực tiếp cho `anon/authenticated` qua Data API trong phương án BFF này. RLS kiểm membership/scope là lớp bảo vệ thêm, không thay kiểm capability tại command.

Đường admin/migration và job control phải tách privilege; secret C1 hiện tại không tự trở thành credential ít quyền bằng cách tạo key mới. Phải thiết kế grants/runtime role phù hợp để giảm blast radius. Không tuyên bố “đã bật RLS nên BE dùng secret cũng được RLS bảo vệ”: secret/service role vượt RLS. Views/RPC cũng cần review quyền và test allow/deny. [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).

Raw, model, embedding, preview và export durable nằm S3 private. Local disk chỉ scratch/cache có giới hạn và dọn định kỳ; media/field retrieval có thể tải theo nhu cầu. Object/cache key và active input dedup có tenant scope; không lộ việc workspace khác đã có cùng hash. Phân biệt asset ID theo ownership với content/unit ID thuật toán hiện có để giữ đúng snapshot và cache semantics.

Migration dự kiến: backup SQLite và inventory S3 → tạo một workspace/project legacy có owner được xác nhận → map catalog/relations/jobs/artifacts → kiểm counts/checksum/replay snapshot/export → chạy thử staging → cutover có cửa sổ ghi dừng → kiểm sau chuyển. Giữ bản cũ chỉ đọc để rollback; ghi mới sau cutover phải có kế hoạch đối soát trước rollback, không quay về SQLite làm mất dữ liệu. Không chuyển toàn bộ trong một commit lớn.

## 7. Bảo mật: rủi ro và kiểm soát bắt buộc trước public deploy

| Rủi ro | Kiểm soát đề xuất | Bằng chứng phải có |
|---|---|---|
| Đọc/sửa resource người khác bằng ID | Membership + resource scope tại BE, RLS, kiểm mọi ID liên kết | Ma trận hai workspace/hai project cho toàn bộ endpoint và command |
| Nâng quyền hoặc tự gán owner | Allowlist trường update, policy role hierarchy, transaction, audit | Member không đổi role/invite; không xóa owner cuối |
| Chiếm session, brute force | Managed Auth, MFA quản trị, rate limit login/reset, cookie an toàn, revoke/session rotation | Login/logout/reset/expiry/revoke và race refresh được test |
| CSRF/XSS, open redirect | CSRF token + Origin, redirect allowlist, escape text/comment/filename, CSP, không render HTML tùy ý | Request ngoài origin bị từ chối; payload script không thực thi |
| Upload độc hại, decode gây cạn tài nguyên | Quarantine, allowlist nội dung, sandbox validation, giới hạn bytes/pixels/frames/timeout | File giả MIME, archive bomb/traversal, video lỗi không làm sập API |
| Model thực thi code | Model tin cậy, approval riêng, worker cách ly, secret/egress tối thiểu | Model chưa duyệt không chạy; worker không đọc được secret/asset khác |
| SSRF khi import URL hoặc tải asset | V1 không nhận URL tải tùy ý; BE chỉ sinh URL trusted; nếu mở import URL cần kiểm IP/DNS/redirect + egress | Chặn loopback/private/link-local/metadata và redirect tới chúng |
| Rò S3 URL, log hoặc cache dùng chung | Bucket private, signed URL scoped/TTL, private cache, scrub token/URL khỏi log, không cache auth response ở CDN | FE bundle/log/DTO sạch secret; cache không trả ảnh user khác |
| Lạm dụng GPU/storage/export | Quota, atomic reservation, rate/concurrency limits, bounded batch, idempotency, cancel/deadline | Burst/race không tạo job trùng hoặc vượt quota; lỗi trả rõ |
| SQL/command injection, archive/export path | Query có tham số, không shell chuỗi user nhập, path theo ID server, safe ZIP/CSV | Input chứa SQL/shell/path không thực thi; CSV không kích hoạt công thức |
| Giả progress/result, retry cũ ghi đè | Capability theo attempt, lease, sequence, manifest/hash validation, atomic publish | Replay/corruption/job khác/cancelled không publication |

Upload không được tin extension/MIME client hoặc chỉ một kiểm tra signature. [OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html). Cookie `SameSite` là một lớp bổ sung, không thay toàn bộ chống CSRF. [OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html). Nhập URL từ user là một luồng cần thiết kế riêng, nhất là redirect và truy cập mạng nội bộ. [OWASP SSRF](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

Không thêm WAF rồi coi mọi vấn đề đã giải quyết. Khi triển khai, lập threat model và checklist yêu cầu có test/evidence theo [OWASP ASVS](https://owasp.org/projects/asvs), chọn mức áp dụng theo độ nhạy dữ liệu. Security review/pentest trước mở public; theo dõi và vá định kỳ sau đó.

## 8. Vận hành và quản trị dữ liệu

- Tách dev/staging/prod, credential và bucket hoặc policy tương đương; HTTPS, trusted hosts/proxy, CORS allowlist nếu khác origin. Runtime không mở dev server/debug tooling hoặc endpoint nội bộ ra Internet.
- Secrets ở secret store của deployment; AWS dùng workload IAM role khi có thể. Không commit `.env`, không ghi credential/signed URL trong trace, lỗi hay artifact. Có quy trình rotation/revoke và kiểm CI bundle.
- Worker/API non-root, image/dependency version pin, quét dependency/secret, cập nhật decoder/ML runtime. Không cấp Docker socket, đường dẫn host tùy ý hoặc admin DB cho executor.
- Audit ai mời/cấp quyền, đọc/tải/export, chạy/hủy job, review/release/xóa; user không sửa audit. Log ứng dụng không chứa raw/password/token. Cảnh báo failed login, export bất thường, queue kẹt, lease mất, S3 lỗi và chi phí tăng.
- Backup metadata và manifest, chính sách chống xóa nhầm cho S3, kiểm restore thật và mục tiêu RPO/RTO được nhóm chốt. Versioning/retention/lifecycle cân đối chi phí; `temp/` chỉ chứa dữ liệu tái tạo được, không áp dụng cleanup temp vào raw.
- Có ngân sách storage/GPU/egress, quota workspace, usage report; kiểm vùng S3/Supabase/worker và quyền đưa dữ liệu qua Kaggle. Free tier hoặc 100 GB mục tiêu không phải hạn mức miễn phí đã xác nhận cho deployment.
- Chốt dữ liệu được phép upload, quyền sử dụng, PII/biển số/khuôn mặt, retention, yêu cầu xóa và phạm vi xóa backup. Chức năng hỗ trợ redaction khi cần; chỉ đưa dữ liệu được phép sang nhà cung cấp compute.
- Chốt ai trực vận hành và playbook sự cố: khóa session/user, chặn job/export, revoke URL/credential bằng biện pháp phù hợp, giữ audit, khôi phục và thông báo theo nghĩa vụ đã xác định.

## 9. Roadmap từng lát, có điều kiện hoàn thành

Thứ tự dưới đây là đề xuất. Chưa ước lượng lịch hoặc cam kết tải khi chưa đo. Mỗi lát cần docs trước, migration/test riêng và checkpoint; không gom thành một đợt viết lại toàn hệ thống.

| Mốc | Phạm vi | Điều kiện hoàn thành |
|---|---|---|
| **R0 — chốt thiết kế** | Workspace/project, role matrix, auth/BFF, DB/storage scope, threat model, migration | Người dùng duyệt; CONTRACTS/architecture/UI và quyết định cập nhật |
| **R1 — một luồng private hoàn chỉnh** | Supabase Auth + app session; Postgres legacy migration; membership/permission; login → project → video list/preview | Hai user/hai workspace không đọc chéo; media/thumbnail/cache kiểm quyền; không secret FE; rollback diễn tập |
| **R2 — quản lý asset** | Invite/admin UI; upload S3 multipart/validation/quota; dataset/model registry; archive/restore | Upload dở tiếp tục hoặc cleanup đúng; file/model chưa ready không vào compute; quyền download tách preview |
| **R3 — execution dùng chung** | Orchestrator tách API, queue/attempt/cancel, tenant quota, feature/artifact access, runtime model cách ly | Restart/multiworker không chạy trùng; cancel/revoke không publish sai; GPU/storage abuse được chặn |
| **R4 — team curation** | Collection/task/assignee; feedback concurrency; review queue; approval/release/export | Conflict không mất feedback; release immutable; approval gắn snapshot; truy vết đầy đủ |
| **R5 — pilot deploy** | Staging/prod, observability/audit, backup/restore, security/load review, budgets và incident runbook | Gate §10 đạt; thử với nhóm được mời, dữ liệu được phép và hạn mức rõ |
| **R6 — mở rộng sau pilot** | Annotation integrations nâng cao, SSO, chia sẻ có hạn, API/service accounts, billing, executor khác | Dựa trên nhu cầu/tải thật; từng khả năng có quyền/quota/audit/test riêng |

Đánh giá chất lượng thuật toán chạy song song từ R1, không chờ R6: bộ truy vấn/tiêu chí có nhãn độc lập, negative/không có mẫu phù hợp, precision@K/coverage/duplicate/review time, so baseline random/uniform và kiểm downstream khi có nhãn/train thật. Không tự đổi ngưỡng để luôn đủ batch; không claim uplift khi chưa có benchmark.

**Lát nên làm đầu tiên sau khi duyệt R0: R1 theo chiều dọc**, gồm cả login, ownership và quyền media trên một project private. Các endpoint chưa chuyển phải bị chặn trên môi trường hosted; không để legacy routes mở toàn kho cạnh luồng mới. Chưa làm UI quản trị lớn, realtime hay SSO trước khi lát này chứng minh được cô lập dữ liệu.

## 10. Gate kiểm thử trước pilot và public

1. **Authorization:** kiểm allow/deny cho từng action × role; A/B khác workspace và khác project cùng workspace. Bao phủ list/get/upload/prepare/proposal/feedback/evidence/view/metric/media/model/snapshot/job/export và mọi ID tham chiếu. Không chỉ test Happy Path.
2. **Session:** cookie/JWT giả, token hết hạn, logout, reset password, revoke membership, MFA/step-up, CSRF, open redirect, invite dùng lại, refresh đồng thời. Tài nguyên không thuộc quyền trả lỗi nhất quán, không lộ có tồn tại hay không.
3. **Storage:** PUT replay không sửa asset đã publish, download bị kiểm quyền, multipart/quota race, cache không rò tenant, signed URL không vào log. Test và ghi rõ cửa sổ còn hiệu lực của URL đã cấp sau revoke.
4. **Execution:** restart API/orchestrator, lease hết hạn, hai worker tranh job, URL hết hạn, Kaggle mất session, partial/corrupt result, job cancel, model/raw đổi checksum. Không có duplicate publication và không stop session của job khác.
5. **Curation:** feedback xung đột, reviewer tự duyệt bị chặn nếu policy yêu cầu, sửa snapshot mất approval, export đủ đúng ảnh/frame/VFR, source/dataset/model versions tái lập được.
6. **Security:** input độc hại, path traversal/ZIP bomb, decoder timeout, checkpoint chưa tin cậy, SSRF nếu có URL import, rate limit và secret scan. Đánh giá độc lập trước public; không còn finding nghiêm trọng chưa xử lý hoặc chưa được chấp nhận có trách nhiệm.
7. **Vận hành/tải:** restore backup thật, deployment rollback, mất DB/S3 không báo thành công giả, queue/alert hoạt động. Đo các cỡ video 1/10/60 phút và tải đồng thời mục tiêu được chốt; theo dõi RAM/disk/latency/egress/GPU, không coi smoke 8 frame là kiểm tải video dài.
8. **Chất lượng sản phẩm:** truy vấn có/không có đáp án đúng; không lấp batch bằng mẫu lệch tiêu chí. Nếu thuật toán còn thử nghiệm, UI ghi rõ và mọi release cần review người thật; vẫn không được claim hiệu quả chưa chứng minh.

Kết quả gate phải có artifact và người chịu trách nhiệm. Pilot private có thể giới hạn tính năng/tải đã kiểm chứng; public deploy chỉ sau phạm vi endpoint và rủi ro đã được review tương ứng.

## 11. Những quyết định cần chốt khi bắt đầu R0

| Câu hỏi | Đề xuất mặc định cho V1 |
|---|---|
| Platform cho ai? | Nhóm được mời, dữ liệu private; chưa mở signup/public share |
| Đơn vị sở hữu? | Workspace chứa project; member thường chỉ thấy project được giao |
| Admin có đọc mọi project không? | Có trong V1 và phải thông báo rõ; private với admin là yêu cầu khác |
| Đăng nhập bằng gì? | Supabase Auth, BE quản lý app session; chọn email/password hoặc Google theo nhóm |
| Ai được tải/export? | Capability riêng; Viewer không tự tải raw; preview được cấp quyền rõ |
| Release cần mấy người duyệt? | Một Reviewer khác tác giả; Manager publish sau approval |
| Model nào được chạy? | Nguồn tin cậy, người có quyền bảo mật duyệt; checkpoint khác quarantine |
| Upload đi đường nào? | BE cấp multipart URL scoped, bytes lên S3; proxy BE nếu cần cách ly transport tuyệt đối |
| Compute và dữ liệu qua Kaggle? | Chỉ dữ liệu được phép; quota và deadline; sau pilot đánh giá executor deploy |
| Quy mô/ngân sách/retention? | Chốt GB/workspace, phút/frame mỗi job, concurrency, chi phí tối đa, RPO/RTO và thời gian lưu trước test tải |

Các lựa chọn ở cột phải là **đề xuất**, chưa phải quyết định đã xác nhận. Chưa sửa auth, schema nghiệp vụ, quyền bucket hoặc luồng đang chạy theo tài liệu này.
