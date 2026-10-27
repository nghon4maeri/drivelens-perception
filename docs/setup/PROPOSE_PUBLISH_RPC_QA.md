# PROPOSE publish RPC và mô phỏng latency — 2026-10-14, #131

Người dùng duyệt mục 1 gom publish/tất toán, rồi bổ sung đo/mô phỏng để ước tính luồng.
Candidate local, chưa apply migration 011 cloud hoặc deploy worker. Pilot local đang nối cloud vẫn dùng worker live cũ.
Thiết kế [CONTRACTS §21.24](../design/CONTRACTS.md), [architecture](../architecture_diagram.md).

## Thay đổi

- Migration 011 thêm hàm SECURITY INVOKER, quyền EXECUTE worker-only, search_path cố định.
- PROPOSE reservation đủ/không object inventory hoặc storage/GPU hold: một SELECT gọi hàm thay
  22 execute đồng bộ + 9 enqueue + 4 nhóm pipeline. Query capability thêm một lần lúc runtime chưa biết;
  benchmark steady-state đã biết capability, không tính query đầu này.
- Transaction giữ profile/job/attempt/slot/session, fence/lease/cancel/frozen basis, proposal/hash,
  CPU/GPU receipts, consume base/bonus đúng parts/FIFO, refund/pins/close/free/quarantine.
- Deficit cần reserve bổ sung hoặc inventory/holds đặc biệt: fallback legacy trong cùng transaction,
  trước mutation phía RPC. Ngoại lệ có thêm một lời gọi kiểm RPC; chưa claim giảm 31 execute xuống 1 cho đường này.
- Migration additive staged: schema readiness chấp nhận thiếu 011 vì có fallback; checksum lệch vẫn chặn.
  Khi operator áp 011, restart worker/runtime để làm mới capability. API/anon/authenticated không được execute hàm.
  Không bỏ CPU quota, sửa UI/queue/model hoặc thay policy numbers trong lát này.

## Kiểm thử

- Backend regression: **540 passed, 5 skipped, 1 warning, 516.10 s**, exit 0;
  `python -m pytest tests -q --ignore=tests/test_codex_hook.py --basetemp=output/pytest-rpc-complete`.
  Hook Unicode lỗi có sẵn loại riêng; skip symlink Windows, hai thiếu YOLO checkpoint và hai Linux-only.
  Đây là suite RPC; file benchmark opt-in thêm sau collection và được chạy riêng ở dưới.
- Có 14 case RPC: replay với output sai sau commit, concurrent publish cùng attempt, wrong worker/fence/generation,
  expired lease/cancel/context/result/inactive rejection, base+bonus/refund, deficit fallback,
  lỗi INSERT thật sau consume rollback, DB chưa migration vẫn ready+legacy, quyền EXECUTE.
  Regression curation 11 đạt; regression queue/quota/objects/store kiểm trong suite.
- Ruff `src tests scripts/benchmark_proposal_latency.py`, diff check và CLI report đạt. Không đổi FE nên không chạy lại tsc/build.
- Mô phỏng publish: **18 passed**, 85.64 s: legacy/RPC × chờ 0/100/260 ms × 3 lượt.
  PostgreSQL thật cô lập, feature artifacts có seed giả để tách publication khỏi chất lượng AI.
  Kết quả đều SUCCEEDED, một proposal và hai receipts đúng usage. Không production pytest.

Evidence: `output/proposal-local/rpc-complete.{log,exit}`, `rpc-simulation.{log,exit}`,
baseline trước sửa `rpc-baseline.jsonl`; `output/proposal-latency/{samples.jsonl,report.json,report.log}`.
Lỗi fixture bonus thiếu operator provenance đã sửa bằng pg.bonus; không coi lượt đỏ là pass.

## Số đo và mô phỏng

| Đo/giả lập | Kết quả |
|---|---:|
| SELECT 1 loopback local, 20 mẫu | p50 0.065 ms; p95 0.168 ms |
| SELECT 1 VPS → Supabase, 20 mẫu, connection sẵn | p50 262.9 ms; mean 276.2 ms; p95 264.3 ms; max 527.5 ms |
| CLIP text VPS, process cô lập cap 1 CPU/1.5 GB, offline | cold import+load+first inference 20.715 s |
| CLIP model warm, query mới, batch 1/20 mẫu | p50 63.4 ms; p95 72.5 ms |
| CLIP query trúng text cache | p50 0.010 ms |

SELECT 1 bao gồm driver/protocol/pooler/server, không phải phép đo pure network RTT.
CLIP dùng đúng revision `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`; process benchmark riêng,
không suy worker hiện đang cold hoặc toàn bộ handler chỉ mất 63 ms. Không chạy GPU hay model PII/YOLO ở probe này.
Các probe cloud chỉ đọc DB hoặc dùng model/cache offline read-only; không cập nhật live schema/config.

| Chờ cố định tại mỗi điểm đồng bộ | Publish legacy p50 | Publish RPC p50 |
|---|---:|---:|
| 0 ms | 111 ms | 29 ms |
| 100 ms | 2.889 s | 0.332 s |
| 260 ms | **7.360 s** | **0.819 s** |

Mô hình local thêm delay mỗi execute đồng bộ, một lần mỗi nhóm pipeline và hai boundary BEGIN/COMMIT.
Legacy 28 điểm chờ, RPC 3; **đây không phải số packet/round trips trên wire**.
Không giả lập TLS, implicit pipeline fetch sync, savepoints, lock contention, reconnect hoặc tải cạnh tranh.
N=3 mỗi trường hợp; p95 là mẫu lớn nhất và không đủ để nghiệm thu SLA. Suite khác chạy cùng máy;
số baseline CPU local có nhiễu. Dùng chênh lệch chờ có kiểm soát, không khẳng định cloud chắc đạt 0.819 s.
Live trước đó publish 8.7–12.8 s, khác mô phỏng vì các chi phí chưa mô hình hóa.

## Ước tính cả luồng

Thay riêng publish bằng mức mô phỏng RPC, giữ chờ/compute/điều phối đã quan sát:
ba job đường thường từ khoảng 21.9–25.3 s có thể còn **14.0–16.3 s**.
Tổng lấy created_at đến worker attempt kết thúc/commit, không dùng updated_at trong transaction.
Chưa gồm latency FE polling/result/thumbnail. Job 42.8 s có nhiều thủ tục/deficit nên report không áp
đường RPC chuẩn để hứa cải thiện cho nó. Chưa đạt mục tiêu batch p95 4 s; cần claim/context/cache/model các lát sau.

## Tái chạy

Source storage trước Python. Dùng PostgreSQL test theo [runbook](PILOT_RUNTIME_RUNBOOK.md), không URL production.

```powershell
$env:RAV_PILOT_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:54329/postgres'
$env:RAV_PUBLISH_SIM_OUTPUT='output/proposal-latency/samples-new.jsonl'
$env:RAV_PUBLISH_SIM_RTT_MS='0,100,260'
$env:RAV_PUBLISH_SIM_RUNS='3'
python -m pytest tests/test_pilot_publish_latency.py -q --basetemp=output/pytest-rpc-simulation-new
python scripts/benchmark_proposal_latency.py --output output/proposal-latency/report-new.json --samples output/proposal-latency/samples-new.jsonl
# Optional cached CLIP local cold/warm probe: add --model; offline, no downloads.
```

Để hiệu chỉnh trace: `--trace`, `--job-times`, `--calibration-dir`, xem CLI --help.
Remote probe scripts/evidence nằm trong output/proposal-latency; không secrets trong output JSON.
Triển khai tiếp cần backup/011 checksum/compatibility, worker restart, canary PROPOSE và fault/quota smoke cloud,
đo publish thật rồi mới kết luận mục tiêu 1–3 s. Chưa push/deploy ở lượt này.
