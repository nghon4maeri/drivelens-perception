# Strategy orchestration #141 — QA, 2026-10-16

Contract: [§21.32](../design/CONTRACTS.md). Candidate local; không deploy, không push, không migration.

## Phạm vi đã kiểm

- Registry Strategy độc lập; mọi recipe cũ chạy qua `LegacyStrategy`, strategy mới `context_uncertainty`
  (CLIP lọc → DINO giới hạn ứng viên → YOLO gốc/lật chỉ trên ứng viên → chọn batch).
- Local job và pilot worker dispatch theo registry; params/version/model đóng băng trong snapshot job; trace lưu cùng proposal.
- Sidebar chỉ hiện title/steps/inputs/public params; config JSON chỉ đọc trong chi tiết kỹ thuật.

## Sửa trong QA

- Local DB bỏ mất `scores` và `strategy_trace` của native strategy khi lưu → mở lại batch không còn điểm/trace.
  Lưu cùng snapshot như `_evidence` (`store/db.py`).
- Worker pilot bỏ mất try/except quanh `update_progress`; field khác bắt đầu lại `done=0` sẽ làm 422 và fail job.
  Khôi phục hành vi trước #141; test handler giờ cho runtime ném lỗi ở mọi update và vẫn phải xong (fail nếu bỏ fix).
- `test_pilot_api_v2::test_proposal_routes` hard-code danh sách recipe từ trước #140 → so với `PILOT_RECIPES`.

## Test

- Regression adapter: với random/uniform/diversity/text_diversity/perception_instability, `LegacyStrategy.run`
  cho cùng picks/combined/rank/config_hash như `propose()` cũ (video thật, YOLO và CLIP text mock).
- Native local job: config bị từ chối, params/model đóng băng, YOLO chỉ chạy 2× số ứng viên đã giới hạn, picks ⊂ ứng viên.
- Full backend với PostgreSQL 16 thật: **643 passed, 26 skipped** (opt-in/Linux-only/thiếu `yolo11n.pt`),
  `test_codex_hook.py` loại riêng (fail sẵn do đường dẫn có dấu). Ruff pass. Catalog sinh lại trùng file commit.
- Frontend: tsc pass, Vitest 48 pass. Bỏ test registry giả, gộp hai test restore trùng, thay assertion chuỗi chữ dễ vỡ.

## Browser acceptance local (2026-10-16)

Chromium headless (Playwright trong `data/tools`), app local cô lập `output/qa141`, `commaai-sample.mp4` (21 s, 86 frame),
DINO + CLIP prepare thật, YOLO11n COCO đã verify. Giữ 2 / Bỏ 1 frame qua API (tính năng có sẵn), còn lại thao tác trên UI.

- Sidebar: title, 4 bước, đầu vào, chọn model, câu tìm/loại trừ, phạm vi ứng viên; đợt đang xem tách khỏi cấu hình đợt sau.
- **Lỗi 1 (đã sửa):** đợt đầu chỉ ra 3/16 frame — bước giới hạn ứng viên giữ 5/48 vì `greedy_nms relax=False` trên frame dashcam
  gần trùng (DINO < 0.1). Đổi sang relax mặc định như candidate stage cũ (mask giữ nguyên). Sau sửa: 83 qua CLIP → 48 ứng viên
  → YOLO 48 → 16 frame, 18 s, `relaxed=true`. Test tái hiện (1 → 12 ứng viên) fail trước sửa.
- **Lỗi 2 (đã sửa):** `/explain` của đợt native trả 422 (thiếu `similarity_field`, `field_used_by_recipe`). Test qua API fail trước sửa.
- Không frame Giữ/Bỏ nào bị chọn lại; picks ⊂ ứng viên; reload khôi phục strategy/model/multiplier của đợt đang xem; console sạch.
- UI nhỏ: chữ "không có tham số" dính "Số ảnh mỗi đợt" → block.

## Rà soát độc lập sau Claude (2026-10-16)

Base `94dd6335`, giữ nguyên code/commit của Claude; không deploy/push trong lượt review.
Các lỗi được tái hiện bằng test trước khi sửa:

- Native `context_uncertainty` có config=null làm API pilot TypeError khi cố gắn features recipe cũ.
  Chỉ bind legacy config nếu có; thêm regression PostgreSQL thật API → queue → worker → publish → cache,
  thay vì chỉ kiểm native qua local API. Cả perception cũ và native cùng đạt.
- API validator chỉ nhận array chuỗi/integer và metadata buộc mọi parameter có default, chặn custom Strategy.
  Dùng JSON Schema validator nhẹ (`jsonschema`, khai báo ở cả requirements/API/worker), hỗ trợ required/string/number/bool/array/nested;
  giữ giới hạn bytes, finite JSON và query không rỗng. Worker tiếp tục validate bằng Params Pydantic.
- Native identity phụ thuộc thứ tự set và `str(FieldKey)` bỏ producer/params_hash. Hash các FieldKey đầy đủ đã sort;
  regression đảo thứ tự không đổi hash, đổi params_hash phải đổi identity.
- Concurrent first requests có thể đăng ký trùng strategy và làm UI internal_error. Discovery được khóa trong process;
  regression 8 request khởi tạo đồng thời tái hiện duplicate trước sửa và pass sau sửa.

Kiểm chứng riêng: 54 core/strategy/perception/algorithm tests, 31 PostgreSQL curation/interactive/API tests,
48 frontend tests, TypeScript/build và Ruff đạt; catalog sinh lại khớp registry. Hook lỗi sẵn loại riêng khi full suite.
Full suite với PostgreSQL thật: 650 passed, 26 skipped, 1 failed trong 606.58 s. Lỗi duy nhất là fixture
`test_loader_rejects_wrong_task_or_version_before_images` dùng module Ultralytics giả không có utils, trong khi harness
ép RAV_MODEL_THREADS=1. Nhóm đó pass 11/11 khi bỏ biến ép; đã cô lập biến môi trường trong chính test validation,
không đổi thread setting sản phẩm. Rerun cuối YOLO + Strategy với RAV_MODEL_THREADS=1: **23 passed**, Ruff đạt.
Không ghi full suite là 651 passed vì không chạy lại toàn bộ sau sửa fixture; không còn failure chưa xử lý trong lần chạy này.

Chrome qua công cụ browser được phép, local API riêng cổng8151 và Vite3151, bản sao database/fields QA trong
`output/strategy141/review-browser`, không sửa dữ liệu Claude hoặc production. Mở lại batch3 khôi phục model/multiplier;
Giữ 1/Bỏ 1 qua UI rồi Đợt mới chạy model thật thành batch4: 48 ứng viên → 16 picks, loại đủ 3 kept/2 dropped.
Điểm/trace/snapshot và explain200 được đọc lại qua API; đổi next strategy sang CLIP không đổi strategy của batch4.
Lần reload UI cuối bị extension Chrome chặn automation; không claim đã quan sát bước reload đó hoàn tất.
Các server test do lượt review tạo đã dừng, không dừng server của Claude.

## Chưa làm / giới hạn

### Hotfix chuyển sang Diversity (2026-10-16)

Tái hiện bằng regression: `recipeSelFor` giữ model đã chọn ở strategy trước; `proposalRequest`
gửi model đó cả khi strategy mới không cần, rồi hook gửi tiếp vào `InteractiveIn` không có trường `model_id`.
Backend từ chối đúng `extra_forbidden` (422). Sửa request theo capability `inputs.model`/`requires_model`,
và bỏ trường model khỏi body Interactive; không nới DTO backend hoặc đổi dữ liệu/snapshot cũ.
Test chuyển model strategy → Diversity/Uniform/Random/text_diversity fail trước sửa và pass sau sửa.
49 frontend tests, tsc/build và Ruff đạt; không sửa backend. FE/API source56ca91ff đã deploy;
[release QA](STRATEGY_RELEASE_QA.md) ghi rõ kiểm API live, chưa browser acceptance đã đăng nhập ở lượt hotfix.

- Màn Phiên mới chỉ liệt kê recipe cũ (`RECIPE_LABELS`); strategy native chọn ở sidebar Studio. Tên/bước strategy hiển thị tiếng Anh.
- Đã deploy FE/API và worker #141: [release QA](STRATEGY_RELEASE_QA.md). Native cloud acceptance còn thiếu CLIP ở nguồn QA.
- Chưa benchmark chất lượng/uplift của `context_uncertainty`; không claim bất định lớn nhất toàn video.
