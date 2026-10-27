"""Durable embedding jobs and worker leases, local JSON or Supabase REST over HTTPS.

No signed URLs/credentials in records. SQL schema: docs/setup/SUPABASE_EMBEDDING_JOBS.sql.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values


class JobStoreError(RuntimeError):
    """Safe job database failure; remote errors/keys are never forwarded."""


def now():
    return datetime.now(UTC).isoformat()


def check_record(record):
    raw = json.dumps(record)
    if any(text in raw for text in ('https://', 'X-Amz-', 'sb_secret_', 'AWS_SECRET_ACCESS_KEY')):
        raise JobStoreError('Job record không được chứa credential/signed URL.')


class LocalJobs:
    # local-only: single API process per output; Supabase leases required across processes.
    def __init__(self, root: Path):
        self.root, self.lock = root, threading.RLock()
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id):
        if not isinstance(job_id, str) or not job_id or any(c not in '0123456789abcdef-' for c in job_id):
            raise JobStoreError('Job ID không hợp lệ.')
        return self.root / (job_id + '.json')

    def _save(self, row):
        check_record(row)
        path = self._path(row['id'])
        temp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text(json.dumps(row, sort_keys=True), encoding='utf-8')
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def list(self, video_id=None, active=False):
        with self.lock:
            rows = [json.loads(p.read_text(encoding='utf-8')) for p in self.root.glob('*.json')]
        return sorted((r for r in rows if (not video_id or r['video_id'] == video_id)
                       and (not active or r['state'] in ('queued','running'))), key=lambda r: r['created_at'])

    def get(self, job_id):
        with self.lock:
            path = self._path(job_id)
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def create(self, row):
        with self.lock:
            existing = next((r for r in self.list(active=True) if r['input_key'] == row['input_key']), None)
            if existing:
                return existing
            self._save(row)
            return copy.deepcopy(row)

    def claim(self, job_id, owner, seconds=120):
        with self.lock:
            row = self.get(job_id)
            if not row or row['state'] not in ('queued','running'):
                return None
            if row.get('lease_until', 0) > time.time() and row.get('lease_owner') != owner:
                return None
            row.update(lease_owner=owner, lease_until=time.time()+seconds)
            self._save(row)
            return row

    def update(self, row, owner):
        with self.lock:
            current = self.get(row['id'])
            if not current or current.get('lease_owner') != owner or current.get('lease_until', 0) <= time.time():
                raise JobStoreError('Lease job đã hết; không ghi trạng thái từ worker cũ.')
            row = copy.deepcopy(row)
            row.update(updated_at=now(), lease_owner=owner,
                       lease_until=0 if row['state'] in ('done','error') else time.time()+120)
            self._save(row)
            return row


class SupabaseJobs:
    def __init__(self, url, key, session=None):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ('','/') or parsed.query or not key:
            raise JobStoreError('Thiếu/sai SUPABASE_URL hoặc SUPABASE_SECRET_KEY trong .env.')
        self.url, self.key = url.rstrip('/') + '/rest/v1/', key
        self.session = session or requests.Session()

    @classmethod
    def from_repo(cls):
        cfg = dotenv_values('.env')
        return cls(cfg.get('SUPABASE_URL',''), cfg.get('SUPABASE_SECRET_KEY',''))

    def _request(self, method, path, **kwargs):
        headers = {'apikey': self.key, 'Prefer': 'return=representation'}
        if self.key.startswith('eyJ'):
            headers['Authorization'] = 'Bearer ' + self.key
        try:
            response = self.session.request(method, self.url + path, headers=headers, timeout=20, **kwargs)
            if not response.ok:
                if response.status_code == 409:
                    return None
                if response.status_code == 404:
                    raise JobStoreError('Supabase thiếu bảng/RPC trong schema cache; chạy docs/setup/SUPABASE_EMBEDDING_JOBS.sql ở đúng project.')
                if response.status_code in (401,403):
                    raise JobStoreError('Supabase từ chối key/quyền backend; kiểm SUPABASE_SECRET_KEY và quyền service_role trong SQL setup.')
                raise JobStoreError('Supabase job DB chưa sẵn sàng; kiểm key và chạy SQL setup trong docs/setup.')
            return response.json()
        except JobStoreError:
            raise
        except Exception:
            raise JobStoreError('Không kết nối được Supabase job DB; không fallback local.') from None

    def list(self, video_id=None, active=False):
        params = {'select':'*', 'order':'created_at.asc'}
        if video_id:
            params['video_id'] = 'eq.' + video_id
        if active:
            params['state'] = 'in.(queued,running)'
        return self._request('GET', 'rav_embedding_jobs', params=params)

    def get(self, job_id):
        rows = self._request('GET', 'rav_embedding_jobs', params={'id':'eq.'+job_id,'select':'*'})
        return rows[0] if rows else None

    def create(self, row):
        check_record(row)
        rows = self._request('POST','rav_embedding_jobs',json=row)
        if rows:
            return rows[0]
        match = next((r for r in self.list(active=True) if r['input_key'] == row['input_key']), None)
        if not match:
            raise JobStoreError('Không tạo được job Supabase.')
        return match

    def claim(self, job_id, owner, seconds=120):
        rows = self._request('POST','rpc/rav_claim_embedding_job',
                             json={'p_id':job_id,'p_owner':owner,'p_seconds':seconds})
        return rows[0] if rows else None

    def update(self, row, owner):
        check_record(row)
        rows = self._request('POST','rpc/rav_update_embedding_job',
            json={'p_id':row['id'],'p_owner':owner,'p_state':row['state'],'p_payload':row['payload']})
        if not rows:
            raise JobStoreError('Lease job đã hết; không ghi trạng thái từ worker cũ.')
        return rows[0]
