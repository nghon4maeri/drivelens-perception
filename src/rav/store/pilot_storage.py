"""S3 riêng tư và kế toán dung lượng của pilot (CONTRACTS §21.7, §21.12; runtime spec §3.3, §6.3).

- `PilotStorage`: bucket + prefix tường minh (env `RAV_S3_BUCKET`, `RAV_PILOT_S3_PREFIX`; credential theo chuỗi mặc định của boto3 —
  biến môi trường đã inject trên host). Key chỉ do server dựng; presigned POST/GET; tải có giới hạn + SHA-256; PUT kèm ChecksumSHA256.
- Hàm kế toán (nhận cursor của transaction caller): tài khoản `USER:<uuid>`/`GLOBAL`, hold có receipt; persisted + reserved ≤ limit.
Lỗi S3 → 503 `dependency_unavailable` (thông điệp an toàn, không lộ key/credential).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..errors import ServiceError
from ..timing import timing

_PREFIX_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CHUNK = 1 << 20
MULTIPART_THRESHOLD = 100 << 20
MULTIPART_CHUNK = 16 << 20


def _unavailable(what: str) -> ServiceError:
    return ServiceError("dependency_unavailable", f"lưu trữ S3 tạm thời không dùng được ({what})", 503)


def _part(value: Any) -> str:
    s = str(value)
    if not _SAFE_PART.match(s) or ".." in s:
        raise ServiceError("validation_error", "thành phần key không hợp lệ", 422)
    return s


class PilotStorage:
    def __init__(self, client: Any, bucket: str, prefix: str):
        if not bucket:
            raise ServiceError("dependency_unavailable", "thiếu RAV_S3_BUCKET", 503)
        if not prefix or not _PREFIX_OK.match(prefix) or ".." in prefix:
            raise ServiceError("dependency_unavailable", "thiếu hoặc sai RAV_PILOT_S3_PREFIX (không dùng root bucket)", 503)
        self.client, self.bucket, self.prefix = client, bucket, prefix

    @classmethod
    def from_env(cls) -> PilotStorage:
        bucket, prefix = os.environ.get("RAV_S3_BUCKET", ""), os.environ.get("RAV_PILOT_S3_PREFIX", "")
        if not bucket or not prefix:
            raise ServiceError("dependency_unavailable", "thiếu RAV_S3_BUCKET hoặc RAV_PILOT_S3_PREFIX", 503)
        import boto3
        from botocore.config import Config

        client = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("RAV_S3_REGION"),
                              config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=60,
                                            retries={"max_attempts": 3, "mode": "standard"}))
        return cls(client, bucket, prefix)

    # ---- key (runtime spec §6.3) ----
    def staging_key(self, owner: UUID, upload: UUID) -> str:
        return f"{self.prefix}/staging/{_part(owner)}/{_part(upload)}/video"

    def private_raw_key(self, owner: UUID, source: UUID, sha256: str, ext: str) -> str:
        return f"{self.prefix}/private/{_part(owner)}/{_part(source)}/raw/{_part(sha256)}/video.{_part(ext)}"

    def artifact_key(self, owner: UUID, artifact: UUID, sha256: str) -> str:
        return f"{self.prefix}/private/{_part(owner)}/artifacts/{_part(artifact)}/{_part(sha256)}"

    def frame_key(self, owner: UUID, frame_set: UUID, frame_idx: int, variant: str, sha256: str) -> str:
        """Ảnh lưu sẵn (§21.20): `frame_set` mới mỗi attempt nên retry không ghi đè object đã được nhận."""
        return (f"{self.prefix}/private/{_part(owner)}/frames/{_part(frame_set)}/{int(frame_idx)}/{_part(variant.lower())}/"
                f"{_part(sha256)}.jpg")

    def system_frame_key(self, seed_version: str, core_source_id: str, frame_set: UUID, frame_idx: int, variant: str,
                         sha256: str) -> str:
        return (f"{self.prefix}/system/{_part(seed_version)}/frames/{_part(core_source_id)}/{_part(frame_set)}/{int(frame_idx)}/"
                f"{_part(variant.lower())}/{_part(sha256)}.jpg")

    def system_raw_key(self, seed_version: str, core_source_id: str, sha256: str, ext: str) -> str:
        return f"{self.prefix}/system/{_part(seed_version)}/sources/{_part(core_source_id)}/raw/{_part(sha256)}/video.{_part(ext)}"

    def job_key(self, job: UUID, attempt: UUID, name: str) -> str:
        """Object trao đổi với worker GPU từ xa (progress/result) theo attempt; xóa theo tên cụ thể, không quét prefix."""
        return f"{self.prefix}/jobs/{_part(job)}/{_part(attempt)}/{_part(name)}"

    def system_model_key(self, model: str, sha256: str, suffix: str) -> str:
        if suffix and not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            raise ServiceError("validation_error", "đuôi file model không hợp lệ", 422)
        return f"{self.prefix}/system/models/{_part(model)}/{_part(sha256)}{suffix}"

    def system_feature_key(self, seed_version: str, core_source_id: str, feature: str, sha256: str) -> str:
        return (f"{self.prefix}/system/{_part(seed_version)}/features/{_part(core_source_id)}/{_part(feature)}/"
                f"{_part(sha256)}.npz")

    def _own(self, key: str) -> str:
        if not key.startswith(self.prefix + "/") or ".." in key:
            raise ServiceError("validation_error", "key ngoài namespace pilot", 422)
        return key

    # ---- S3 ----
    def presign_post(self, key: str, max_bytes: int, ttl_s: int) -> dict:
        try:
            post = self.client.generate_presigned_post(self.bucket, self._own(key),
                                                       Conditions=[["content-length-range", 1, max_bytes]], ExpiresIn=ttl_s)
        except Exception:
            raise _unavailable("cấp URL tải lên") from None
        return {"method": "POST", "url": post["url"], "fields": post["fields"],
                "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl_s)).isoformat()}

    def presign_get(self, key: str, ttl_s: int) -> str:
        try:
            return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": self._own(key)},
                                                      ExpiresIn=ttl_s)
        except Exception:
            raise _unavailable("cấp URL xem") from None

    def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        """URL PUT một object cụ thể cho worker GPU từ xa (không credential AWS ở remote)."""
        try:
            return self.client.generate_presigned_url("put_object", Params={"Bucket": self.bucket, "Key": self._own(key),
                                                                            "ContentType": content_type}, ExpiresIn=ttl_s)
        except Exception:
            raise _unavailable("cấp URL ghi") from None

    def read_json(self, key: str, limit: int = 1 << 20) -> Any:
        """None nếu object chưa có; JSON quá giới hạn hoặc hỏng → 422 `result_invalid`."""
        import json

        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self._own(key))["Body"]
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise _unavailable("đọc object") from None
        with body as src:
            raw = src.read(limit + 1)
        if len(raw) > limit:
            raise ServiceError("result_invalid", "progress vượt giới hạn", 422)
        try:
            return json.loads(raw)
        except ValueError:
            raise ServiceError("result_invalid", "progress không phải JSON", 422) from None

    def head(self, key: str) -> dict | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self._own(key))
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise _unavailable("kiểm object") from None

    def copy_verified(self, source: str, target: str, sha: str, size: int, callback=None) -> None:
        """Server-side bounded multipart copy of an immutable canonical object, guarded by source ETag."""
        from boto3.s3.transfer import TransferConfig

        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self._own(source), ChecksumMode='ENABLED')
        except Exception:
            raise _unavailable('kiểm checksum object nguồn') from None
        if not head or head['ContentLength'] != size or head.get('Metadata', {}).get('sha256', sha) != sha:
            raise ServiceError('result_invalid', 'object nguồn không khớp bản đã chuẩn bị', 422)
        checksum = head.get('ChecksumSHA256')
        if checksum and head.get('ChecksumType') != 'COMPOSITE' and '-' not in checksum:
            if checksum != base64.b64encode(bytes.fromhex(sha)).decode():
                raise ServiceError('result_invalid', 'checksum nguồn không khớp artifact đã chuẩn bị', 422)
        try:
            self.client.copy({'Bucket': self.bucket, 'Key': self._own(source)}, self.bucket, self._own(target),
                ExtraArgs={'CopySourceIfMatch': head['ETag'], 'MetadataDirective': 'REPLACE',
                           'Metadata': {'sha256': sha}, 'ContentType': head.get('ContentType', 'application/octet-stream')},
                Config=TransferConfig(multipart_threshold=128 << 20, multipart_chunksize=64 << 20, max_concurrency=2),
                Callback=callback)
        except ServiceError:
            raise
        except Exception:
            raise _unavailable('sao chép object') from None
        copied = self.head(target)
        if not copied or copied['ContentLength'] != size or copied.get('Metadata', {}).get('sha256') != sha:
            raise ServiceError('result_invalid', 'bản sao không khớp kích thước/SHA metadata', 422)

    def download(self, key: str, dest: Path, max_bytes: int, *, stop=None) -> tuple[str, int]:
        """Tải có giới hạn; trả (sha256, số byte). Vượt giới hạn → 422 `upload_invalid` (không ghi phần dư)."""
        digest, count = hashlib.sha256(), 0
        try:
            if stop is not None and stop.is_set():
                raise ServiceError('cancel_requested', 'đã yêu cầu dừng tải dữ liệu', 409)
            body = self.client.get_object(Bucket=self.bucket, Key=self._own(key))["Body"]
        except ServiceError:
            raise
        except Exception:
            raise _unavailable("tải object") from None
        try:
            with body as src, dest.open("wb") as out:
                while True:
                    if stop is not None and stop.is_set():
                        raise ServiceError('cancel_requested', 'đã yêu cầu dừng tải dữ liệu', 409)
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    if stop is not None and stop.is_set():
                        raise ServiceError('cancel_requested', 'đã yêu cầu dừng tải dữ liệu', 409)
                    count += len(chunk)
                    if count > max_bytes:
                        raise ServiceError("upload_invalid", f"file vượt {max_bytes} byte", 422)
                    digest.update(chunk)
                    out.write(chunk)
        except ServiceError:
            dest.unlink(missing_ok=True)
            raise
        except Exception:
            dest.unlink(missing_ok=True)
            raise _unavailable("tải object") from None
        return digest.hexdigest(), count

    def put_file(self, key: str, path: Path, sha256: str, content_type: str) -> None:
        """PUT kèm ChecksumSHA256 (S3 tự từ chối nếu byte khác) rồi HEAD đối chiếu kích thước."""
        size = path.stat().st_size
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        try:
            if size >= MULTIPART_THRESHOLD:
                from boto3.s3.transfer import TransferConfig
                self.client.upload_file(str(path), self.bucket, self._own(key),
                                        ExtraArgs={'ContentType': content_type, 'Metadata': {'sha256': sha256}, 'ChecksumAlgorithm': 'SHA256'},
                                        Config=TransferConfig(multipart_threshold=MULTIPART_THRESHOLD, multipart_chunksize=MULTIPART_CHUNK,
                                                              max_concurrency=4))
            else:
                with path.open("rb") as f:
                    self.client.put_object(Bucket=self.bucket, Key=self._own(key), Body=f, ContentType=content_type,
                                           ChecksumSHA256=checksum)
        except Exception:
            raise _unavailable("ghi object") from None
        head = self.head(key)
        if not head or head["ContentLength"] != size:
            raise _unavailable("object ghi xong nhưng kích thước không khớp")
        if size >= MULTIPART_THRESHOLD and head.get('Metadata', {}).get('sha256') != sha256:
            raise _unavailable('metadata SHA canonical không khớp')

    def put_bytes(self, key: str, data: bytes, sha256: str, content_type: str) -> None:
        """PUT byte trực tiếp kèm ChecksumSHA256 rồi HEAD đối chiếu kích thước."""
        size = len(data)
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        try:
            self.client.put_object(Bucket=self.bucket, Key=self._own(key), Body=data, ContentType=content_type,
                                   ChecksumSHA256=checksum)
        except Exception:
            raise _unavailable("ghi object") from None
        head = self.head(key)
        if not head or head["ContentLength"] != size:
            raise _unavailable("object ghi xong nhưng kích thước không khớp")

    def multipart_uploads(self, key: str) -> list[str]:
        """Recover/clean MPU only for one server-owned exact key, never a prefix delete."""
        try:
            out = []
            for page in self.client.get_paginator('list_multipart_uploads').paginate(Bucket=self.bucket, Prefix=self._own(key)):
                out.extend(u['UploadId'] for u in page.get('Uploads', []) if u['Key'] == key)
            return out
        except Exception:
            raise _unavailable('liệt kê multipart') from None

    def multipart_start(self, key: str) -> str:
        try:
            return self.client.create_multipart_upload(Bucket=self.bucket, Key=self._own(key), ContentType='application/octet-stream')['UploadId']
        except Exception:
            raise _unavailable('khởi tạo multipart') from None

    def multipart_parts(self, key: str, upload: str) -> list[dict]:
        try:
            return [p for page in self.client.get_paginator('list_parts').paginate(Bucket=self.bucket, Key=self._own(key), UploadId=upload)
                    for p in page.get('Parts', [])]
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') == 'NoSuchUpload':
                return []
            raise _unavailable('đọc multipart parts') from None

    def multipart_sign(self, key: str, upload: str, number: int, size: int, ttl: int) -> str:
        try:
            return self.client.generate_presigned_url('upload_part', Params={'Bucket': self.bucket, 'Key': self._own(key),
                'UploadId': upload, 'PartNumber': number, 'ContentLength': size}, ExpiresIn=ttl)
        except Exception:
            raise _unavailable('ký multipart part') from None

    def multipart_complete(self, key: str, upload: str, parts: list[dict]) -> None:
        try:
            self.client.complete_multipart_upload(Bucket=self.bucket, Key=self._own(key), UploadId=upload,
                MultipartUpload={'Parts': [{'PartNumber': p['PartNumber'], 'ETag': p['ETag']} for p in parts]})
        except Exception:
            raise _unavailable('hoàn tất multipart') from None

    def multipart_abort(self, key: str, upload: str) -> None:
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=self._own(key), UploadId=upload)
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'NoSuchUpload':
                raise _unavailable('hủy multipart') from None

    def abort_multipart_key(self, key: str) -> None:
        for upload in self.multipart_uploads(key):
            self.multipart_abort(key, upload)
        if self.multipart_uploads(key):
            raise _unavailable('multipart chưa sạch')

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._own(key))
        except Exception:
            raise _unavailable("xóa object") from None


# ------------------------------------------------------------------------------
# Kế toán dung lượng (gọi trong transaction của caller, đã khóa profile)
# ------------------------------------------------------------------------------
def _accounts(cur, owner: UUID, policy: dict, *, scope: str = "PRIVATE") -> list[str]:
    keys = ["GLOBAL"] if scope == "SYSTEM" else [f"USER:{owner}", "GLOBAL"]
    limits = {f"USER:{owner}": policy["storage_user_bytes"], "GLOBAL": policy["storage_global_bytes"]}
    for k in keys:   # giới hạn theo policy hiện hành; không hạ dưới số đang dùng
        cur.execute("INSERT INTO rav_pilot_storage_account (account_key, limit_bytes) VALUES (%s, %s) "
                    "ON CONFLICT (account_key) DO UPDATE SET limit_bytes = GREATEST(EXCLUDED.limit_bytes, "
                    "rav_pilot_storage_account.persisted_bytes + rav_pilot_storage_account.reserved_bytes)",
                    (k, limits[k]))
    cur.execute("SELECT * FROM rav_pilot_storage_account WHERE account_key = ANY(%s) ORDER BY account_key FOR UPDATE", (keys,))
    return keys


def reserve(cur, owner: UUID, policy: dict, nbytes: int, receipt_key: str, *, job_id: UUID | None = None,
            upload_id: UUID | None = None, scope: str = "PRIVATE") -> UUID:
    """Giữ `nbytes` trên USER và GLOBAL (hoặc chỉ GLOBAL khi scope='SYSTEM'); vượt giới hạn → 429 `storage_exceeded`. Trả id hold."""
    storage_enabled = policy.get("storage_accounting_enabled", True)
    account_scope = "GLOBAL_ONLY" if scope == "SYSTEM" else "USER_AND_GLOBAL"
    if storage_enabled:
        keys = _accounts(cur, owner, policy, scope=scope)
        cur.execute("SELECT account_key, limit_bytes - persisted_bytes - reserved_bytes AS free FROM rav_pilot_storage_account "
                    "WHERE account_key = ANY(%s)", (keys,))
        for row in cur.fetchall():
            if row["free"] < nbytes:
                raise ServiceError("storage_exceeded", f"hết dung lượng ({row['account_key'].split(':')[0]})", 429)
        cur.execute("UPDATE rav_pilot_storage_account SET reserved_bytes = reserved_bytes + %s WHERE account_key = ANY(%s)",
                    (nbytes, keys))
    hold = uuid4()
    cur.execute("INSERT INTO rav_pilot_storage_hold (id, owner_id, job_id, upload_id, reserved_bytes, receipt_key, "
                "storage_accounting_enabled, account_scope) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (hold, owner, job_id, upload_id, nbytes, receipt_key, storage_enabled, account_scope))
    return hold


def settle(cur, hold_id: UUID, *, persist: int = 0, release: int = 0, final: bool = False) -> None:
    """Chuyển phần giữ sang persisted và/hoặc trả lại; không vượt phần còn giữ."""
    cur.execute("SELECT * FROM rav_pilot_storage_hold WHERE id = %s FOR UPDATE", (hold_id,))
    h = cur.fetchone()
    if h is None or h["state"] == "FINAL":
        return
    left = h["reserved_bytes"] - h["persisted_bytes"] - h["released_bytes"]
    persist = min(persist, left)
    release = left - persist if final else min(release, left - persist)
    storage_enabled = h.get("storage_accounting_enabled", True)
    if storage_enabled:
        keys = ["GLOBAL"] if h.get("account_scope") == "GLOBAL_ONLY" else [f"USER:{h['owner_id']}", "GLOBAL"]
        cur.execute("SELECT 1 FROM rav_pilot_storage_account WHERE account_key = ANY(%s) ORDER BY account_key FOR UPDATE", (keys,))
        with timing("db.pipeline"), cur.connection.pipeline():
            cur.execute("UPDATE rav_pilot_storage_account SET reserved_bytes = reserved_bytes - %s, persisted_bytes = persisted_bytes + %s "
                        "WHERE account_key = ANY(%s)", (persist + release, persist, keys))
            cur.execute("UPDATE rav_pilot_storage_hold SET persisted_bytes = persisted_bytes + %s, released_bytes = released_bytes + %s, "
                        "state = %s WHERE id = %s", (persist, release, "FINAL" if final else "OPEN", hold_id))
    else:
        cur.execute("UPDATE rav_pilot_storage_hold SET persisted_bytes = persisted_bytes + %s, released_bytes = released_bytes + %s, "
                    "state = %s WHERE id = %s", (persist, release, "FINAL" if final else "OPEN", hold_id))


def release_job_holds(cur, job_id: UUID, persisted: int = 0) -> None:
    """Hold output của job: ghi `persisted` byte thật (phần vượt hold vẫn được tính, vượt giới hạn → 429), trả phần dư, đóng hold."""
    from .pilot_objects import pending_in_tx

    if pending_in_tx(cur, job_id=job_id):
        return
    cur.execute("SELECT * FROM rav_pilot_storage_hold WHERE job_id = %s AND upload_id IS NULL AND state = 'OPEN' FOR UPDATE",
                (job_id,))
    left = persisted
    for h in cur.fetchall():
        take = min(left, h["reserved_bytes"] - h["persisted_bytes"] - h["released_bytes"])
        settle(cur, h["id"], persist=take, final=True)
        left -= take
    if left > 0:
        cur.execute("SELECT j.owner_id, j.payload, q.config FROM rav_pilot_job j JOIN rav_pilot_quota_policy q ON q.version = j.policy_version "
                    "WHERE j.id = %s", (job_id,))
        row = cur.fetchone()
        if row["config"].get("storage_accounting_enabled", True):
            job_scope = (row.get("payload") or {}).get("target_scope", "PRIVATE")
            keys = _accounts(cur, row["owner_id"], row["config"], scope=job_scope)
            cur.execute("SELECT 1 FROM rav_pilot_storage_account WHERE account_key = ANY(%s) "
                        "AND persisted_bytes + reserved_bytes + %s > limit_bytes", (keys, left))
            if cur.fetchone():
                raise ServiceError("storage_exceeded", "kết quả vượt dung lượng còn lại", 429)
            cur.execute("UPDATE rav_pilot_storage_account SET persisted_bytes = persisted_bytes + %s WHERE account_key = ANY(%s)",
                        (left, keys))


def persist_global_in_tx(cur, policy: dict, nbytes: int) -> None:
    """Byte của dữ liệu SYSTEM do operator ghi (ảnh lưu sẵn §21.20): chỉ tính `GLOBAL`; vượt giới hạn → 429, không ghi."""
    if policy.get("storage_accounting_enabled", True) is False:
        return
    cur.execute("INSERT INTO rav_pilot_storage_account (account_key, limit_bytes) VALUES ('GLOBAL', %s) "
                "ON CONFLICT (account_key) DO NOTHING", (policy["storage_global_bytes"],))
    cur.execute("SELECT * FROM rav_pilot_storage_account WHERE account_key = 'GLOBAL' FOR UPDATE")
    acct = cur.fetchone()
    if acct["persisted_bytes"] + acct["reserved_bytes"] + nbytes > acct["limit_bytes"]:
        raise ServiceError("storage_exceeded", "hết dung lượng (GLOBAL)", 429)
    cur.execute("UPDATE rav_pilot_storage_account SET persisted_bytes = persisted_bytes + %s WHERE account_key = 'GLOBAL'", (nbytes,))


def persist_job_bytes(cur, job_id: UUID, nbytes: int) -> None:
    """Chuyển `nbytes` đã giữ trên hold output của job sang persisted, không đóng hold; phần dư trả khi job hết receipt chờ.
    Byte ảnh luôn được giữ trước PUT theo lô (§21.20) nên thiếu phần giữ là kết quả sai, không tính vượt."""
    cur.execute("SELECT * FROM rav_pilot_storage_hold WHERE job_id = %s AND upload_id IS NULL AND state = 'OPEN' "
                "ORDER BY created_at, id FOR UPDATE", (job_id,))
    left = nbytes
    for h in cur.fetchall():
        take = min(left, h["reserved_bytes"] - h["persisted_bytes"] - h["released_bytes"])
        if take > 0:
            settle(cur, h["id"], persist=take)
            left -= take
    if left > 0:
        raise ServiceError("result_invalid", "byte ảnh vượt phần đã giữ", 422)
