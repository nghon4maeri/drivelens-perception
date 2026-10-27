"""Owned S3 multipart transport (§21.27): short DB reads, per-upload advisory lock, bounded URL batches.

Input: verified actor/upload/part numbers. Output: metadata, existing part sizes, signed PUT URLs.
All S3 I/O runs outside DB transactions; persisted upload reservation precedes initiation.
"""
import hashlib
import math
from contextlib import contextmanager
from uuid import UUID

from ..errors import ServiceError
from .pilot_db import require_user
from .pilot_storage import MULTIPART_CHUNK


def layout(size: int) -> tuple[int, int]:
    chunk = max(MULTIPART_CHUNK, math.ceil(size / 10000 / (1 << 20)) * (1 << 20))
    return chunk, math.ceil(size / chunk)


@contextmanager
def upload_lock(db, upload_id: UUID, *, wait_s: int = 0):
    key = int.from_bytes(hashlib.sha256(('rav-multipart:'+str(upload_id)).encode()).digest()[:8], 'big', signed=True)
    with db._connection() as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(%s) AS locked', (key,)).fetchone()['locked']:
            if not wait_s:
                raise ServiceError('upload_busy', 'upload đang xử lý; thử lại sau', 429, {'retry_after': 1})
            from psycopg.errors import QueryCanceled
            old_timeout = conn.execute('SHOW statement_timeout').fetchone()['statement_timeout']
            try:
                conn.execute("SELECT set_config('statement_timeout',%s,false)", (f'{wait_s}s',))
                conn.execute('SELECT pg_advisory_lock(%s)', (key,))
            except QueryCanceled:
                raise ServiceError('upload_busy', 'cleanup đang chạy; thử lại sau', 429) from None
            finally:
                conn.execute("SELECT set_config('statement_timeout',%s,false)", (old_timeout,))
        try:
            yield conn
        finally:
            conn.execute('SELECT pg_advisory_unlock(%s)', (key,))


class PilotMultipart:
    def __init__(self, db, storage):
        self.db, self.storage = db, storage

    def _row(self, conn, actor, upload_id):
        uid = require_user(actor)
        row = conn.execute('SELECT u.*, greatest(0, extract(epoch FROM u.capability_expires_at-now()))::int AS ttl '
                           'FROM rav_pilot_upload u JOIN rav_pilot_profile p ON p.user_id=u.owner_id '
                           'WHERE u.id=%s AND u.owner_id=%s AND p.active', (upload_id, uid)).fetchone()
        if not row:
            raise ServiceError('not_found', 'không tìm thấy upload', 404)
        if row['transfer_method'] != 'MULTIPART' or row['state'] != 'CREATED' or row['ttl'] < 1:
            raise ServiceError('upload_invalid', 'multipart không còn khả dụng; kiểm tra trạng thái upload', 409)
        return row

    def info(self, actor, upload_id, *, start=False):
        with upload_lock(self.db, upload_id) as conn:
            row = self._row(conn, actor, upload_id)
            key = row['staging_key']
            store = self.storage()
            head = store.head(key)
            if not head and not row['multipart_id'] and start:
                existing = store.multipart_uploads(key)
                upload = existing[0] if existing else store.multipart_start(key)
                # Session lock prevents competing init/abort; autocommit makes the reference durable immediately.
                conn.execute('UPDATE rav_pilot_upload SET multipart_id=%s WHERE id=%s', (upload, upload_id))
                row['multipart_id'] = upload
                for extra in existing[1:]:
                    store.multipart_abort(key, extra)
            parts = store.multipart_parts(key, row['multipart_id']) if row['multipart_id'] and not head else []
            chunk, count = layout(row['declared_bytes'])
            return {'part_size': chunk, 'part_count': count, 'assembled': head is not None,
                    'parts': [{'part_number': p['PartNumber'], 'size_bytes': p['Size']} for p in parts]}

    def sign(self, actor, upload_id, numbers):
        with upload_lock(self.db, upload_id) as conn:
            row = self._row(conn, actor, upload_id)
            if not row['multipart_id']:
                raise ServiceError('upload_invalid', 'khởi tạo multipart trước', 409)
            chunk, count = layout(row['declared_bytes'])
            if not numbers or len(numbers) > 16 or len(set(numbers)) != len(numbers) or any(type(n) is not int or not 1 <= n <= count for n in numbers):
                raise ServiceError('validation_error', 'part numbers không hợp lệ', 422)
            store = self.storage()
            return {'parts': [{'part_number': n, 'url': store.multipart_sign(row['staging_key'], row['multipart_id'], n,
                       min(chunk, row['declared_bytes']-(n-1)*chunk), min(900, row['ttl']))} for n in numbers]}

    def assemble(self, actor, upload_id):
        with upload_lock(self.db, upload_id) as conn:
            row = self._row(conn, actor, upload_id)
            store = self.storage()
            if store.head(row['staging_key']) is not None:
                return   # lost response after CompleteMultipartUpload
            if not row['multipart_id']:
                raise ServiceError('upload_invalid', 'chưa khởi tạo multipart', 409)
            parts = sorted(store.multipart_parts(row['staging_key'], row['multipart_id']), key=lambda p: p['PartNumber'])
            chunk, count = layout(row['declared_bytes'])
            if len(parts) != count or any(p['PartNumber'] != i+1 or p['Size'] != min(chunk, row['declared_bytes']-i*chunk)
                                          for i, p in enumerate(parts)):
                raise ServiceError('upload_invalid', 'chưa đủ parts hoặc dung lượng part không khớp', 409)
            store.multipart_complete(row['staging_key'], row['multipart_id'], parts)

    def abort_bytes(self, key):
        self.storage().abort_multipart_key(key)
