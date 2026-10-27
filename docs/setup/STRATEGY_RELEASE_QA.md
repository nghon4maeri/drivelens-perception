# Release Strategy #141 — 2026-10-16

Theo yêu cầu deploy của người dùng. Source `ac914d59f69ca25de5b60b0810cc0e6708e3ab98`; không push Git, không migration.

- Render deploy `dep-db8of97f3r2c73era36g`, status live.
- FE/API image `docker.io/tonmeu/rav26@sha256:6837eb9e01c3e2ccb871d2214b776916cf84b6d6fc4cd651f4a7c7d0a140d7fa`.
- Worker image `sha256:3d2ea5e8eff7f52b6048fbe2b1aa202803739bd4f07aa0f7db49b132fde2c1d9` (cùng source).
- Rollback worker: `/opt/rav26-worker/output/pre-strategy-20261016-ac914d59/` giữ release.env/worker.env/compose.worker.yml.
- Rollback FE/API: `docker.io/tonmeu/rav26@sha256:651b91c66511f7e9efe0394a56caf7f105fce4714ba35872f91063e8a85d40e3`.
- Rollback worker DEP6: `sha256:218c7a3037e5eea44577e620c5deba10edcd2c2d5b151cb7c0532dea189d5129`.

## Kiểm chứng

Trước switch: không job/upload hoạt động, slots FREE. Không đổi env API/worker, compose, schema, policy hay model PII.
Build Docker Linux: tsc, 48 frontend tests, production build đạt. Candidate health/routes/registry đạt.
Model PII mặt/biển số ghim SHA load ready trong candidate và live; worker pip check sạch.
Sau switch: worker healthy, Render live; /health, /health/ready, /, /login, /app đều200; API env và policy không đổi.
Browser in-app mở/reload trang đăng nhập live bình thường; chưa xác minh Studio đã đăng nhập trong lượt deploy này.

Tài khoản QA riêng, dashcam60s/YOLO11n thật: perception batch1 30.8s,
batch2 sau feedback 43.75s, mỗi batch4 picks. Giữ/Bỏ được persist và loại khỏi batch kế;
chuyển sang Interactive Random có số đợt kế tiếp; upload VPS chunk/abort đạt. Không sửa decisions người dùng.

## Gate còn mở

Smoke native `context_uncertainty` trên cloud dừng đúng HTTP409 `feature_not_ready`: nguồn QA có DINO nhưng chưa có CLIP.
Không enqueue Kaggle hoặc đổi policy để vượt gate. Vì vậy chưa claim native end-to-end cloud đạt;
local native model thật đã đạt ở [Strategy QA](STRATEGY_ORCHESTRATION_QA.md).
Cần nguồn đủ DINO+CLIP để tiếp tục acceptance native trên live. Chưa benchmark chất lượng/uplift/capacity,
chưa smoke export thật ở lượt này; PII quality/browser Export vẫn là gate riêng.

Artifacts vận hành (gitignored): `output/strategy-release/`, protected service/env backups, release.json,
verified.json, compat-smoke.json, build-checks.json, worker-checks.json. Không đưa credentials vào docs.

## Hotfix FE/API: chuyển khỏi strategy dùng model (2026-10-16)

Source `56ca91ff4c49b2a6962f4c7602cee4514a45da36`, Render `dep-db8r8gnf3r2c73f4rj40`, image `docker.io/tonmeu/rav26@sha256:986a9c595e897c434645e7b964204d4b59114306e7ee1deb7e1e46ff77c8decb`.
Worker vẫn `sha256:3d2ea5e8eff7f52b6048fbe2b1aa202803739bd4f07aa0f7db49b132fde2c1d9` từ source ac914d59; không migration/env/policy change.
Rollback FE/API là image6837eb9e… của release trên, protected config tại output/strategy-switch-release.
Lỗi cũ: model_id còn trong request Diversity → InteractiveIn extra_forbidden422. Chỉ gửi model khi strategy cần;
body Interactive không gửi trường này. Test tái hiện fail trước sửa, 49 frontend tests/tsc/build/Ruff đạt sau sửa.
Cloud QA API từ batch perception đã persist → Diversity4 picks (0.85s),
Uniform4 picks (1.53s), Random4 picks (1.23s).
Kept/dropped bị loại khỏi picks, decisions và input/evidence batch cũ không đổi; mọi health/ready/page route200.
Đây là smoke API QA4 ảnh, không là benchmark16 ảnh hoặc browser acceptance đã đăng nhập.
Artifacts gitignored tại output/strategy-switch-release: release.json, verified.json, switch-smoke.json.
