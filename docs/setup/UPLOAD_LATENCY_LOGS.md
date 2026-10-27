# Đo latency upload pilot (#111)

JSON `event=rav_timing` luôn bật trên bản API/worker có #111. Dùng `upload_id`
từ Console để nối `api.upload.created/completed` và `worker.claimed`; `job_id` và
`attempt_id` nối các span worker. `request_id` nối API request, response và DB;
response có `X-Request-ID`. Không log filename, URL, S3 key, SQL/params hoặc secret.

1. Reload app để tải FE mới; mở Console và lọc `rav_timing`, chọn Preserve log.
2. Tải video. Browser ghi `browser.create`, `browser.s3.transfer` (bytes/s),
   `browser.complete`, `browser.ready_wait`, `browser.upload_total`.
   `browser.media_ready_wait` đo từ xin media đến receipt READY (gồm chờ slot/poll),
   chưa bao gồm tải/hiển thị JPEG/mp4. Console không tự gửi về server.
3. Render service Logs: lọc `rav_timing` hoặc upload/job/request UUID.
   `api.upload.complete` gồm HEAD, enqueue và DB; `api.s3.head` tách HEAD.
4. Trên Contabo trong `/opt/rav26-worker`:

   ```bash
   docker compose --env-file release.env -f compose.worker.yml logs --since 15m worker
   ```

   Lọc JSON theo upload/job/attempt, lưu ngay evidence khi đo: Compose giữ tối đa
   3 file × 10 MB, không phải kho lịch sử dài hạn. Không in env/config chứa secret.

DB: `db.checkout` chỉ chờ pool/kết nối; `db.execute` mỗi execute, không gồm fetch
hoặc BEGIN/COMMIT tự chạy bởi Psycopg. `db.transaction` bao gồm checkout, BEGIN,
execute/fetch và COMMIT/ROLLBACK. Worker: `worker.attempt` từ sau claim tới khi
kết thúc dispatcher; `worker.claimed.queue_ms` = attempt.started_at − job.created_at
(retry gồm thời gian từ lúc tạo job). Claim transaction không nằm trong attempt.
`worker.run_handler`/`worker.handler` bao gồm chặng xử lý, `worker.publish` tách
ghi kết quả và quota. Download+SHA streaming cùng span; `worker.probe_core_hash`
gồm hash core lần nữa và probe. `worker.s3.canonical_put_head` không xuất hiện
khi `worker.dedup.duplicate=true`; không so dedup với upload nguồn mới như cùng việc.

`wall_ms` dùng đồng hồ monotonic, `at` UTC để đối chiếu. Span lồng nhau không
cộng dồn. `process_cpu_ms` gồm các thread của process, chỉ chẩn đoán và không
thay receipt tính quota. API outcome ok nghĩa là trả được response; HTTP lỗi
đọc ở `api.response.status`. Lỗi span chỉ ghi loại exception, không nội dung.

Muốn benchmark: cùng máy/mạng/file, 3 lần, ghi cold/warm, source mới/dedup,
queue và CPU receipt. S3 transfer chậm gợi ý mạng client→S3; download/PUT chậm
gợi ý worker↔S3/I/O; DB execute chậm chưa phân biệt RTT, khóa hoặc server nếu
chưa có dữ liệu server bổ sung. Không kết luận mạng nhà từ wall−CPU.
