"""C1 boundaries: S3 raw, worker cleanup/parity, progress, durable jobs and restart isolation.

Network/GPU-free tests with real small encoded videos; live cloud smoke is separate.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageOps

from src.rav.api import create_app
from src.rav.components.features.clip_b32 import ClipB32
from src.rav.components.features.dinov2_s import MEAN, STD, DinoV2S
from src.rav.components.features.phash import PHash
from src.rav.pipeline import prepare as p1
from src.rav.pipeline import s3_clip_worker as worker
from src.rav.pipeline.remote_embed import EmbeddingValidationError, validate_result
from src.rav.pipeline.remote_s3_embed import WorkerDetachedError, build_manifest, compute, manifest_hash, valid_progress
from src.rav.service import App, cloud_embedding
from src.rav.service import video as video_service
from src.rav.store.embedding_jobs import JobStoreError, LocalJobs, SupabaseJobs, now
from src.rav.store.s3 import CloudStorageError, S3Store

from .test_p1 import make_video


class MissingError(Exception):
    response = {'Error':{'Code':'404'}}


class MemoryS3:
    def __init__(self):
        self.objects, self.uploads = {}, []

    def head_object(self, *, Bucket, Key):  # noqa: N803 -- boto3 parameter names
        if Key not in self.objects:
            raise MissingError()
        data, metadata = self.objects[Key]
        return {'ContentLength':len(data),'Metadata':metadata}

    def upload_file(self,path,bucket,key,*,ExtraArgs,Config):  # noqa: N803
        assert Config.max_concurrency == 2 and ExtraArgs['ChecksumAlgorithm'] == 'SHA256'
        self.uploads.append(key)
        self.objects[key] = (Path(path).read_bytes(),ExtraArgs['Metadata'])

    def put_object(self,*,Bucket,Key,Body,**kwargs):  # noqa: N803
        self.objects[Key] = (Body,kwargs.get('Metadata',{}))

    def get_object(self,*,Bucket,Key):  # noqa: N803
        self.head_object(Bucket=Bucket,Key=Key)
        return {'Body':io.BytesIO(self.objects[Key][0])}

    def generate_presigned_url(self,method,*,Params,ExpiresIn):  # noqa: N803
        assert ExpiresIn <= 604800
        return 'https://signed.invalid/' + Params['Key']

    def list_objects_v2(self,**kwargs):
        return {'Contents':[]}


@pytest.fixture
def cloud(tmp_path):
    app = App(tmp_path/'videos',tmp_path/'output',1<<30,storage_backend='s3')
    app.videos_dir.mkdir()
    make_video(app.videos_dir/'small.mp4',seconds=1)
    client = MemoryS3()
    app.__dict__['cloud_storage'] = S3Store('test-bucket',client)
    return app,client


def row_for(app, ctx):
    return {'id':'a'*32,'video_id':ctx.units[0].video_id,'input_key':'input','state':'running',
            'created_at':now(),'updated_at':now(),'payload':{}}


def fake_http(monkeypatch, client):
    class Reply:
        def __init__(self,data): self.data = data
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def raise_for_status(self): pass
        def iter_content(self,size):
            for start in range(0,len(self.data),size):
                yield self.data[start:start+size]
    def key(url): return url.removeprefix('https://signed.invalid/')
    def get(url,**kwargs): return Reply(client.objects[key(url)][0])
    def put(url,*,data=None,json=None,**kwargs):
        raw = __import__('json').dumps(json).encode() if json is not None else data.read()
        client.objects[key(url)] = (raw,{})
        return Reply(b'')
    monkeypatch.setattr('requests.get',get)
    monkeypatch.setattr('requests.put',put)


class FakeGPU:
    def __init__(self,root,mode='ok'):
        self.root,self.mode,self.created,self.stopped,self.launches = root,mode,[],[],0
        self.root.mkdir()
        self.session = SimpleNamespace(session_id=123,operation_name='operations/create-kernel-session/123',tools=self)
    def ensure_kernel(self,*args,**kwargs): return 'owner/private'
    def create_session(self,*args,**kwargs):
        self.created.append(123)
        return self.session
    def wait_session(self,session,**kwargs): session.tools = self
    def put_file(self,local,remote):
        target = self.root / Path(remote).name
        target.write_bytes(Path(local).read_bytes())
    def execute(self,code,**kwargs):
        if 'subprocess.Popen' in code:
            self.launches += 1
            if self.mode == 'timeout':
                return {'status':'ok'}
            def embed(images):
                if self.mode == 'embed_error':
                    raise RuntimeError('https://signed.invalid/private-token')
                dim = json.loads((self.root/'config.json').read_text())['manifest']['dim']
                values = np.ones((len(images),dim),np.float32)
                return values / np.linalg.norm(values,axis=1,keepdims=True)
            worker.run(self.root/'config.json',embed_batch=embed)
        return {'status':'ok'}
    def stop_session(self,sid): self.stopped.append(sid)


def test_upload_s3_dedup_hash_and_error_not_success(cloud,tmp_path):
    app,client = cloud
    source = next(app.videos_dir.glob('*.mp4'))
    first = app.cloud_storage.upload_raw(source,'videos')
    assert first == app.cloud_storage.upload_raw(source,'videos') and len(client.uploads)==1
    client.objects[first['key']] = (b'bad',{'sha256':first['sha256']})
    with pytest.raises(CloudStorageError,match='không khớp'):
        app.cloud_storage.upload_raw(source,'videos')
    other = tmp_path/'new.mp4'
    make_video(other,seconds=2)
    def fail(*args,**kwargs): raise CloudStorageError('Upload S3 lỗi')
    app.cloud_storage.upload_raw = fail
    with pytest.raises(CloudStorageError):
        video_service.upload_video(app,other,'new.mp4')
    assert not (app.videos_dir/'new.mp4').exists()


def test_real_worker_raw_to_s3_cleanup_and_controller_validation(cloud,tmp_path,monkeypatch):
    app,client = cloud
    fake_http(monkeypatch,client)
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    gpu, job = FakeGPU(tmp_path/'gpu'),row_for(app,ctx)
    saved = []
    table = compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,
                    persist=lambda:saved.append(copy.deepcopy(job)),output_dir=app.output_dir,
                    client_factory=lambda:gpu,poll_seconds=0)
    assert table.unit_ids == [u.id for u in ctx.units] and table.values.shape==(4,512)
    assert gpu.created==[123] and gpu.stopped==[123] and gpu.launches==1
    assert not (gpu.root/'raw').exists() and not (gpu.root/'config.json').exists() and not (gpu.root/'result.npz').exists()
    assert not list((app.output_dir/'tmp').iterdir())
    assert job['payload']['remote_progress']['cleanup_done'] is True
    assert any(key.endswith('/result.npz') for key in client.objects)
    assert all('signed.invalid' not in json.dumps(row) for row in saved)
    assert not app.fields.get(ClipB32().key,(ctx.units[0].video_id,ctx._execution.spec_hash))
    # Completed S3 result is recoverable without a second GPU launch, after a backend restart.
    second_gpu = FakeGPU(tmp_path/'gpu2')
    recovered = compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                        output_dir=app.output_dir,client_factory=lambda:second_gpu)
    assert np.array_equal(recovered.values,table.values)
    assert not second_gpu.created and second_gpu.launches==0


def test_dino_transport_phash_preprocessing_cleanup_and_corrupt_result(cloud,tmp_path,monkeypatch):
    from importlib.metadata import version

    import cv2

    from src.rav.pipeline import dino_assets
    app,client = cloud
    fake_http(monkeypatch,client)
    runtime = {'opencv':version('opencv-python-headless'),'imagehash':version('ImageHash'),'scipy':version('scipy')}
    monkeypatch.setattr(dino_assets,'prepare_assets',lambda *args: ({},runtime))
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    image = Image.fromarray(ctx.pixels_exact(ctx.units[0].anchor))
    expected = ((cv2.resize(np.asarray(image),(224,224),interpolation=cv2.INTER_AREA).astype(np.float32)/255-MEAN)/STD).transpose(2,0,1)
    assert np.array_equal(worker.dino_preprocess([image],224)[0],expected)
    gpu,job = FakeGPU(tmp_path/'gpu-dino'),row_for(app,ctx)
    dino,hashes = compute(DinoV2S(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                          output_dir=app.output_dir,client_factory=lambda:gpu)
    assert dino.values.shape == (4,384) and dino.values.dtype == np.float32
    assert hashes.values.dtype == np.uint64 and np.array_equal(hashes.values,PHash().compute(ctx.units,ctx).values)
    assert job['payload']['result_key'].startswith('results/dino/')
    assert job['payload']['remote_progress']['cleanup_done'] and gpu.stopped == [123]
    assert not (gpu.root/'raw').exists() and not (gpu.root/'config.json').exists()
    result = tmp_path/'corrupt.npz'
    manifest = job['payload']['manifest']
    metadata = json.dumps({'manifest':manifest,'job_id':job['id'],'status':'done'})
    np.savez(result,values=dino.values,phash=hashes.values.astype(np.int64),metadata=metadata)
    with pytest.raises(EmbeddingValidationError,match='pHash'):
        validate_result(result,manifest,expected_job_id=job['id'])
    np.savez(result,values=dino.values,metadata=metadata)
    with pytest.raises(EmbeddingValidationError,match='container'):
        validate_result(result,manifest,expected_job_id=job['id'])


def test_video_upload_idle_and_explicit_jobs_are_independent(cloud,tmp_path):
    import queue
    app,_ = cloud
    app.auto_prepare = True
    app.__dict__['prepare_queue'] = queue.Queue()
    with TestClient(create_app(app)) as http:
        source = make_video(tmp_path/'uploaded.mp4',seconds=2)
        reply = http.post('/api/v1/videos',files={'file':('uploaded.mp4',source.read_bytes(),'video/mp4')})
        assert reply.status_code == 200
        video = reply.json()
        assert video['prepare'] is None and video['clip_prepare'] is None
        assert not video['prepared'] and not video['clip_prepared']
        assert not app.embedding_jobs.list() and app.prepare_queue.empty()
        http.get('/api/v1/videos')
        assert not app.embedding_jobs.list()
        vid = video['id']
        dino = http.post(f'/api/v1/videos/{vid}/prepare').json()
        assert dino['prepare']['state']=='queued' and dino['clip_prepare'] is None
        assert http.post(f'/api/v1/videos/{vid}/prepare').json()['prepare']['job_id']==dino['prepare']['job_id']
        clip = http.post(f'/api/v1/videos/{vid}/prepare-clip').json()
        assert clip['prepare']['job_id']==dino['prepare']['job_id']
        assert clip['clip_prepare']['job_id'] != clip['prepare']['job_id']
        assert len(app.embedding_jobs.list(video_id=vid)) == 2
        assert app.prepare_queue.qsize() == 2
        app.prepare_jobs.clear()
        app.clip_prepare_jobs.clear()
        assert cloud_embedding.get_status(app,vid,feature='dino')['job_id'] == dino['prepare']['job_id']
        assert cloud_embedding.get_status(app,vid)['job_id'] == clip['clip_prepare']['job_id']


def test_dino_assets_frozen_dedup_without_inference(cloud,tmp_path,monkeypatch):
    from src.rav.pipeline.dino_assets import prepare_assets
    app,client = cloud
    hub = tmp_path/'torch'/'hub'
    source = hub/'facebookresearch_dinov2_main'
    source.mkdir(parents=True)
    (source/'hubconf.py').write_text('# frozen source')
    (source/'LICENSE').write_text('Apache 2.0')
    (hub/'checkpoints').mkdir()
    (hub/'checkpoints'/'dinov2_vits14_pretrain.pth').write_bytes(b'weights')
    monkeypatch.setenv('TORCH_HOME',str(hub.parent))
    first,_ = prepare_assets(app.cloud_storage,app.output_dir)
    assert prepare_assets(app.cloud_storage,app.output_dir)[0] == first and len(client.uploads)==2
    assert not list((app.output_dir/'tmp').iterdir())
    (source/'hubconf.py').write_text('# changed source')
    assert prepare_assets(app.cloud_storage,app.output_dir)[0]['source']['sha256'] != first['source']['sha256']
    (hub/'checkpoints'/'dinov2_vits14_pretrain.pth').unlink()
    with pytest.raises(EmbeddingValidationError,match='Thiếu'):
        prepare_assets(app.cloud_storage,app.output_dir)


def test_dino_job_publication_cached_skip_and_no_cpu_fallback(cloud,monkeypatch):
    import queue

    from src.rav.core.types import FieldTable
    from src.rav.service import ServiceError
    app,_ = cloud
    app.__dict__['prepare_queue'] = queue.Queue()
    video = next(iter(p1.catalog(app.spec).values()))
    with pytest.raises(ServiceError,match='queue_prepare'):
        video_service.prepare_video(app,video.id)
    cloud_embedding.queue(app,video.id,feature='dino')
    row = app.embedding_jobs.list(video_id=video.id)[-1]
    def complete(feature,units,ctx,**kwargs):
        assert isinstance(feature,DinoV2S)
        return (FieldTable(feature.key,[u.id for u in units],np.ones((len(units),384),np.float32)/np.sqrt(384),'vector'),
                PHash().compute(units,ctx))
    monkeypatch.setattr(cloud_embedding,'compute',complete)
    cloud_embedding.run(app,row['id'])
    assert app.embedding_jobs.get(row['id'])['state']=='done' and video_service.is_prepared(app,video)
    assert app.prepare_jobs[video.id]['state']=='done' and not app.clip_prepare_jobs.get(video.id)
    count = len(app.embedding_jobs.list())
    cloud_embedding.queue(app,video.id,feature='dino')
    assert len(app.embedding_jobs.list()) == count
    app.prepare_jobs.clear()
    assert cloud_embedding.get_status(app,video.id,feature='dino')['state']=='done'


@pytest.mark.parametrize('field,value',[('job_id','wrong'),('manifest_hash','wrong'),('done',99),
                                       ('total',99),('sequence',-1),('cleanup_done',False)])
def test_progress_job_binding_and_terminal_cleanup(cloud,field,value):
    app,_ = cloud
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    job = row_for(app,ctx)
    manifest = build_manifest(ClipB32(),ctx.units,ctx,app.cloud_storage)
    job['payload'].update(manifest=manifest,manifest_hash=manifest_hash(manifest))
    progress = {'job_id':job['id'],'manifest_hash':job['payload']['manifest_hash'],'sequence':1,
                'state':'done','stage':'done','done':4,'total':4,'cleanup_done':True,'result_sha256':'a'*64}
    assert valid_progress(progress,job)
    progress[field] = value
    with pytest.raises(EmbeddingValidationError):
        valid_progress(progress,job)


def test_local_job_restart_dedup_lease_and_secret_rejection(tmp_path):
    store = LocalJobs(tmp_path/'jobs')
    row = {'id':'b'*32,'input_key':'same','video_id':'v','state':'queued','created_at':now(),'payload':{}}
    store.create(row)
    assert store.create({**row,'id':'c'*32})['id']==row['id']
    restarted = LocalJobs(tmp_path/'jobs')
    claimed = restarted.claim(row['id'],'owner')
    assert claimed and restarted.claim(row['id'],'other') is None
    claimed['payload']['done'] = 8
    restarted.update(claimed,'owner')
    assert store.get(row['id'])['payload']['done']==8
    claimed['payload']['url'] = 'https://signed.invalid/token'
    with pytest.raises(JobStoreError):
        restarted.update(claimed,'owner')


def test_supabase_backend_key_only_and_rpc_leases():
    calls = []
    class HTTP:
        def request(self,method,url,**kwargs):
            calls.append((method,url,kwargs))
            assert kwargs['headers']['apikey']=='sb_secret_test'
            assert 'Authorization' not in kwargs['headers']
            return SimpleNamespace(ok=True,status_code=200,json=lambda:[{'id':'a','payload':{}}])
    store = SupabaseJobs('https://test.supabase.co','sb_secret_test',HTTP())
    assert store.claim('a','00000000-0000-0000-0000-000000000000')['id']=='a'
    store.update({'id':'a','state':'running','payload':{}},'owner')
    assert calls[0][1].endswith('/rpc/rav_claim_embedding_job')
    assert calls[1][2]['json']['p_payload']=={}
    with pytest.raises(JobStoreError):
        SupabaseJobs('http://test.supabase.co','key')


@pytest.mark.parametrize('code,expected',[(404,'schema cache'),(401,'key/quyền')])
def test_supabase_errors_are_actionable_and_redacted(code,expected):
    class HTTP:
        def request(self,*args,**kwargs):
            return SimpleNamespace(ok=False,status_code=code,json=lambda:{'message':'secret-url'})
    with pytest.raises(JobStoreError,match=expected) as caught:
        SupabaseJobs('https://test.supabase.co','sb_secret_test',HTTP()).list()
    assert 'secret-url' not in str(caught.value)


def test_missing_local_video_stops_owned_gpu_before_terminal_error(cloud,monkeypatch):
    app,_ = cloud
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    row = row_for(app,ctx)
    row['payload']['session']={'session_id':123,'operation_name':'op'}
    app.embedding_jobs.create(row)
    source = next(app.videos_dir.glob('*.mp4'))
    source.unlink()
    stopped = []
    from src.rav.pipeline import remote_s3_embed
    monkeypatch.setattr(remote_s3_embed,'job_client',lambda job:SimpleNamespace(stop_session=stopped.append))
    cloud_embedding.run(app,row['id'])
    final = app.embedding_jobs.get(row['id'])
    assert final['state']=='error' and final['payload']['session_stopped']
    assert stopped==[123]


def test_restore_without_kaggle_auth_keeps_cleanup_recoverable(monkeypatch):
    from src.rav.pipeline.remote_s3_embed import job_client
    monkeypatch.setattr('src.rav.pipeline.remote_s3_embed.dotenv_values',lambda _: {})
    row = {'payload':{'session':{'session_id':123,'operation_name':'op'}}}
    with pytest.raises(WorkerDetachedError):
        job_client(row)


def test_result_other_job_rejected(tmp_path):
    manifest = {'sample_ids':['x']}
    result = tmp_path/'result.npz'
    values = np.ones((1,512),np.float32)/np.sqrt(512)
    np.savez(result,values=values,metadata=json.dumps({'manifest':manifest,'job_id':'other','status':'done'}))
    with pytest.raises(EmbeddingValidationError,match='job ID'):
        validate_result(result,manifest,expected_job_id='expected')


def test_backend_cloud_status_and_errors_never_return_keys(cloud):
    app,_ = cloud
    client = TestClient(create_app(app))
    response = client.get('/api/v1/cloud/status')
    assert response.status_code==200 and response.json()['s3_ready'] and response.json()['jobs_ready']
    assert 'AWS_' not in response.text and 'signed.invalid' not in response.text


def test_service_publication_after_transport_and_durable_status(cloud,monkeypatch):
    app,_ = cloud
    video = next(iter(p1.catalog(app.spec).values()))
    ctx = p1.make_context([video],app.spec,app.fields)
    row = row_for(app,ctx)
    row['state']='queued'
    app.embedding_jobs.create(row)
    def complete(feature,units,ctx,**kwargs):
        from src.rav.core.types import FieldTable
        assert not app.fields.get(feature.key,(video.id,ctx._execution.spec_hash))
        kwargs['job']['payload']['remote_progress']={'done':4,'total':4,'cleanup_done':True}
        return FieldTable(feature.key,[u.id for u in units],np.ones((4,512),np.float32)/np.sqrt(512),'vector')
    monkeypatch.setattr(cloud_embedding,'compute',complete)
    cloud_embedding.run(app,row['id'])
    assert app.embedding_jobs.get(row['id'])['state']=='done'
    assert app.fields.get(ClipB32().key,(video.id,ctx._execution.spec_hash)) is not None
    app.clip_prepare_jobs.clear()
    assert cloud_embedding.get_status(app,video.id)['state']=='done'


def test_detach_keeps_session_and_job_active(cloud,tmp_path,monkeypatch):
    app,_ = cloud
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    gpu,job = FakeGPU(tmp_path/'gpu','timeout'),row_for(app,ctx)
    with pytest.raises(WorkerDetachedError):
        compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                output_dir=app.output_dir,client_factory=lambda:gpu,should_detach=lambda:True)
    assert not gpu.stopped and job['payload']['launched']


def test_restart_after_launch_recovers_same_session_result(cloud,tmp_path,monkeypatch):
    app,client = cloud
    fake_http(monkeypatch,client)
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    gpu,job = FakeGPU(tmp_path/'gpu'),row_for(app,ctx)
    with pytest.raises(WorkerDetachedError):
        compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                output_dir=app.output_dir,client_factory=lambda:gpu,should_detach=lambda:True)
    restarted = FakeGPU(tmp_path/'gpu-restarted')
    table = compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                    output_dir=app.output_dir,client_factory=lambda:restarted)
    assert table.values.shape==(4,512) and not gpu.stopped
    assert restarted.stopped==[123] and not restarted.created and restarted.launches==0


def test_worker_error_cleans_raw_and_redacts_exception(cloud,tmp_path,monkeypatch):
    app,client = cloud
    fake_http(monkeypatch,client)
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    gpu,job = FakeGPU(tmp_path/'gpu','embed_error'),row_for(app,ctx)
    with pytest.raises(RuntimeError) as error:
        compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                output_dir=app.output_dir,client_factory=lambda:gpu)
    assert 'private-token' not in str(error.value)
    assert not (gpu.root/'raw').exists() and not (gpu.root/'config.json').exists()
    assert gpu.stopped==[123] and job['payload']['remote_progress']['cleanup_done'] is True
    assert not any(key.endswith('/result.npz') for key in client.objects)


def test_failed_session_stop_is_durable_and_recovered(cloud,tmp_path,monkeypatch):
    app,client = cloud
    fake_http(monkeypatch,client)
    ctx = p1.make_context(list(p1.catalog(app.spec).values()),app.spec,app.fields)
    gpu,job = FakeGPU(tmp_path/'gpu'),row_for(app,ctx)
    def fail(sid):
        raise RuntimeError('signed-secret')
    gpu.stop_session = fail
    with pytest.raises(WorkerDetachedError):
        compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                output_dir=app.output_dir,client_factory=lambda:gpu)
    assert job['payload']['stop_pending']
    recovered = FakeGPU(tmp_path/'gpu2')
    table = compute(ClipB32(),ctx.units,ctx,storage=app.cloud_storage,job=job,persist=lambda:None,
                    output_dir=app.output_dir,client_factory=lambda:recovered)
    assert table.values.shape==(4,512) and recovered.stopped==[123] and not recovered.created


def test_remote_pixels_vfr_counts_decode_indices(tmp_path):
    path = tmp_path/'vfr.mp4'
    with av.open(str(path),'w') as container:
        stream = container.add_stream('mpeg4',rate=10)
        stream.width,stream.height,stream.pix_fmt = 64,48,'yuv420p'
        stream.codec_context.time_base = Fraction(1,10)
        for index,pts in enumerate((0,1,4,6)):
            image = Image.new('RGB',(64,48),(index*60,20,0))
            ImageDraw.Draw(image).text((10,10),str(index),fill='white')
            frame = av.VideoFrame.from_ndarray(np.array(image),format='rgb24')
            frame.pts,frame.time_base = pts,Fraction(1,10)
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    with av.open(str(path)) as container:
        decoded = list(container.decode(video=0))
    assert float(decoded[2].time)==.4
    reader = worker.Pixels({'v':{'kind':'video'}},{'v':path})
    try:
        for index in (0,2,3,1):
            assert np.array_equal(reader.read({'source':'v','idx':index}),decoded[index].to_ndarray(format='rgb24'))
    finally:
        reader.close()


def test_image_manifest_and_worker_exif_parity(tmp_path):
    from src.rav.core.types import ImageRef, Unit
    path = tmp_path/'image.jpg'
    image = Image.new('RGB',(16,8),(120,30,70))
    exif = image.getexif()
    exif[274]=6
    image.save(path,exif=exif)
    frame = ImageRef('images','img-key','image.jpg')
    unit = Unit('image-unit',None,None,None,'image',frame,())
    def pixels(frame):
        with Image.open(path) as source:
            return np.asarray(ImageOps.exif_transpose(source).convert('RGB')).copy()
    ctx = SimpleNamespace(videos={},images={'images':SimpleNamespace(root=str(tmp_path))},pixels_exact=pixels)
    storage = S3Store('test',MemoryS3())
    manifest = build_manifest(ClipB32(),[unit],ctx,storage)
    reader = worker.Pixels(manifest['sources'],{'img-key':path})
    assert reader.read(manifest['samples'][0][0]).shape==(16,8,3)
    assert hashlib.sha256(reader.read(manifest['samples'][0][0]).tobytes()).hexdigest()==manifest['pixel_checks'][0]['sha256']


def test_model_and_dataset_raw_go_to_s3_before_success(cloud,tmp_path):
    import zipfile

    from src.rav.service import dataset, reference_model
    app,client = cloud
    app.datasets_dir = tmp_path/'datasets'
    root = app.datasets_dir/'sample'
    root.mkdir(parents=True)
    Image.new('RGB',(12,12),'red').save(root/'a.png')
    imported = dataset.import_dataset(app,'sample','sample',None,'none',None)
    assert imported['created'] and any(k.startswith('datasets/') and k.endswith('.png') for k in client.objects)
    path = tmp_path/'model.pt'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('model/data.pkl',b'opaque')
        archive.writestr('model/version',b'3')
    app.models_dir = tmp_path/'models'
    uploaded = reference_model.upload_model(app,'model.pt',str(path))
    assert uploaded['created'] and any(k.startswith('models/') and k.endswith('.pt') for k in client.objects)


def test_conflicting_dataset_labels_are_all_preserved_on_s3(cloud,tmp_path):
    from src.rav.service import dataset
    app,client = cloud
    app.datasets_dir = tmp_path/'datasets'
    root = app.datasets_dir/'conflicts'
    (root/'images').mkdir(parents=True)
    (root/'labels').mkdir()
    (root/'classes.txt').write_text('car\n')
    image = Image.new('RGB',(12,12),'red')
    for name,label in [('a','0 .5 .5 .2 .2'),('b','0 .5 .5 .3 .3')]:
        image.save(root/'images'/f'{name}.png')
        (root/'labels'/f'{name}.txt').write_text(label)
    imported = dataset.import_dataset(app,'conflicts','conflicts',None,'yolo',None)
    key = f"datasets/{imported['version']['id']}.json"
    objects = json.loads(client.objects[key][0])['objects']
    assert set(objects)=={'classes.txt','images/a.png','images/b.png','labels/a.txt','labels/b.txt'}
    assert objects['images/a.png']['key']==objects['images/b.png']['key']
    assert objects['labels/a.txt']['key']!=objects['labels/b.txt']['key']
