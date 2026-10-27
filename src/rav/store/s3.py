"""Private S3 durable raw/results and scoped job URLs; input .env config, output object refs.

Uses bounded multipart/streaming, content hashes and safe errors. No machine credential fallback.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dotenv import dotenv_values


class CloudStorageError(RuntimeError):
    """Safe storage error without SDK messages, credentials or signed URLs."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class S3Store:
    def __init__(self, bucket: str, client):
        if not bucket:
            raise CloudStorageError('Thiếu RAV_S3_BUCKET trong .env repo.')
        self.bucket, self.client = bucket, client

    @classmethod
    def from_repo(cls):
        import boto3
        from botocore.config import Config

        cfg = dotenv_values('.env')
        if not cfg.get('AWS_ACCESS_KEY_ID') or not cfg.get('AWS_SECRET_ACCESS_KEY'):
            raise CloudStorageError('Thiếu AWS credential trong .env repo.')
        client = boto3.client('s3', aws_access_key_id=cfg['AWS_ACCESS_KEY_ID'],
                              aws_secret_access_key=cfg['AWS_SECRET_ACCESS_KEY'],
                              aws_session_token=cfg.get('AWS_SESSION_TOKEN'),
                              region_name=cfg.get('AWS_DEFAULT_REGION') or 'us-east-1',
                              config=Config(signature_version='s3v4', connect_timeout=15, read_timeout=60,
                                            retries={'max_attempts': 3, 'mode': 'standard'}))
        return cls(cfg.get('RAV_S3_BUCKET', ''), client)

    def head(self, key):
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code in ('404', 'NoSuchKey', 'NotFound'):
                return None
            raise CloudStorageError('Không kiểm tra được object S3; kiểm quyền/kết nối.') from None

    def upload_raw(self, path: Path, prefix: str, expected_sha: str | None = None) -> dict:
        from boto3.s3.transfer import TransferConfig

        digest = file_sha256(path)
        if expected_sha and digest != expected_sha:
            raise CloudStorageError('Nguồn đã đổi trước upload S3.')
        size = path.stat().st_size
        suffix = path.suffix.lower() if path.suffix.lower() in ('.mp4','.mov','.mkv','.avi','.webm','.pt','.png','.jpg','.jpeg','.txt','.yaml','.yml') else '.bin'
        key = f'{prefix.rstrip("/")}/{digest}{suffix}'
        existing = self.head(key)
        if existing is not None:
            if existing['ContentLength'] != size or existing.get('Metadata', {}).get('sha256') != digest:
                raise CloudStorageError('Object theo SHA trên S3 không khớp; không ghi đè.')
        else:
            try:
                self.client.upload_file(str(path), self.bucket, key,
                    ExtraArgs={'Metadata': {'sha256': digest}, 'ChecksumAlgorithm': 'SHA256'},
                    Config=TransferConfig(multipart_threshold=8 << 20, multipart_chunksize=8 << 20,
                                          max_concurrency=2, use_threads=True))
            except Exception:
                raise CloudStorageError('Upload S3 lỗi; không coi dữ liệu đã lưu thành công.') from None
        head = self.head(key)
        if (file_sha256(path) != digest or path.stat().st_size != size or not head
                or head['ContentLength'] != size or head.get('Metadata', {}).get('sha256') != digest):
            raise CloudStorageError('Nguồn/object đổi trong upload S3; không nhận kết quả.')
        return {'bucket': self.bucket, 'key': key, 'sha256': digest, 'size_bytes': size}

    def signed(self, key: str, method: str, ttl: int, content_type: str | None = None):
        if method not in ('get_object', 'put_object') or not 60 <= ttl <= 604800:
            raise CloudStorageError('Cấu hình URL S3 không hợp lệ.')
        params = {'Bucket': self.bucket, 'Key': key}
        if content_type:
            params['ContentType'] = content_type
        try:
            return self.client.generate_presigned_url(method, Params=params, ExpiresIn=ttl)
        except Exception:
            raise CloudStorageError('Không cấp được URL S3 cho job.') from None

    def put_json(self, key: str, value):
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(value, sort_keys=True).encode(),
                                   ContentType='application/json')
        except Exception:
            raise CloudStorageError('Không lưu được manifest S3.') from None

    def read_json(self, key: str, limit=1 << 20):
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key)
            with result['Body'] as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise CloudStorageError('Progress S3 vượt giới hạn.')
            return json.loads(raw)
        except CloudStorageError:
            raise
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code in ('404', 'NoSuchKey', 'NotFound'):
                return None
            raise CloudStorageError('Không đọc được progress S3 hợp lệ.') from None

    def download(self, key: str, dest: Path, *, max_bytes: int, expected_sha=None):
        head = self.head(key)
        if not head or head['ContentLength'] > max_bytes:
            raise CloudStorageError('Kết quả S3 thiếu hoặc vượt giới hạn.')
        digest, count = hashlib.sha256(), 0
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            with response['Body'] as source, dest.open('wb') as target:
                for chunk in iter(lambda: source.read(1 << 20), b''):
                    count += len(chunk)
                    if count > max_bytes:
                        raise CloudStorageError('Kết quả S3 vượt giới hạn.')
                    digest.update(chunk)
                    target.write(chunk)
            if count != head['ContentLength'] or expected_sha and digest.hexdigest() != expected_sha:
                raise CloudStorageError('SHA/kích thước kết quả S3 không khớp.')
        except Exception:
            dest.unlink(missing_ok=True)
            raise CloudStorageError('Không tải/kiểm được kết quả S3.') from None
