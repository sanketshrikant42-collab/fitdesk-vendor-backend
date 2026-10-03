"""Cloudflare R2 access (S3-compatible API via boto3).

The R2 credentials (R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY) exist ONLY
in this backend's environment (Render secrets). They are never sent to
the desktop app, never returned by any API, never logged, and never
placed in an exception message that could reach a response.

What the desktop gets instead is a presigned URL: valid for one HTTP
method on one exact object key for a few minutes. It cannot list the
bucket, cannot touch any other key, and expires on its own.

Every error leaving this module is one of the StorageError subclasses
below with a fixed, safe message -- the underlying botocore exception is
logged by its error *code* only.
"""

import hashlib
import logging
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger("cloud_backup")


class StorageError(Exception):
    code = "storage_error"
    safe_message = "Cloud storage request failed."

    def __init__(self, detail_code=""):
        super().__init__(self.safe_message)
        self.detail_code = detail_code


class StorageNotConfigured(StorageError):
    code = "storage_not_configured"
    safe_message = "Cloud backup storage is not configured on the server."


class StorageAuthError(StorageError):
    code = "storage_auth_error"
    safe_message = "Cloud storage rejected the server's credentials."


class StorageUnavailable(StorageError):
    code = "storage_unavailable"
    safe_message = "Cloud storage is temporarily unavailable."


class ObjectNotFound(StorageError):
    code = "object_not_found"
    safe_message = "The backup file was not found in cloud storage."


@dataclass
class ObjectInfo:
    size: int
    metadata: dict
    etag: str = ""


_AUTH_CODES = {
    "InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied",
    "InvalidToken", "ExpiredToken", "Unauthorized", "403",
}
_NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}


def _translate(exc):
    """Map a boto/botocore exception onto a safe StorageError."""
    try:
        from botocore.exceptions import (
            ClientError, EndpointConnectionError, ConnectTimeoutError,
            ReadTimeoutError, NoCredentialsError, PartialCredentialsError,
        )
    except ImportError:  # pragma: no cover
        return StorageUnavailable()

    if isinstance(exc, StorageError):
        return exc
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        return StorageNotConfigured("no_credentials")
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return StorageUnavailable(type(exc).__name__)
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _NOT_FOUND_CODES:
            return ObjectNotFound(code)
        if code in _AUTH_CODES:
            return StorageAuthError(code)
        return StorageUnavailable(code or "client_error")
    return StorageUnavailable(type(exc).__name__)


def r2_settings():
    account = getattr(settings, "R2_ACCOUNT_ID", "") or ""
    endpoint = getattr(settings, "R2_ENDPOINT", "") or ""
    if not endpoint and account:
        endpoint = f"https://{account}.r2.cloudflarestorage.com"
    return {
        "endpoint": endpoint,
        "access_key": getattr(settings, "R2_ACCESS_KEY_ID", "") or "",
        "secret_key": getattr(settings, "R2_SECRET_ACCESS_KEY", "") or "",
        "bucket": getattr(settings, "R2_BUCKET_NAME", "") or "",
    }


class R2Storage:
    """Thin wrapper over a boto3 S3 client pointed at R2."""

    def __init__(self):
        cfg = r2_settings()
        if not (cfg["endpoint"] and cfg["access_key"] and cfg["secret_key"] and cfg["bucket"]):
            raise StorageNotConfigured("missing_settings")
        import boto3
        from botocore.config import Config

        self.bucket = cfg["bucket"]
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg["endpoint"],
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret_key"],
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        )

    # -- presigned URLs (no network call; pure local signing) -------------
    def presign_put(self, key, metadata, expires_in, content_md5_b64=None):
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ContentType": "application/octet-stream",
            "Metadata": metadata,
        }
        if content_md5_b64:
            # Signed header: the uploader must send exactly this
            # Content-MD5, and R2 rejects a body that doesn't match it.
            params["ContentMD5"] = content_md5_b64
        try:
            return self._client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expires_in,
                HttpMethod="PUT",
            )
        except Exception as exc:
            raise _translate(exc)

    def presign_get(self, key, expires_in):
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
        except Exception as exc:
            raise _translate(exc)

    # -- server-side operations ------------------------------------------
    def head(self, key):
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise _translate(exc)
        meta = {str(k).lower(): v for k, v in (resp.get("Metadata") or {}).items()}
        return ObjectInfo(size=int(resp.get("ContentLength", -1)), metadata=meta,
                          etag=str(resp.get("ETag", "")).strip('"').lower())

    def delete(self, key):
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            err = _translate(exc)
            if isinstance(err, ObjectNotFound):
                return  # already gone == success
            raise err

    def sha256_of(self, key, chunk_size=1024 * 1024):
        """Streams the object and hashes it server-side (used by the
        explicit Verify action). Never holds the whole object in memory."""
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
            body = resp["Body"]
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: body.read(chunk_size), b""):
                digest.update(chunk)
                size += len(chunk)
            return digest.hexdigest(), size
        except Exception as exc:
            raise _translate(exc)

    def put_bytes(self, key, data, metadata=None):
        """Used only by the r2_selftest management command."""
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data, Metadata=metadata or {})
        except Exception as exc:
            raise _translate(exc)

    def get_bytes(self, key):
        """Used only by the r2_selftest management command."""
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            raise _translate(exc)

    def list_keys(self, prefix, max_keys=100):
        """Used only by the r2_selftest command (throwaway prefix). Works
        with a bucket-scoped "Object Read & Write" token, unlike HeadBucket."""
        try:
            resp = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=max_keys)
        except Exception as exc:
            raise _translate(exc)
        return [o["Key"] for o in resp.get("Contents", [])]


_storage_override = None


def set_storage_for_tests(storage):
    """Tests inject an in-memory fake here; production never calls this."""
    global _storage_override
    _storage_override = storage


def get_storage():
    if _storage_override is not None:
        return _storage_override
    return R2Storage()


def is_configured():
    cfg = r2_settings()
    return bool(cfg["endpoint"] and cfg["access_key"] and cfg["secret_key"] and cfg["bucket"])
