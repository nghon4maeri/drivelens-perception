# Deployment — identity và dữ liệu

Cập nhật #128. Đặc tả hiện hành theo CONTRACTS §21.1–4/9–11; [bản D0 lịch sử/schema detail](../archived/2026-10-13/docs/design/DEPLOYMENT_IDENTITY_DATA_SPEC.md).
Schema thực là migrations immutable và adapter source theo CONTRACTS, không copy proposed SQL/RPC cũ để áp vào DB live.

## Boundary và invariants

- Supabase verified JWT → ActorContext user server-derived. UUID owner/role không lấy từ body/header tự khai.
- API role rav_pilot_api, worker rav_pilot_worker; authenticated RLS claims cho read, explicit owner/scope checks cho backend write.
- UUID catalog/API ↔ core_source_id/unit anchor, conversion theo contract; không đổi bằng timestamp rounded.
- Source PRIVATE owner-only, SYSTEM/protected readonly; templates/history public trong pilot chỉ đọc.
- Session private, config/spec/source references; CAS state_version/resource_version, append-only decisions, frozen proposals/snapshots.
- Fork creates empty owner session cùng nguồn/spec; không copy log/result/quota hoặc auto propose.
- Lock order profile → sources UUID sorted → session → receipt/pin; read optimizations phải giữ auth/status/CAS.
- RESTRICT source referenced bởi session/snapshot/job/model/reference; DELETE_PENDING/tombstone audited, không cascade raw history.
- Admission/job policy snapshot immutable; role bootstrap/seed/import/migration operator explicit, boot chỉ kiểm checksum/config.

## Data lifecycle và phần còn thiếu

Upload/frame/artifact/object receipts có storage accounting USER/GLOBAL; job holds/pins/fence cho recovery.
Dataset/model catalog đầy đủ pilot/binding phiên/training provenance thuộc N3–N5, không suy local catalog là đã expose multi-user.
Source/schema details theo CONTRACTS §21.3/11/13–22; API v2/error/idempotency theo §21.4 và route adapter.
Acceptance PostgreSQL thật RLS/A-B/CAS/immutability/RESTRICT/race; [execution plan](../backlog/DEPLOYMENT_EXECUTION_PLAN.md).
[Architecture](../architecture_diagram.md), [runtime runbook](../setup/PILOT_RUNTIME_RUNBOOK.md), [status](../setup/DEPLOYMENT_STATUS.md).
