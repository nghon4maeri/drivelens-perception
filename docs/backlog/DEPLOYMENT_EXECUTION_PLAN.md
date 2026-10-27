# Deployment — thứ tự thực thi và gates

Cập nhật 2026-10-13 (#128), thay [plan chia việc D0](../archived/2026-10-13/docs/backlog/DEPLOYMENT_EXECUTION_PLAN.md).
Đây là roadmap, không tự cấp quyền deploy/push hoặc đổi scope. [PLAN](../../PLAN.md) chọn một lát.

## 1. Nền tảng đã có

Auth/RLS/owner + CAS; PostgreSQL queue/attempt/slot/lease/fence/quota; direct S3 upload và ảnh READY;
clip từ raw, xóa/retry/cleanup; templates readonly; feature/proposal/YOLO/analysis handlers và tracing.
Bản frames source b195c61 đã live; compute #127 commit 966ab44 mới là candidate test đạt.
[Status](../setup/DEPLOYMENT_STATUS.md) và QA là evidence, không suy toàn bộ acceptance từ checklist code.

## 2. Trước mở tính năng

| Thứ tự | Lát | Nghiệm thu |
|---|---|---|
| 1 | A0 + N1/P0–P5 | Trace đủ, decisions/propose batch nguyên tử, read không giữ khóa ghi, round trips giảm; owner/CAS/idempotency/fence/fault giữ đúng |
| 2 | A2 compute rollout | 1/3 account mixed load, peak RSS/thread/connection budget, policy-slot compatibility, kill/restart/cancel/cleanup, UI latency; rollout/rollback riêng |
| 3 | A3/A4 + N2 | Artifact/version cache có cap/eviction/recovery, ETA từ số đo, source/image/migration/policy matrix và restore drill |
| 4 | A5 + N3–N5 | Dataset/model catalog/provenance/binding, bounded recipe, đúng model/anchor/order và report, cache không double-charge |
| 5 | DEP6 PII/export | Face/plate model + quality eval, frozen snapshot, redaction/report/confirm/ZIP, fault/retry/storage accounting và A/B isolation |
| 6 | N6 + pilot tổng | Representative frame/quality có version; label/retrain ngoài hệ thống, không leakage; browser/GPU/cost/capacity E2E đủ |

[Proposal plan](PROPOSAL_LATENCY_PLAN.md), [N1–N6](POST_BASIC_FLOW_DEVELOPMENT_PLAN.md),
[solutions](ARCHITECTURE_SOLUTIONS.md) giữ chi tiết; một phần A2 đã làm tại #127, phần capacity/rollout vẫn mở.

## 3. Ma trận kiểm tra

- Code thuần: unit deterministic/IDs/pixel/hash/parity; không test mirror implementation.
- Store/queue: PostgreSQL thật RLS/transaction/lock/concurrency/owner/CAS/quota/refund/UNKNOWN/quarantine.
- Storage: moto cho fault tests + S3 thật trong namespace smoke riêng; DELETE/HEAD/receipt/hold recovery.
- Worker: Windows/Linux spawn, supervisor process identity/health/restart/SIGTERM/deadline; model thật khi claim model behavior.
- FE: vitest/tsc/build modes + browser auth/expiry/CAS/URL refresh/grid/clip; HTTP script không thay click-through.
- GPU/PII: provider lifecycle/quota thật và dataset quality độc lập; mock không thay acceptance.
- Release: image thực non-root/health/import nhẹ, backup/restore, compatibility/drain, pin digest/config/policy, canary và rollback.

## 4. Release boundary

Không dùng pytest vào production DB; candidate namespace/S3 prefix/account riêng, cleanup xác minh đúng tài nguyên.
Không tự cấp bonus, auto backfill, sửa policy bất biến hoặc trả QUARANTINED slot khi chưa chứng minh process dừng.
Commit source đã kiểm trước build; weights/cache ở volume, secrets env đúng host, Git push riêng với registry publish/deploy.
Rollout theo [combined](../setup/COMBINED_WEB_RELEASE.md), [worker](../setup/CONTABO_WORKER_RELEASE.md),
[frames](../setup/FRAMES_RELEASE.md) và [compute](../setup/COMPUTE_RUNTIME.md).
