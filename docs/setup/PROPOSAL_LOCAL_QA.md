# N1-DB #129 — kiểm chứng local trước release

2026-10-13. Tiếp tục từ commit 2675c22, implement theo người dùng duyệt; chưa push/deploy hoặc đổi policy/schema live.
[Contract §21.23](../design/CONTRACTS.md), [plan](../backlog/PROPOSAL_LATENCY_PLAN.md), [architecture](../architecture_diagram.md).

## Thay đổi

- Pilot keep/drop/clear/undo là draft RAM; một POST proposals lưu lô decisions + freeze basis + reserve/enqueue nguyên tử.
  Version tăng một lần nếu đổi; retry cùng key/body không append/reserve lần hai; lỗi enqueue rollback toàn lô.
- GET job/list/result, GET media và lookup ảnh không khóa độc quyền profile. Account active/owner/scope/READY vẫn kiểm.
  Discovery lọc PROPOSE/phiên + pagination; sau submit poll đúng job, backoff/dừng và result một lần.
- Batch ảnh READY đọc/ký URL theo lô, giữ order/duplicates/TTL, không object_key/decoder/job ảnh.
  Frontend gom tối đa 16 lookup, tự chia nhỏ khi policy cap thấp; POST chỉ đọc có token refresh an toàn.
- Publish giữ transaction/quota/fence/receipts/recovery, gom aggregates/settlement reads và pipeline writes đã có dependencies rõ.
  Không stored function mới hoặc DB replica. P4 cache nâng thêm, compute lanes rollout và feature reference/PII vẫn riêng.
- Browser test bắt lỗi job SUCCEEDED trước response retry: UI trước đó không tải batch mới. Đã sửa terminal replay path,
  test chủ động hoàn tất worker job rồi làm mất response và retry; không chỉ chạy happy path.

## Test local thực hiện như thế nào

**PostgreSQL 16.15 thật** trên 127.0.0.1:54329, mỗi pytest session DB test riêng, migration thật/RLS/roles;
**S3 moto server** giữ object thật trong môi trường giả lập, video MP4 thật tổng hợp được PyAV decode.
API Uvicorn thật qua HTTP, JWT ES256 test, worker supervisor/process spawn CPU/MEDIA thật. Không Supabase Auth/S3/Kaggle production.
Cache/temp/dependencies dưới data/output; không secret hoặc dump trong Git.

Test mới kiểm enqueue rollback/quota error/CAS/invalid unit/feature missing/target budget, replay/body mismatch,
decision seq/version freeze, A/B/inactive, job/list và batch ảnh trả trước khi nhả profile lock đang giữ,
batch order/duplicate/policy cap/source DELETE_PENDING và không tạo job ảnh.
Regression quota/queue/media còn kiểm base/bonus/refund/lease/cancel/UNKNOWN/publish failure và clip/raw semantics.

**Chrome headless thật qua Playwright**, frontend Vite pilot trên localhost proxy đúng API test:

1. Seed video 12 s/48 units và DINO artifact có mảng giả tất định, proposal thật 16 picks; ảnh S3 moto tải hiển thị được.
2. Giữ cả 16: zero request decisions/proposal; undo zero writes; reload xóa draft.
3. Submit 16 changes; cố ý làm mất response sau API commit và worker SUCCEEDED; UI giữ draft để retry.
4. Retry đúng body/key: DB chỉ 16 decisions, tổng 2 PROPOSE jobs (1 seed + 1 mới), không job thứ ba; giữ committed 16.
5. UI tải Đợt 2 kể cả response replay đã terminal; chỉ targeted job GET, không account jobs polling;
   chỉ đọc proposal mới, không reload toàn history; một batch 16 ảnh, không standalone decision POST. Không pageerror.
6. Tạo draft mới rồi mở Snapshot: chốt bị disabled và hướng dẫn bấm Đề xuất; chuyển tab không autosave.

Hai POST trong fault test là một submit và một retry cùng job, không hai job. Test uses synthetic features, không quality/CLIP inference benchmark.
Screenshot/evidence ignored: `output/proposal-local/browser-after.png`, `browser-result.json`, `browser-db.json`, `browser-release.log`.

## Kiểm tra và số đo

Focused PG: 74 regression bước đầu; sau chỉnh cuối 28 quota/media/atomic tests và 19 API/atomic tests đạt.
Frontend cuối: **34 Vitest tests**, tsc và build local/pilot đạt. Build có warning chunk >500 kB đã có, không lỗi.
Browser release cuối: **1 passed**, test chủ động completed-job replay + snapshot draft guard + không reload proposal history, 54,19 s.
Full backend cuối **526 passed, 5 skipped, 1 warning**, 464,81 s, exit code 0 (`complete.log`/`complete.exit`).
Năm skip: Windows symlink, hai checkpoint YOLO local thiếu, hai Linux-only; known hook Unicode test loại riêng theo AGENTS.
Không dùng lượt có assertion DTO cũ làm pass.
Lượt full trước 525 pass/5 skip/1 fail do test exact JobDTO keys chưa cập nhật accepted_state_version nullable;
đã cập nhật theo contract và API suite rerun đạt. Known hook Unicode test loại rõ theo AGENTS, không tính như tự pass.

Baseline 5 lần trước sửa, cùng seed=11/batch16/fixture input: publication **34 db.execute, 7 db.enqueue, 3 db.pipeline** mỗi lượt.
Candidate final 5 lượt sau khi browser/suite khác dừng: **22 db.execute, 9 db.enqueue, 4 db.pipeline** cùng nhánh;
publication p50 baseline **52,590 ms**, candidate **45,653 ms** (giảm khoảng 13,2%). N=5 mỗi bản, chạy tuần tự trước/sau,
không randomized/interleaved A/B, shared máy dev; không suy SLA/p95 hoặc mức tăng tốc cloud từ thời gian localhost này.
Logs `baseline.jsonl`, `candidate-final.jsonl`, `bench-final.log` dưới output/proposal-local.
Đây là counts spans SQL từ thread publish,
**không phải packet/round-trip count** (fetch/sync/BEGIN/COMMIT và heartbeat thread chưa thống kê ở đây).
Không lấy 260 ms probe cloud nhân số SQL để hứa thời gian mới. Publication local vài chục ms không đại diện cloud 10–13 s.

## Đủ cơ sở tới đâu

Local functional/fault/UI kiểm đúng luồng; sau full regression xanh có cơ sở build/canary có rollback để đo hạ tầng thật.
Chưa chứng minh publish cloud giảm 50%/dưới5 s, mixed-load SLA, browser Supabase login/expiry hoặc AWS thật.
Trước rollout pin cùng source FE/API/worker, smoke image non-root/import nhẹ, backup/drain/rollback và account/source test riêng.
N1 không cần migration/policy mới; giữ lanes env unset/legacy nếu chưa capacity/slots gate #127.
Không auto backfill/GPU/PII/export. Client mới cần API mới cho changes/batch; combined FE/API triển khai cùng image,
body không changes vẫn tương thích client cũ. Không dùng kết quả local để tự chuyển DB/topology/numeric policy.
