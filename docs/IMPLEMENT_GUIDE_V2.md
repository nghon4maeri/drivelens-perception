# Hướng dẫn thực thi deployment RAV-26

Cập nhật 2026-10-13 (#128). [Guide cũ](archived/2026-10-13/docs/IMPLEMENT_GUIDE_V2.md) giữ lịch sử.

## Read-order

1. [AGENTS](../AGENTS.md) → [PLAN](../PLAN.md): đúng một lát và authorization hiện có.
2. [Docs index](README.md) → [CONTRACTS](design/CONTRACTS.md) §21 và core interfaces liên quan → [architecture](architecture_diagram.md)/[UI](design/UI_DESIGN.md).
3. [Status live/candidate](setup/DEPLOYMENT_STATUS.md), spec/runbook/plan của lát và evidence gate.

Specs active dẫn CONTRACTS; không còn dùng nhãn D0 để nói toàn bộ pilot chưa implement.
PII/report/reference/capacity phần chưa xong vẫn ghi rõ Hướng tới, không viết docs để hợp thức hóa code lệch.
Local v1/SQLite còn là compatibility, không là deployment multi-user. Archive chỉ đọc khi cần history/core local.

## Làm một lát

Docs trước code; thiết kế mới phải được duyệt theo AGENTS. Giữ owner/RLS/CAS/fence/lease/quota/receipt/recovery.
Không tự seed policy numbers, claim provider ABSENT khi UNKNOWN, cấp quota hoặc mở export chưa có PII gate.
Không tự chạy GPU/deploy/push từ roadmap. Credentials chỉ env đúng host/role, không copy toàn .env vào image/FE/log.
Test candidate Contabo namespace riêng, không dùng production DB để chạy pytest.

## Kiểm tra

Mỗi terminal PowerShell từ repo root:

```powershell
. .\scripts\use_project_storage.ps1
python -m pytest tests -q --basetemp=output/pytest-tmp
python -m ruff check src tests
```

RLS/transaction cần PostgreSQL thật: [runbook §6](setup/PILOT_RUNTIME_RUNBOOK.md).
Hook test lỗi Unicode đã ghi ở AGENTS: nếu loại thì báo rõ, không gọi toàn bộ không ngoại lệ.
TestClient socket bị sandbox chặn thì xử lý quyền test, không sửa app để né.
Đụng FE: source storage, vào src/web, `npx tsc --noEmit`, vitest và build local/pilot phù hợp.
Cache/model/temp/output trong data/output; PIL/cv2.imencode cho đường Unicode, không đổi pagefile.

Docs-only: kiểm relative links/anchors trọng yếu, consistency source/image/gate, archive manifest và `git diff --check`;
không chạy lại ML/full suite hoặc redeploy chỉ vì sửa docs. Evidence cũ phải ghi checkpoint, không gọi là test mới.

## Kết thúc và release

Diff đúng lát, giữ thay đổi người dùng; xác nhận output kiểm tra → commit tiếng Việt không dấu → tick PLAN → dừng.
Không push nếu chưa được yêu cầu. Release riêng theo runbook: source/image/migration/policy compatibility,
backup/drain, canary/health/functional smoke và rollback; host Live không đồng nghĩa đủ gate pilot.
