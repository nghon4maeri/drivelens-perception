# Kiểm độ mượt DEP7-Latency — 2026-10-12

Lát #116 theo CONTRACTS §21.3 và UI_DESIGN Media State. Admin `duyla25clc` giữ quyền
Vận hành chỉ đọc toàn pilot; quyền này không cấp video private của tài khoản khác.

## Baseline auth UI

Phiên kiểm thử #114 `9af53a87-0f69-4e00-a11d-0388ee542234`, nguồn 0909, proposal 1 có 16 picks.
Không chạy lại DINO/CLIP/YOLO trong lượt này; YOLO đọc lại evidence 16 ảnh/6 đối tượng đã lưu.

| Luồng | Quan sát |
|---|---|
| Thumbnail cache, lấy URL | Console media wait 6–9 giây; 16 ảnh cuối cùng tải được |
| Context đầu tiên | Browser media wait 49,334 s; file mp4 6 s, readyState 4, không lỗi playback |
| Ảnh gốc đầu tiên | Browser media wait 35,933 s; JPEG rộng 1728 pixel đã tải |
| Giữ rồi hủy giữ | Trạng thái pending → đã lưu; hủy giữ 1,081 s, trở lại 0/50 |
| Phân tích | METRICS SUCCEEDED; giữ 0/50, kết quả khớp trạng thái hiện tại |

Undo khôi phục trạng thái kept, lịch sử append-only vẫn ghi hai thao tác kiểm thử.
Các số này là một lượt QA, chưa là p95 hoặc benchmark 80 MB. Review-time trong bảng phân tích
là thời gian người dùng duyệt, không phải thời gian xử lý hệ thống.

Trace worker trước thay đổi: context handler 12,716 s, publish 16,846 s, attempt 38,460 s;
ảnh gốc handler 6,405 s, publish 16,443 s, attempt 27,919 s. METRICS publish 12,222 s,
attempt 33,726 s. Timestamp updated_at dùng now() trong transaction nên không đại diện lúc
commit; ưu tiên span monotonic cho thời gian publication. Spans lồng nhau không cộng trực tiếp.

## Thay đổi

- Viewer hiện rõ chờ job/tải clip/lỗi thay cho vùng đen; khóa tua/phát khi video chưa sẵn sàng.
  Play/pause phản ánh sự kiện video thực, seek chờ metadata. Không đổi media auth, polling hoặc quota.
- Pipeline chỉ trong nhóm ghi quota consume/refund, storage settle và attempt close;
  thứ tự lệnh và transaction cũ được giữ. Lỗi deferred ở sync phải rollback trước caller chạy tiếp.
  Không bao toàn transaction/savepoint, không tự retry ghi hoặc đổi schema/quyền.
- Trace `db.enqueue` là thời gian đưa SQL vào pipeline; `db.pipeline` gồm sync,
  `db.execute` ngoài pipeline vẫn đo execute đồng bộ. Không log SQL/params/credential.

Probe chỉ đọc từ Contabo: 3 lượt × 20 SELECT 1, libpq 18.0.6; sequential median 5.207,6 ms,
pipeline median 259,3 ms. Đo này chứng minh overhead chờ mạng và hỗ trợ protocol; không chứng minh
job tăng tốc 20 lần. Tham khảo [Psycopg pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html).

## Kiểm và release

Regression backend PostgreSQL thật: **434 pass, 3 skip**; bỏ test hook lỗi sẵn theo AGENTS.
Hai test mới gây lỗi DB ở giữa nhóm ghi quota/storage, kiểm rollback toàn nhóm, pool reuse và replay
không tính hai lần. Frontend **24 pass**, tsc/ruff/diff-check đạt; image build chạy lại test/tsc/build.
Không thêm dependency/migration hoặc push Git. PG local đã dừng/gỡ alias sau kiểm.

Worker pin `sha256:350d40c7d4810a3849e69f136857820e2698aef903215ff27c7fb4a411d9170c`, healthy;
rollout khi không có active job, giữ env/volume/quota. Rollback worker dùng
`output/release-pre-pipeline.env` trên VPS, image #115 `746af4861ca1…`.

Smoke auth UI sau pipeline:

| Luồng | Sau release | Baseline tham khảo |
|---|---|---|
| Context chưa có cache | 41,533 s browser wait | 49,334 s |
| Context worker publish | 14,918 s | 16,846 s |
| Upload video tổng hợp | 32,340 s tổng; S3 1,095 s; READY wait 24,985 s | 37,992 s tổng; S3 1,121 s; READY wait 30,982 s |
| Upload worker publish | 13,164 s | 16,408 s |

Context sau là pick #2, trước là pick #1. Video tổng hợp sau 1395 byte, trước 1556 byte.
Đây là hai mẫu smoke khác nhau, không là A/B kiểm soát hoặc p95; không quy toàn bộ mức giảm cho pipeline.
Thumbnail cache khi 16 yêu cầu đồng thời vẫn 5–10,6 s; thumbnail mới của video tổng hợp mất 36,070 s.
**Chức năng chạy được nhưng chưa đủ mượt**, đặc biệt media lần đầu; không claim tốc độ video 80 MB.

Job context `6b4bdc52-9415-4bfa-8ab1-f48793b9c1a3`, upload validation
`5d2d9ef3-dbca-4e15-a79b-da3417a92dc2`: SUCCEEDED, attempt CLOSED, usage FINAL, cleanup true,
GPU 0; CPU/MEDIA/GPU slots FREE sau tất toán. Upload `9c75359e-0ea3-4494-b021-dd73ed286c9c`
READY, source `fe9f5baa-b9ea-40e3-8c31-f2d6adb2105d`; video tổng hợp giữ trong tài khoản test.

FE/API pipeline đầu `af44eb689084…`, Render `dep-db5sul942hec73a3us2g` Live 20,5 s.
Image cuối thêm resume khi ended: `tonmeu/rav26:combined-pipeline-loop-20261012`, digest
`sha256:724119b1b640968030bcac5337743904e2fd06095a039a6a5679b9543214cbd5`,
Render `dep-db5t295ckfvc73bbsap0` Live 20,3 s, readiness DB/schema 200, worker healthy.
Trang Render từng giữ status cũ In progress; reload đã xác minh trạng thái Live, không coi
duration hiển thị stale là thời gian deploy thật.

Browser image cuối: clip 6 s vẫn playing/readyState 4/error null sau nhiều vòng; dừng trả paused,
Về t0 trả currentTime 3 s đúng anchor. Mở lại cùng clip khi cache URL còn hạn tới khi nút Dừng hiện
mất **1264 ms**, không sinh job mới. Reload trang rồi mở clip cùng 16 thumbnail đồng thời vẫn
chờ cấp URL 8739 ms; cache artifact trên server không bảo đảm mọi lần mở đều nhanh.
Proof `viewer-pending.png`, `viewer-ready.png`, `browser-after.json`, các span worker và receipt
trong `output/latency-release`. Lỗi resolution được test bằng mock frontend; không cố làm hỏng media cloud.
Rollback FE/API về Ops `sha256:110f5cde633178fcf656d0f29230371509c63fc2b96d980012aa5cd84482ebf2`.

Ưu tiên tiếp: giảm các round trip đọc/cấp URL, đo decode riêng và preload media có giới hạn;
đổi DB region/RPC/policy cần duyệt thiết kế riêng. Không tick toàn DEP7/DEP8;
export/PII, CI, DR toàn hệ thống và gate multi-account còn riêng.
Evidence có ảnh video private chỉ lưu ignored trong `output/latency-release`, không commit.
