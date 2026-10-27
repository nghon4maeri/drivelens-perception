"""Leased durable DINO/CLIP jobs: queue/recover, publish validated cache and expose video progress.

Backend owns credentials. Kaggle progress/result travels through S3; Supabase stores durable status.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict

from ..components.features.clip_b32 import ClipB32
from ..components.features.dinov2_s import DinoV2S
from ..pipeline import prepare as p1
from ..pipeline.remote_embed import EmbeddingValidationError, KaggleJobError
from ..pipeline.remote_s3_embed import WorkerDetachedError, compute, stop_owned_session
from ..store.embedding_jobs import JobStoreError, now
from ..store.s3 import CloudStorageError
from . import ServiceError
from .video import get_video

log = logging.getLogger(__name__)


def family(row):
    value = row['payload'].get('feature','clip')
    if value not in ('clip','dino'):
        raise KaggleJobError('Loại feature của job không được hỗ trợ.')
    return value


def job_map(app, feature):
    return app.prepare_jobs if feature == 'dino' else app.clip_prepare_jobs


def status(row):
    payload = row['payload']
    progress = payload.get('remote_progress',{})
    return {'state':row['state'],'stage':payload.get('stage','CLIP đang chờ'),
            'done':progress.get('done',0),'total':progress.get('total',payload.get('total',0)),
            'started_at':payload.get('started_at'),'error':payload.get('error'),
            'job_id':row['id'],'result_key':payload.get('result_key') if row['state']=='done' else None}


def get_status(app, video_id, *, feature='clip'):
    jobs = job_map(app,feature)
    if video_id in jobs:
        return jobs[video_id]
    rows = app.embedding_jobs.list(video_id=video_id)
    for kind in ('dino','clip'):
        matching = [row for row in rows if family(row) == kind]
        job_map(app,kind).setdefault(video_id,status(matching[-1]) if matching else None)
    return jobs.get(video_id)


def queue(app, video_id, *, feature='clip'):
    from .clip_prepare import prepared

    video = get_video(app,video_id)
    if feature not in ('dino','clip'):
        raise KaggleJobError('Feature không được hỗ trợ.')
    ready = p1.is_prepared(video,app.spec,app.fields) if feature == 'dino' else prepared(app,video)
    if ready:
        return
    ctx = p1.make_context([video],app.spec,app.fields)
    encoder = DinoV2S() if feature == 'dino' else ClipB32()
    input_key = hashlib.sha256(json.dumps([video_id,asdict(encoder.key),ctx._execution.spec_hash,
                                           [u.id for u in ctx.units]],sort_keys=True).encode()).hexdigest()
    row = {'id':uuid.uuid4().hex,'video_id':video_id,'input_key':input_key,'state':'queued',
           'created_at':now(),'updated_at':now(),
           'payload':{'feature':feature,'total':len(ctx.units),'stage':feature.upper()+' chờ Kaggle','error':None}}
    row = app.embedding_jobs.create(row)
    job_map(app,feature)[video_id] = status(row)
    schedule(app,row)


def schedule(app,row):
    with app.clip_job_lock:
        if row['id'] in app.cloud_scheduled:
            return
        app.cloud_scheduled.add(row['id'])
        app.prepare_queue.put(lambda: run(app,row['id']))


class Lease:
    def __init__(self, store, job_id, owner):
        self.store,self.job_id,self.owner = store,job_id,owner
        self.lost,self.stop = threading.Event(),threading.Event()

    def __enter__(self):
        def heartbeat():
            while not self.stop.wait(30):
                try:
                    if not self.store.claim(self.job_id,self.owner):
                        self.lost.set()
                        return
                except Exception:
                    self.lost.set()
                    return
        self.thread = threading.Thread(target=heartbeat,daemon=True,name='cloud-job-lease')
        self.thread.start()
        return self

    def __exit__(self,*args):
        self.stop.set()
        self.thread.join(timeout=1)


def run(app, job_id):
    store, owner = app.embedding_jobs, str(uuid.uuid4())
    row = None
    try:
        row = store.claim(job_id,owner)
        if not row:
            return
        with Lease(store,job_id,owner) as lease:
            def persist():
                if lease.lost.is_set():
                    raise WorkerDetachedError('Lease đã đổi; nhường job cho worker mới.')
                saved = store.update(row,owner)
                row.update({k:v for k,v in saved.items() if k != 'payload'})
                job_map(app,family(row))[row['video_id']] = status(row)

            row['state'] = 'running'
            row['payload'].setdefault('started_at',now())
            persist()
            if row['payload'].get('stop_pending') and row['payload'].get('terminal_error'):
                stop_owned_session(row,persist)
                raise KaggleJobError(row['payload']['terminal_error'])
            video = get_video(app,row['video_id'])
            ctx = p1.make_context([video],app.spec,app.fields)
            encoder = DinoV2S() if family(row) == 'dino' else ClipB32()
            result = compute(encoder,ctx.units,ctx,storage=app.cloud_storage,job=row,persist=persist,
                            output_dir=app.output_dir,timeout=float(os.environ.get('RAV_CLOUD_JOB_TIMEOUT_S','7200')),
                            should_detach=lambda: app.cloud_stop.is_set() or lease.lost.is_set())
            if lease.lost.is_set() or app.cloud_stop.is_set():
                raise WorkerDetachedError('Backend đang dừng; giữ kết quả S3 để phục hồi.')
            # Result/raw validated by pipeline; atomic FieldStore publication remains in service.
            for table in result if isinstance(result,tuple) else (result,):
                ctx._execution.store.put(table,(video.id,ctx._execution.spec_hash))
            row['state'] = 'done'
            row['payload'].update(stage=family(row).upper()+' sẵn sàng',error=None)
            persist()
    except WorkerDetachedError:
        # State remains active; dispatcher/restarted API reclaims after lease expiry.
        if row:
            job_map(app,family(row))[row['video_id']] = status(row)
    except Exception as exc:
        if row:
            safe = str(exc) if isinstance(exc,(ServiceError,JobStoreError,CloudStorageError,EmbeddingValidationError,KaggleJobError)) else 'Job S3/Kaggle lỗi; kiểm storage/notebook rồi retry.'
            if row['payload'].get('session') and not row['payload'].get('session_stopped'):
                row['payload'].update(terminal_error=safe,stage='dừng session sau lỗi nguồn/job')
                try:
                    stop_owned_session(row,lambda:store.update(row,owner))
                except (WorkerDetachedError,JobStoreError):
                    job_map(app,family(row))[row['video_id']] = status(row)
                    return
            row['state'] = 'error'
            row['payload'].update(error=safe,stage=family(row).upper()+' lỗi')
            try:
                store.update(row,owner)
            except JobStoreError:
                row['state'] = 'running'
                row['payload']['stage'] = 'chờ phục hồi kết nối Supabase'
            job_map(app,family(row))[row['video_id']] = status(row)
    finally:
        with app.clip_job_lock:
            app.cloud_scheduled.discard(job_id)


def start_recovery(app):
    if app.storage_backend != 's3':
        return
    app.cloud_stop.clear()
    def recover():
        while not app.cloud_stop.is_set():
            try:
                for row in app.embedding_jobs.list(active=True):
                    job_map(app,family(row))[row['video_id']] = status(row)
                    schedule(app,row)
            except Exception:
                log.warning('Cloud job recovery chưa kết nối được job DB; kiểm cấu hình/SQL setup.')
            app.cloud_stop.wait(15)
    threading.Thread(target=recover,name='cloud-job-recovery',daemon=True).start()
