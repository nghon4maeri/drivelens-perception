# Deployment — queue, storage và compute runtime

Cập nhật #128. Authority CONTRACTS §21.5–7/9/12–15/20–22; [D0 transaction/schema chi tiết](../archived/2026-10-13/docs/design/DEPLOYMENT_RUNTIME_STORAGE_SPEC.md)
là lịch sử, không config/SQL release. [Architecture](../architecture_diagram.md) mô tả luồng hiện hành.

## Runtime invariants

PostgreSQL rav_pilot_job/attempt/slot giữ job authority; API enqueue freeze input/policy + quota/storage holds/source pins nguyên tử.
Claim CAS/SKIP LOCKED/fairness/profile lock, snapshot-policy slots, lease+heartbeat+fence. CPU/GPU/MEDIA queue khác trách nhiệm.
UNKNOWN/quarantine không release tới proof process/provider stopped và usage/cleanup đối soát; cancel không là kill verified.
CPU process CPU ms, GPU owned session wall ms riêng; actual receipts immutable, refund/extension đúng nguồn base/bonus/window.
Kaggle budget snapshot phải tươi; launch marker/handle/STOP_PENDING/UNKNOWN không giả ABSENT để chạy session thứ hai.

## Storage invariants

Direct Browser S3 upload staging capability; validate raw hash/probe/limits/canonical và frame_set before READY.
Intent/receipt/reservation bền trước PUT; checksum/HEAD trước publication. Staging hold tới expiry+grace cleanup.
Thumbnail/frame READY lookup stored images, no decode fallback; NOT_PREPARED explicit. Context clip vẫn MEDIA render/cache raw.
Cache raw/artifact/model keyed SHA/version, bounded disk/RSS; eviction không đổi durable authority hoặc private permissions.
DELETE/HEAD ngoài lock transaction nhưng preflight + fence/lease/cancel recheck trước receipt; partial delete giữ pending/accounting.
SYSTEM import inventory/session advisory lock + GLOBAL staging reservation, atomic COMMITTED, exact cleanup proof và recovery.

## Compute candidate và rollout

Live source frames một CPU process + MEDIA/GPU. #127 CPU lanes partition kinds + one assigned slot/lane,
long-lived model owner, thread/RSS/cgroup guard và health/restart identity; CLIP text-only parity + YOLO resident digest cache.
Không config lanes giữ legacy, không tự provision policy/slots. Heavy owner limits đếm HELD/QUARANTINED non-MEDIA.
PII handler chưa có; BULK export routing không là implemented handler. Docker Linux không tạo VM mỗi lane; native chưa test.
[Config và rollout](../setup/COMPUTE_RUNTIME.md), [QA](../setup/COMPUTE_RUNTIME_QA.md),
[source/image/policy compatibility](../setup/DEPLOYMENT_STATUS.md). Mixed-load/peak budgets bắt buộc trước mở production profile.
