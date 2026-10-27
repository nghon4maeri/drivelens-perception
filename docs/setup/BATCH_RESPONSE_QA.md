# Proposal response theo batch — 2026-10-16

Yêu cầu người dùng: trình duyệt chỉ nhận dữ liệu của batch/mẫu đã duyệt, không nhận bảng điểm mọi ứng viên.
CONTRACTS §21.26 và architecture đã cập nhật trước code.

- Formatter public nhẹ dùng chung interactive/API checkpoint/read/list và local API.
- Scores theo thứ tự picks; evidence chỉ per-pick. Trace/execution giữ count, bỏ candidate_ids/source_hashes toàn pool.
- Checkpoint không trả scores; không lộ evidence envelope nội bộ chứa scores/interactive_basis.
- Proof, snapshots, dữ liệu DB và cache backend vẫn đầy đủ; không đổi CAS/owner/idempotency hoặc thuật toán chọn mẫu.
- Timeline ghi rõ điểm chỉ thuộc batch, mẫu không có điểm để trống; điểm rời rạc vẽ marker thay polyline một điểm vô hình.

## Bằng chứng

Regression worker tái hiện lỗi trước sửa (4 picks trả16 score rows). 63 focused backend/local/PostgreSQL tests pass;
50 frontend tests, tsc/build đạt; pure projection100.000 ứng viên + source hashes trả dưới2KB cho2 picks,
không mutate snapshot gốc. Đo chỉ đọc trên phiên được người dùng chỉ định ở mốc batch25:
2.810 score rows/263.987 bytes →16 rows/1.537 bytes (chỉ phần scores, không là kích thước HTTP cả response).

Compatibility cuối:16 tests PostgreSQL thật pass, gồm API với worker cũ/new worker, checkpoint success/error,
replay sau mất cache và YOLO/analysis thật. Test chức năng ML dùng fixture quota10s nên cold load16,7s CPU
làm WAITING_RESOURCE; cấp bonus120s chỉ trong fixture analysis (không đổi production/policy hoặc test quota), rerun16/16 pass.
Full backend regression:656 passed,24 skipped,1 failed trong1858,51s; lỗi duy nhất là YOLO fixture quota nêu trên.
Rerun sau sửa fixture:16/16 pass trên code cuối (có cả API compatibility old/new worker và projection source hashes).
Không ghi full suite là xanh hoàn toàn vì không chạy lại toàn bộ sau sửa fixture; không còn failure chưa xử lý trong lát này.
Hook Unicode lỗi sẵn được loại riêng theo AGENTS. Ruff và git diff --check đạt.

## Release và giới hạn

API có projection riêng để thu gọn cả response từ worker cũ. Ưu tiên release FE/API, không restart worker đang giữ
batch tạm của người dùng. Worker mới cũng có projection nhưng rollout worker riêng khi có thể checkpoint/drain an toàn.
Không claim hệ thống đã hỗ trợ dataset hàng triệu mẫu: scoring/context/internal snapshot, analysis views và pagination
là các giới hạn riêng. Review state vẫn chứa kept/dropped đã duyệt theo contract, không chứa mọi unit chưa duyệt.
Không dùng production DB để chạy test; các phép đo phiên thật chỉ đọc counts/bytes, không sửa decisions.

## Release live

FE/API source `9c10294a693ba5a08fe4b82266e3f4390d4e6374`, deploy `dep-db8s8v6i0phs738rbhq0` live;
image `docker.io/tonmeu/rav26@sha256:cc002e0cce47cfde94838b42dc5bd0551351faca50bc3948c389eda18c0da9cb`. Worker giữ image `sha256:3d2ea5e8eff7f52b6048fbe2b1aa202803739bd4f07aa0f7db49b132fde2c1d9` để không mất batch tạm.
API projection đã smoke với worker cũ; projection trong code worker mới chưa rollout, traffic backend/proof còn như cũ.
Không migration/env/policy changes; health/ready/login/app200, worker healthy và image unchanged.
Rollback FE/API: `sha256:26f3cb93efcb6d0dc35d36022599d626a9f0a2b6f70d256692ca1a2e572016da`.

QA live16 picks: response đầu 28382 bytes, lượt kèm checkpoint 36722 bytes
(JSON sau giải nén, gồm URLs ảnh/metadata). Cả hai chỉ16 score rows; checkpoint scores=null.
Giữ/Bỏ không lặp, retry cùng request_id trả cùng kết quả, đọc lại batch trả16 score rows.
DB QA giữ240 score rows nguyên vẹn, public chỉ16. Không POST vào phiên người dùng; đo phiên đó trước release chỉ đọc counts/bytes.
Artifacts gitignored: output/batch-response-release; output/response-size-audit lưu regression/measurement logs.
Không push Git.
