# Backend S3/Kaggle/Supabase (C1)

Thiết kế: CONTRACTS §19. Frontend chỉ gọi FastAPI; không dùng AWS/Supabase secret hoặc VITE_* cho credential.

## Cấu hình backend ở `.env` gốc repo

```dotenv
RAV_STORAGE_BACKEND=s3
RAV_S3_BUCKET=ten-bucket
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
RAV_JOB_BACKEND=supabase
SUPABASE_URL=https://project-ref.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
RAV_CLOUD_JOB_TIMEOUT_S=7200
KAGGLE_API_TOKEN=...
```

Supabase Project Settings → API Keys → Secret keys, dùng key backend mới. Chạy
[SUPABASE_EMBEDDING_JOBS.sql](../../../../setup/SUPABASE_EMBEDDING_JOBS.sql) trong SQL Editor một lần. Không cấp policy anon/authenticated.
Backend dùng HTTPS REST/RPC (443), không cần database password/JWKS/publishable key cho luồng này.
Secret key không cho chạy DDL qua REST: SQL setup là bước quản trị riêng, không giả lập tự tạo bảng bằng API key.

## Chạy và kiểm

```powershell
. .\scripts\use_project_storage.ps1
# Package mới lưu D, helper thêm data/python-packages vào PYTHONPATH:
python -m pip install --target data/python-packages 'boto3>=1.35.0,<2.0'
python -m uvicorn src.rav.api:app --host 127.0.0.1 --port 8000
```

GET `/api/v1/cloud/status` xác nhận s3_ready/jobs_ready. Upload video/model và import dataset qua API cũ;
raw lưu theo SHA lên S3 trước thành công, bản local trên D phục vụ catalog/decoder/media.
Ở Kho video, bấm Chuẩn bị CLIP. VideoDTO.clip_prepare giữ job_id, state/stage/done/total/error,
result_key chỉ khi embedding đã kiểm và cache local xuất bản. Không trả signed URL/secret.

```powershell
. .\scripts\use_project_storage.ps1
python scripts/smoke_cloud_embedding.py --video data/videos/commaai-sample.mp4 --job-backend supabase
```

Smoke có output riêng, không ghi đè embedding/phiên chính. `--job-backend local` là lựa chọn explicit để
kiểm S3/GPU khi chưa setup Supabase; không phải fallback lúc Supabase lỗi.

## Lưu và phục hồi

- S3 videos/datasets/models: raw lâu dài, không xoá khi job xong. results/clip/{job_id}: NPZ + progress JSON.
- Kaggle: GET raw bằng URL có hạn, SHA/pixel/runtime strict, CLIP CUDA pinned, PUT result/progress scoped,
  dọn raw/config/result local trước terminal progress. AWS/Supabase key không rời backend.
- API offline: worker GPU tiếp tục tới deadline URL/job. API mới claim lease, attach đúng session,
  kiểm S3 result và xuất bản FieldStore. Cleanup session lỗi giữ active job để phục hồi.
- GPU mất hoặc deadline/URL hết hạn: retry explicit, không claim resume vector từng batch. Raw/result S3 không tự bị xoá.
- Lát này chưa migrate SQLite phiên/feedback/catalog và chưa xoá working copy D; chưa đủ để deploy nhiều tenant.
- Bucket dùng SSE-S3 + private, app IAM giới hạn bucket/prefix; temp lifecycle chỉ prefix temp/.
