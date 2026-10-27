# DEP7-Basic-Stability — kiểm chứng sau review AGY

2026-10-13. Nhánh `codex/basic-flow-stability`; tiếp tục từ `ca5bc13`, không merge/push/deploy.

## Thay đổi

- SYSTEM dry-run liệt kê receipt chưa cleanup ngoài danh sách nguồn có thể import. `--cleanup-only` + operator-confirm đối soát exact inventory dù nguồn UNAVAILABLE/DELETE_PENDING hoặc không có policy enabled; không PUT/publish mới. Vẫn giữ advisory lock, proof writer dừng, guard ảnh READY và reservation tới khi DELETE/HEAD xác nhận sạch. Receipt đổi sau dry-run bị từ chối.
- Kiểm raw với `worker_temp_bytes` trước download. `encode_batch` nhận byte budget cho importer, kiểm trước mỗi lần ghi variant; không đổi pixel, sampling 4 fps, IDs hoặc encoding.
- Giữ fix key remount của AGY. Đổi tên test cache media đúng phạm vi; thêm browser regression chạy toolbar/state/polling thật bằng Edge profile mới, chặn mọi request ngoài localhost.
- Điều chỉnh trạng thái PLAN theo gate thực tế; không dùng commit hoặc test HTTP để chứng minh browser/hosting/độ mượt toàn pipeline.

## Bằng chứng local

Output chi tiết trong `output/basic-stability/` (gitignore), cache/temp/data trên ổ repo D.

| Gate | Kết quả | Artifact |
|---|---|---|
| Regression toàn repo, PostgreSQL 16.15 thật + moto | 496 đạt, 5 skip; 435,08 giây, tách hook đã biết | `codex-full.log` |
| Focused importer/delete/frames/worker | 57 đạt; 75,95 giây | `codex-focused.log` |
| Importer sau bổ sung stale receipt guard test | 19 đạt; 29,14 giây | `codex-final-importer.log` |
| Vitest / TypeScript / build local + pilot | 31 đạt; tsc và hai build exit 0 | `codex-frontend.log`, `codex-build-*.log` |
| Ruff / node syntax / diff whitespace | đạt | lệnh dưới |
| Browser RED bỏ fix key trong bản copy output | backend READY, chỉ 2/3 thumbnail, không reload/page error | `browser-red/result.json`, `after-ready.png` |
| Browser GREEN source legacy mới, giữ fix key | 2/2 thumbnail sau toolbar, không reload/page error | `browser-green/result.json`, `after-ready.png` |
| E2E tổng hợp baseline / candidate | mỗi bản 5 lượt; mỗi harness 1 test đạt | `ab-baseline.json`, `ab-candidate.json` |
| E2E video thật 21 giây | đạt; bao gồm clip/backfill/crash/delete/accounting | `video21s.json`, `codex-video21s.log` |
| E2E video thật 5 phút / 103.696.301 byte | đạt; không GPU/embedding | `video5m.json`, `codex-video5m.log` |

5 skip: 1 symlink Windows không có quyền, 2 YOLO thật thiếu `data/models/yolo11n.pt`, 2 Linux SIGTERM/atexit (được chạy riêng trên Contabo). Không skip test PostgreSQL để báo xanh.
Hook `test_codex_prompt_hook_from_subdirectory` chạy riêng vẫn lỗi FileNotFoundError/session.jsonl theo vấn đề PowerShell/path có dấu trong AGENTS; không sửa hooks. Vite có cảnh báo chunk >500 kB, không phải build failure.

Lệnh chính (mỗi terminal trước Python/npm: source `scripts/use_project_storage.ps1`; giữ PYTHONPATH do script cấu hình, chỉ prepend deps moto):

```powershell
python -m pytest tests -q --ignore=tests/test_codex_hook.py --basetemp=output/pytest-codex-full --tb=short
python -m pytest tests/test_pilot_frames_prepare.py -q --basetemp=output/pytest-codex-final-importer
python -m ruff check src tests scripts/pilot_prepare_system_frames.py
node --check scripts/pilot_library_browser_smoke.cjs
node scripts/pilot_library_browser_smoke.cjs
git diff --check
```

Browser harness yêu cầu stack test riêng theo `output/basic-stability/ui-local-stack.py`, Vite pilot trỏ API local, token test trong `ui-session.json`, Playwright test-only ở `output/basic-stability/ui-tests`. Không dùng cookie/profile/account production. RED dùng copy frontend trong output, không revert tracked source. Script browser ở `scripts/pilot_library_browser_smoke.cjs` để tái chạy.

## Đo A/B và giới hạn hiệu năng

Baseline thien `5e05c1c` từ archive `output/thien-local-test`, candidate current working tree. Cùng máy, PostgreSQL/S3 mock/harness/config, video tổng hợp 30 s 640×360 10 fps, 5 lượt, 48 thumbnail. Chạy tuần tự sau dừng UI stack; không dùng số cũ khác lượt chạy để claim tăng tốc.

| Chặng (p50 / p95, giây) | Baseline | Candidate |
|---|---:|---:|
| Upload → READY | 4,345 / 5,596 | 3,916 / 4,667 |
| Thumbnail đầu cold | 0,118 / 0,171 | 0,080 / 0,221 |
| Grid cold | 1,122 / 1,693 | 1,309 / 1,387 |
| Grid warm | 1,319 / 1,661 | 1,400 / 1,497 |
| Frame | 0,046 / 0,068 | 0,073 / 0,202 |
| Clip cold | 0,754 / 0,760 | 0,728 / 0,980 |
| Clip warm | 0,046 / 0,083 | 0,065 / 0,081 |

Mức chậm upload ~4 lần trong báo cáo AGY không tái hiện ở lượt A/B này. Một số chặng vẫn chậm hơn; n=5 nhỏ và chưa randomize/repeat A/B nên không chứng minh tối ưu tổng hoặc xác định nguyên nhân chênh lệch. RAW/frame serving/scoring semantics được giữ.

Video thật 21 giây: upload READY 9,002 giây; peak tổng RSS cây process test ~458 MiB. Video 5 phút: upload READY 167,790 giây, thumbnail đầu 0,069 giây, grid cold 1,382 giây, frame 0,070 giây, clip cold 3,894 giây; peak tổng RSS ~720 MiB. Cây process gồm pytest/API/S3/worker; shared pages có thể tính trùng. Mỗi video thật chỉ một lượt. Build FE chạy đầu lượt video 5 phút nên đây là capacity/functional smoke, không benchmark A/B thời gian preprocess. Không suy wall−CPU thành network, không coi local moto là cloud.

Luồng 5 phút chạy được nhưng 168 giây chờ READY còn đáng kể. Chưa đo tách decode/encode/PUT/publish, object/byte/RAM theo từng stage nên giảm DB round-trip/đo preprocessing vẫn là bước tiếp theo trong plan, chưa coi toàn hệ thống đã mượt hoặc mở tính năng tham chiếu mới.

## Contabo

SSH qua khóa phiên đã có: preflight host ~8 GiB RAM, ~6 GiB free, ~72 GiB disk free, worker live healthy. Candidate namespace `/opt/rav26-test/basic-stability-20261013`, base runtime `rav26-worker:pipeline-20261012`, bind source/tests riêng; không env production, không S3/Supabase/Kaggle thật.

- Linux shutdown: **19 đạt**, 6,82 giây; gồm SIGTERM-ignore → kill và parent exit/atexit boundary. Container network none, giới hạn 1 CPU/1 GiB.
- Integration PostgreSQL/S3/worker: **45 đạt**, 302,77 giây; importer, delete/fence/heartbeat, frames, HTTP E2E và spawn/crash/media-while-CPU. Log `output/basic-stability/codex-contabo-integration.log`. Giới hạn candidate 1 CPU/2 GiB; runtime dependencies API/moto cài riêng vào testdeps, không phải image release đã build/pin đầy đủ.
- PG test là container `rav26-basic-test-pg`, PostgreSQL 17 Alpine, internal network `rav26-basic-test-net`, không expose port; moto trong test container. Test dependencies chỉ trong testdeps, không sửa image/live service. Đây là parity/fault smoke trên host, không benchmark cloud storage/DB thật.
- Lỗi transfer archive/dependency test thiếu quyền hoặc thiếu API package đã được sửa trong namespace test; không coi lỗi setup là lỗi sản phẩm hoặc test đạt.

Release còn cần người dùng yêu cầu riêng. Giữ service/image/config/queue live và Render nguyên trạng.

Dọn sau test: container PostgreSQL và internal network test đã xóa sau kiểm label; không còn container candidate, worker live healthy. PostgreSQL local đã dừng, alias R: đã gỡ, Vite/API/worker/S3 browser stack đã đóng. Giữ archive/testdeps/log ở namespace test để đối chiếu; không quét hoặc xóa tài nguyên live.

Các gate của lát ổn định đã được kiểm; việc giảm latency preprocessing/DB và validation Supabase/S3 thật trên release image là công việc tiếp theo, không suy test mock thành pilot cloud hoàn chỉnh. Task 4 báo rõ stage chưa đo và giới hạn, không có cam kết thời gian toàn pipeline.
