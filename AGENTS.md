# AGENTS.md — luật làm việc cho người và AI agent (RAV-26)

Đọc trước khi làm gì: file này → `PLAN.md` (lát cắt đang làm) → `docs/IMPLEMENT_GUIDE_V2.md` → các mục CONTRACTS của lát.

## 1. Nguồn sự thật

Thứ bậc (sản phẩm → thiết kế → dẫn xuất) ghi ở **một chỗ**: [docs/README.md](docs/README.md). Tóm tắt: code theo
`docs/design/CONTRACTS.md` + `docs/architecture_diagram.md`; `docs/components/*`, `PLAN.md`, guide và code đều theo hai file đó.

## 2. Luật (bắt buộc)

1. **Docs trước, code sau.** Muốn code khác docs cấp 2 → sửa docs trước (cùng commit), và **hỏi người dùng nếu thay đổi thiết kế**.
2. **Không tự cắt/đổi phạm vi contract.** Muốn làm ít hơn contract → hỏi, rồi ghi quyết định vào log quyết định
   và đánh dấu "Hướng tới" trong CONTRACTS. "Implement theo kiến trúc" nghĩa là theo CONTRACTS + architecture, không phải một luồng demo.
3. **Người dùng hỏi thì trả lời**, không tự chọn đáp án rồi làm tiếp.
4. **Không viết docs theo code lệch.** `docs/components/*` chỉ trích dẫn CONTRACTS, không định nghĩa lại signature,
   không có câu kiểu "khi lệch thì code theo file này".
5. **Một phiên = một lát cắt** trong `PLAN.md`. Xong lát: test xanh → commit → tick `PLAN.md` → dừng, báo người dùng.
6. Commit message tiếng Việt không dấu (vd `feat: them registry component`). **Không push nếu người dùng chưa bảo.**
7. Quyết định mới → thêm vào `docs/research-pack/06_project_state/01_confirmed_decisions.md`.
   Giả định chỉ đúng khi chạy local → thêm dòng vào `docs/design/LOCAL_SINGLE_USER_LIMITS.md` + comment `# local-only:` trong code.

Tự kiểm trước khi báo xong (guide §6): thay đổi thiết kế đã nằm trong CONTRACTS/architecture trước code? Có chỗ nào làm ít hơn
contract mà chưa hỏi / chưa ghi "Hướng tới"? Test, ruff (và tsc nếu đụng web) xanh? Chưa push?

## 3. Môi trường (Windows)

- Repo nằm ở đường dẫn **có dấu** (`Vin AI Thực Chiến`): `cv2.imread` / `cv2.imwrite` không đọc/ghi được →
  đọc ảnh bằng `PIL.Image.open`, ghi bằng `cv2.imencode(...)` + `Path.write_bytes`.
- `tests/test_codex_hook.py` fail sẵn vì PowerShell `Set-Location` với đường dẫn có dấu — không phải lỗi mới.
- Python: `python` (3.12, torch CPU). DINOv2-S có trong cache `torch.hub`:
  `torch.hub.load(<hub_dir>/facebookresearch_dinov2_main, "dinov2_vits14", source="local")`.
- Máy RAM thấp: đóng bớt app khi tính embedding.
- Dữ liệu (gitignore): `data/youtube5m/6WTF1JV5knI.mp4` (5 phút, benchmark), `data/youtube/…` (30 s, mock),
  `data/videos/commaai-sample.mp4` (21 s). Mọi dữ liệu, cache, output nằm trong `data/` hoặc `output/`.
- Trước lệnh Python/npm/test trong mỗi PowerShell: `. .\scripts\use_project_storage.ps1` để cache/temp ở ổ repo (máy này là D).
  Pytest thêm `--basetemp=output/pytest-tmp`; không tạo cache/model/output mới trên C. Pagefile Windows là phần riêng, không tự đổi cấu hình.

## 4. Lệnh

```bash
python -m pytest tests -q
# test pilot (RLS/transaction) cần Postgres thật: đặt RAV_PILOT_TEST_DATABASE_URL — xem docs/setup/PILOT_RUNTIME_RUNBOOK.md §6
python -m ruff check src tests
cd src/web && npx tsc --noEmit          # nếu đụng frontend
python -m uvicorn src.rav.api:app       # backend
python -m src.rav.cli ...               # prepare / benchmark
```

## 5. Quy ước chung

- Kiến trúc tối giản: không thêm pattern/abstraction ngoài những gì CONTRACTS đã có.
- Cài thêm thư viện → cập nhật `requirements.txt`.
- Không hardcode đường dẫn tuyệt đối; dùng đường dẫn tương đối hoặc biến môi trường (`.env`).
- Module mới có docstring đầu file: làm gì, input/output, cách chạy (nếu chạy được).
