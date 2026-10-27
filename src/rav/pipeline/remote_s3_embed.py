"""Durable S3/Kaggle transport: raw manifest, detached worker, validated progress and result.

Input feature/ordered units/context + leased job; returns validated FieldTable, never publishes cache itself.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import av
import PIL
from dotenv import dotenv_values

from ..components.features.phash import PHash
from ..core.types import FieldTable
from ..store.embedding_jobs import JobStoreError
from .kaggle_gpu import GPUSession, KaggleGPUClient
from .remote_embed import EmbeddingValidationError, KaggleJobError, source_hashes, validate_result


class WorkerDetachedError(RuntimeError):
    """Backend stopped/lease lost; leave GPU/job alive for its next owner."""


def job_client(job, client_factory=None):
    try:
        if client_factory:
            return client_factory()
        token = dotenv_values('.env').get('KAGGLE_API_TOKEN')
        if not token:
            raise ValueError('Thiếu KAGGLE_API_TOKEN trong .env repo.')
        return KaggleGPUClient(api_token=token)
    except Exception:
        if job['payload'].get('session'):
            raise WorkerDetachedError('Chưa kết nối được Kaggle để phục hồi/dừng session; giữ job active.') from None
        raise KaggleJobError('Không khởi tạo được Kaggle client; kiểm KAGGLE_API_TOKEN trong .env repo.') from None


def stop_owned_session(job, persist, client=None):
    """Stop only the session recorded by this job, including when local input is gone."""
    payload = job['payload']
    if not payload.get('session') or payload.get('session_stopped'):
        return
    payload['stop_pending'] = True
    persist()
    try:
        client = client or job_client(job)
        client.stop_session(payload['session']['session_id'])
    except Exception:
        raise WorkerDetachedError('Chưa dừng được session của job; backend sẽ phục hồi cleanup.') from None
    payload.update(session_stopped=True,stop_pending=False)
    persist()


def manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def build_manifest(feature, units, ctx, storage):
    hashes = source_hashes(units, ctx)
    sources, samples, paths = {}, [], {}
    for unit in units:
        frames = []
        for frame in unit.frames or (unit.anchor,):
            if unit.video_id is not None:
                key = frame.video_id
                paths[key] = Path(ctx.videos[key].uri)
                frames.append({'source':key,'idx':frame.idx})
                kind, prefix = 'video', 'videos'
            else:
                key = frame.key
                paths[key] = Path(ctx.images[frame.source_id].root) / frame.relpath
                frames.append({'source':key})
                kind, prefix = 'image', 'datasets'
            if key not in sources:
                sources[key] = {**storage.upload_raw(paths[key],prefix,hashes[key]),'kind':kind}
        samples.append(frames)
    checks = []
    for index in sorted({0,len(units)//2,len(units)-1}):
        frames = units[index].frames or (units[index].anchor,)
        for frame, ref in zip(frames,samples[index]):
            checks.append({'frame':ref,'sha256':hashlib.sha256(ctx.pixels_exact(frame).tobytes()).hexdigest()})
    dino = feature.name == 'dinov2_s'
    manifest = {'field_key':asdict(feature.key),
                'sample_ids':[u.id for u in units],'source_sha256':hashes,'sources':sources,'samples':samples,
                'pixel_checks':checks,'runtime':{'av':av.__version__,'pillow':PIL.__version__,
                                               'ffmpeg':{k:list(v) for k,v in av.library_versions.items()}},
                'dim':384 if dino else 512,'dtype':'float32','normalization':'L2','extraction':'S3-raw/strict-index/PIL-EXIF-RGB'}
    if dino:
        manifest.update(embedding_family='dino',image_size=feature.params.image_size,
                        phash_key=asdict(PHash().key),
                        anchors=[{'source':u.anchor.video_id,'idx':u.anchor.idx} for u in units])
    else:
        manifest.update(model=feature.params.model,revision=feature.params.revision)
    if source_hashes(units,ctx) != hashes:
        raise EmbeddingValidationError('Nguồn đổi khi dựng manifest S3.')
    return manifest


def launch_remote(session, remote_root, config, output_dir):
    """Đưa worker + config lên session Kaggle rồi chạy nền; lock trong session chặn process thứ hai khi mất ACK."""
    setup = session.tools.execute(f"from pathlib import Path\nPath({remote_root!r}).mkdir(exist_ok=True)",timeout=60)
    if setup.get('status') != 'ok':
        raise KaggleJobError('Không tạo được workspace của job Kaggle.')
    temp_root = Path(output_dir) / 'tmp'
    temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='clip-manifest-',dir=temp_root) as temp:
        config_file = Path(temp) / 'config.json'
        config_file.write_text(json.dumps(config),encoding='utf-8')
        session.tools.put_file(str(Path(__file__).with_name('s3_clip_worker.py')),remote_root+'/worker.py')
        session.tools.put_file(str(config_file),remote_root+'/config.json')
    launch = f'''import os, sys, subprocess
from pathlib import Path
root=Path({remote_root!r})
try:
    fd=os.open(str(root/'launch.lock'),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
except FileExistsError:
    pass
else:
    os.close(fd)
    child=subprocess.Popen([sys.executable,str(root/'worker.py'),str(root/'config.json')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    (root/'pid').write_text(str(child.pid))
'''
    if session.tools.execute(launch,timeout=60).get('status') != 'ok':
        raise KaggleJobError('Không launch được worker Kaggle.')


def valid_progress(progress, job):
    if not isinstance(progress,dict):
        raise EmbeddingValidationError('Progress Kaggle không hợp lệ.')
    payload = job['payload']
    total = len(payload['manifest']['sample_ids'])
    if (progress.get('job_id') != job['id'] or progress.get('manifest_hash') != payload['manifest_hash']
            or type(progress.get('sequence')) is not int or progress['sequence'] < 1
            or progress.get('state') not in ('running','done','error')
            or type(progress.get('done')) is not int or not 0 <= progress['done'] <= total
            or progress.get('total') != total or not isinstance(progress.get('stage'),str)
            or len(progress['stage']) > 200 or any(s in progress['stage'] for s in ('https://','X-Amz-','sb_secret_'))):
        raise EmbeddingValidationError('Progress không khớp job/manifest/count.')
    previous = payload.get('remote_progress',{})
    if progress['sequence'] <= previous.get('sequence',0):
        return False
    if progress['done'] < previous.get('done',0):
        raise EmbeddingValidationError('Progress Kaggle đi lùi.')
    if previous.get('state') in ('done','error') and progress['state'] != previous['state']:
        raise EmbeddingValidationError('Progress Kaggle thay terminal state.')
    if progress['state'] == 'done' and (progress['done'] != total or progress.get('cleanup_done') is not True
            or not isinstance(progress.get('result_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',progress['result_sha256'])):
        raise EmbeddingValidationError('Kaggle chưa hoàn tất kết quả/cleanup.')
    # Never persist arbitrary remote strings (including exception URLs) or unknown fields.
    return True


def compute(feature, units, ctx, *, storage, job, persist, output_dir, timeout=7200,
            client_factory=None, should_detach=lambda:False, poll_seconds=3):
    payload = job['payload']
    if not payload.get('manifest'):
        payload['stage'] = 'lưu raw S3 / kiểm pixel nguồn'
        persist()
        payload['manifest'] = build_manifest(feature,units,ctx,storage)
        if feature.name == 'dinov2_s':
            from .dino_assets import prepare_assets
            payload['manifest']['assets'], runtime = prepare_assets(storage,output_dir)
            payload['manifest']['runtime'].update(runtime)
        payload['manifest_hash'] = manifest_hash(payload['manifest'])
        persist()
    manifest = payload['manifest']
    client = job_client(job,client_factory)
    session, detach = None, False
    session_data = payload.get('session')
    if session_data:
        session = GPUSession(session_data['session_id'],session_data['operation_name'])
    kind = 'dino' if feature.name == 'dinov2_s' else 'clip'
    progress_key = f"results/{kind}/{job['id']}/progress.json"
    result_key = f"results/{kind}/{job['id']}/result.npz"
    payload.update(progress_key=progress_key,result_key=result_key)
    started = payload.setdefault('started_epoch',time.time())
    deadline = started + timeout
    def stage(text):
        payload['stage'] = text
        persist()
    try:
        if (manifest['sample_ids'] != [u.id for u in units] or manifest['field_key'] != asdict(feature.key)
                or manifest['source_sha256'] != source_hashes(units,ctx)):
            raise EmbeddingValidationError('Nguồn/spec của job cũ không còn khớp; không publish cache.')
        if payload.get('stop_pending'):
            stop_owned_session(job,persist,client)
            if payload.get('terminal_error'):
                raise KaggleJobError(payload['terminal_error'])
        progress = storage.read_json(progress_key)
        if progress and valid_progress(progress,job):
            payload['remote_progress'] = {k:progress[k] for k in ('job_id','manifest_hash','sequence','state','stage','done','total','cleanup_done','result_sha256') if k in progress}
            persist()
        remote_done = payload.get('remote_progress',{}).get('state') == 'done'
        if not remote_done:
            if time.time() >= deadline:
                raise KaggleJobError('Job Kaggle đã quá deadline; cần retry explicit.')
            if not session:
                stage('cấp Kaggle GPU')
                kernel = client.ensure_kernel(output_dir,override=dotenv_values('.env').get('RAV_KAGGLE_KERNEL',''))
                session = client.create_session(kernel,accelerator='t4',enable_internet=True,wait=False)
                payload['session'] = {'session_id':session.session_id,'operation_name':session.operation_name}
                persist()
            stage('khởi động/attach Kaggle GPU')
            client.wait_session(session,timeout=min(900,max(1,deadline-time.time())))
            if not payload.get('launched'):
                ttl = min(604800,max(3600,int(deadline-time.time())+1800))
                config = {'job_id':job['id'],'manifest':manifest,'manifest_hash':payload['manifest_hash'],
                          'input_urls':{k:storage.signed(s['key'],'get_object',ttl) for k,s in manifest['sources'].items()},
                          'progress_url':storage.signed(progress_key,'put_object',ttl,'application/json'),
                          'result_url':storage.signed(result_key,'put_object',ttl,'application/octet-stream')}
                if manifest.get('assets'):
                    config['asset_urls'] = {k:storage.signed(s['key'],'get_object',ttl) for k,s in manifest['assets'].items()}
                stage('gửi manifest S3 cho Kaggle')
                launch_remote(session,'/kaggle/working/rav-' + job['id'],config,output_dir)
                payload['launched'] = True
                persist()
            stage('Kaggle tải S3 / tính embedding')
            while True:
                if should_detach():
                    detach = True
                    raise WorkerDetachedError('Backend dừng/lease đổi; job chờ phục hồi.')
                progress = storage.read_json(progress_key)
                if progress and valid_progress(progress,job):
                    payload['remote_progress'] = {k:progress[k] for k in ('job_id','manifest_hash','sequence','state','stage','done','total','cleanup_done','result_sha256') if k in progress}
                    if progress.get('error_code') in ('decoder_runtime','pixel_parity','input_size','input_sha256','frame_index','cuda_required'):
                        payload['remote_progress']['error_code'] = progress['error_code']
                    payload['stage'] = progress['stage']
                    persist()
                status = payload.get('remote_progress',{}).get('state')
                if status == 'error':
                    code = payload['remote_progress'].get('error_code','compute_or_transfer')
                    raise KaggleJobError('Kaggle worker lỗi ('+code+'); dữ liệu tải về đã được dọn hoặc session sẽ bị dừng.')
                if status == 'done':
                    break
                if time.time() >= deadline:
                    raise KaggleJobError('Job Kaggle quá thời gian cho phép; không nhận embedding partial.')
                time.sleep(poll_seconds)
        stage('tải/kiểm embedding từ S3')
        temp_root = output_dir / 'tmp'
        temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='clip-result-',dir=temp_root) as folder:
            result = Path(folder) / 'result.npz'
            storage.download(result_key,result,max_bytes=len(units)*(manifest['dim']*4+8)+len(json.dumps(manifest).encode())*4+(1<<20),
                             expected_sha=payload['remote_progress']['result_sha256'])
            values = validate_result(result,manifest,expected_job_id=job['id'])
        if source_hashes(units,ctx) != manifest['source_sha256']:
            raise EmbeddingValidationError('Nguồn đổi khi tính embedding; không ghi cache.')
        if kind == 'dino':
            if manifest.get('phash_key') != asdict(PHash().key):
                raise EmbeddingValidationError('pHash producer đã đổi; không ghi cache.')
            vectors,hashes = values
            return (FieldTable(feature.key,manifest['sample_ids'],vectors,'vector'),
                    FieldTable(PHash().key,manifest['sample_ids'],hashes,'scalar'))
        return FieldTable(feature.key,manifest['sample_ids'],values,'vector')
    except (WorkerDetachedError, JobStoreError, KeyboardInterrupt, SystemExit):
        detach = True
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc,(KaggleJobError,EmbeddingValidationError)) else 'Job S3/Kaggle lỗi; kiểm storage/notebook/quota rồi retry.'
        payload['terminal_error'] = message
        persist()
        raise KaggleJobError(message) from None
    finally:
        if session and not detach and not payload.get('session_stopped'):
            try:
                if session.tools and payload.get('terminal_error'):
                    remote_root = '/kaggle/working/rav-' + job['id']
                    cleanup = f'''import os, signal, shutil
from pathlib import Path
root=Path({remote_root!r})
if (root/'pid').exists():
    try: os.killpg(int((root/'pid').read_text()),signal.SIGKILL)
    except ProcessLookupError: pass
shutil.rmtree(root,ignore_errors=True)
'''
                    session.tools.execute(cleanup,timeout=30)
                stop_owned_session(job,persist,client)
            except Exception:
                payload['stop_pending'] = True
                persist()
                raise WorkerDetachedError('Chưa dừng được session của job; backend sẽ phục hồi cleanup.') from None
