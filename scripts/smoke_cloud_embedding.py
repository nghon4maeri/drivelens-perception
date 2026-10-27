"""Run a real backend upload → S3 → Kaggle → S3/cache smoke with isolated local output.

Run from repo root after storage shell setup:
python scripts/smoke_cloud_embedding.py --video data/videos/commaai-sample.mp4 --job-backend supabase
Use --job-backend local explicitly only for S3/GPU smoke before Supabase SQL/keys are ready.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.rav.service import App, cloud_embedding, video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,default=Path('output/cloud-smoke'))
    parser.add_argument('--job-backend',choices=('local','supabase'),default='supabase')
    args = parser.parse_args()
    load_dotenv('.env')
    app = App(args.output_dir/'videos',args.output_dir,1<<40,storage_backend='s3',job_backend=args.job_backend)
    try:
        uploaded = video.upload_video(app,args.video,args.video.name)
        cloud_embedding.queue(app,uploaded.id)
        previous = None
        deadline = time.monotonic()+9000
        while time.monotonic() < deadline:
            state = cloud_embedding.get_status(app,uploaded.id)
            summary = {k:state.get(k) for k in ('job_id','state','stage','done','total','error','result_key')}
            if summary != previous:
                print(json.dumps(summary,ensure_ascii=False),flush=True)
                previous = summary
            if state['state'] in ('done','error'):
                return 0 if state['state']=='done' else 1
            time.sleep(3)
        print('Smoke timeout; durable job giữ nguyên, kiểm qua backend recovery.',flush=True)
        return 1
    except Exception as exc:
        from src.rav.store.embedding_jobs import JobStoreError
        from src.rav.store.s3 import CloudStorageError
        message = str(exc) if isinstance(exc,(CloudStorageError,JobStoreError)) else 'Cloud smoke lỗi; xem trạng thái job, không in lỗi chứa credential.'
        print(message,flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
