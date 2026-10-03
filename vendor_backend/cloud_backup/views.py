"""Cloud Backup API for the FitDesk Pro desktop app.

Conventions match the rest of this backend (licensing/views.py):
POST + JSON body carrying {"license_key", "machine_id"}; csrf_exempt
because the caller is a desktop app, not a browser session.

AUTHORIZATION -- every endpoint, no exceptions:
  1. license_key + machine_id are resolved through
     licensing.license_utils.resolve_active_client_and_license -- the
     SAME front door the WhatsApp endpoints use (active, unexpired,
     unsuspended license; device registered and not blocked; device
     limit enforced).
  2. The gym (Client) is ALWAYS taken from that resolved license. No
     endpoint accepts a gym_id / client_id from the request; a
     "gym_id" field in a body is simply ignored.
  3. Every backup lookup is filtered by that Client, so another gym's
     backup_id is indistinguishable from one that doesn't exist (404).
  4. Uploading requires the `cloud_backup` feature entitlement.
     Listing/downloading/deleting EXISTING backups only requires an
     active license, so a gym that downgrades can still get its data
     back.

Responses never contain R2 credentials, never contain another gym's
data, and error messages are fixed strings (no exception text).
"""

import base64
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from licensing.license_utils import resolve_active_client_and_license

from . import keys as backup_keys
from . import services
from . import storage as storage_mod
from .models import CloudBackup, gym_ref_for
from .storage import ObjectNotFound, StorageError, get_storage

logger = logging.getLogger("cloud_backup")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MD5_B64_RE = re.compile(r"^[A-Za-z0-9+/]{22}==$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z.\-_+]{0,32}$")


def _json_body(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _error(code, message, status):
    return JsonResponse({"ok": False, "error_code": code, "message": message}, status=status)


_LICENSE_ERROR_STATUS = {
    "invalid_license": 401,
    "machine_mismatch": 403,
    "device_limit_reached": 403,
}


def _resolve(request):
    """Returns (data, client, license, device, error_response)."""
    data = _json_body(request)
    client, lic, err = resolve_active_client_and_license(
        data.get("license_key"), data.get("machine_id"), require_machine_id=True,
    )
    if err:
        return data, None, None, None, _error(err["error_code"], err["message"], _LICENSE_ERROR_STATUS.get(err["error_code"], 403))
    device = lic.devices.filter(fingerprint=(data.get("machine_id") or "").strip()).first()
    return data, client, lic, device, None


def _parse_backup_id(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _get_own_backup(client, backup_id):
    """The ONLY way views look a backup up -- always scoped to the
    caller's own Client."""
    bid = _parse_backup_id(backup_id)
    if bid is None:
        return None
    return CloudBackup.objects.filter(client=client, backup_id=bid).first()


def _storage_error(exc):
    # Always 503: from the desktop's point of view every storage-side
    # failure is "cloud temporarily unavailable, keep the backup local and
    # retry later". The specific code is still in error_code.
    return _error(exc.code, exc.safe_message, 503)


def _serialize(b):
    return {
        "backup_id": str(b.backup_id),
        "file_name": b.file_name,
        "file_size": b.file_size,
        "checksum_sha256": b.checksum_sha256,
        "backup_type": b.backup_type,
        "backup_created_at": b.backup_created_at.isoformat(),
        "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        "verified_at": b.verified_at.isoformat() if b.verified_at else None,
        "application_version": b.application_version,
        "database_version": b.database_version,
        "backup_format_version": b.backup_format_version,
        "encryption_algorithm": b.encryption_algorithm,
        "encryption_key_id": b.encryption_key_id,
        "status": b.status,
    }


def _parse_created_at(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    # Refuse wildly wrong client clocks (keeps object keys sane).
    now = timezone.now()
    if dt > now + timedelta(days=2) or dt.year < 2024:
        return None
    return dt


# ======================================================================
# STATUS / KEY
# ======================================================================

@csrf_exempt
@require_POST
def cloud_backup_status(request):
    """Settings screen: is cloud backup available for this gym, and what
    is stored. Always cheap -- no R2 network call."""
    data, client, lic, device, err = _resolve(request)
    if err:
        return err

    available = CloudBackup.objects.filter(client=client, status=CloudBackup.STATUS_AVAILABLE)
    latest = available.order_by("-backup_created_at").first()
    total = sum(available.values_list("file_size", flat=True))
    return JsonResponse({
        "ok": True,
        "gym_ref": gym_ref_for(client),
        "entitled": services.is_entitled(lic),
        "configured": storage_mod.is_configured() and backup_keys.is_configured(),
        "backup_count": available.count(),
        "total_bytes": total,
        "last_backup_at": latest.backup_created_at.isoformat() if latest else None,
        "last_verified_at": latest.verified_at.isoformat() if latest and latest.verified_at else None,
        "retention": services.retention_limits(),
        "max_file_bytes": services.max_file_bytes(),
    })


@csrf_exempt
@require_POST
def backup_key(request):
    """Returns this gym's backup encryption key to a validated device.

    The key is what makes a cloud restore onto a REPLACEMENT PC possible
    (the old PC's cached copy died with it). It is returned only over
    TLS, only to an active license on a registered, unblocked device of
    this same gym, and is rate-limited + audited. It is never logged."""
    data, client, lic, device, err = _resolve(request)
    if err:
        return err
    if services.is_rate_limited(lic, "cloud_backup.key_requested"):
        return _error("rate_limited", "Too many requests. Please try again later.", 429)
    try:
        key_id, raw = backup_keys.get_or_create_gym_key(client)
    except backup_keys.BackupKeyServiceNotConfigured:
        logger.error("backup key requested but BACKUP_KEY_ENCRYPTION_KEY is not configured")
        return _error("key_service_not_configured", "Cloud backup is not configured on the server yet.", 503)
    except backup_keys.BackupKeyUnreadable:
        logger.error("stored backup key unreadable gym=%s", gym_ref_for(client))
        return _error("key_unreadable", "The gym's backup key could not be read. Contact support.", 500)
    services.audit("cloud_backup.key_requested", lic, gym=gym_ref_for(client), device=device.pk if device else None)
    return JsonResponse({
        "ok": True,
        "gym_ref": gym_ref_for(client),
        "key_id": key_id,
        "key": backup_keys.encode_key(raw),
        "algorithm": "AES-256-GCM",
    })


# ======================================================================
# UPLOAD
# ======================================================================

@csrf_exempt
@require_POST
def request_upload(request):
    data, client, lic, device, err = _resolve(request)
    if err:
        return err

    if not services.is_entitled(lic):
        return _error("not_entitled", "Cloud backup is not included in this license's plan.", 403)
    try:
        storage = get_storage()
    except StorageError as exc:
        return _storage_error(exc)
    if services.is_rate_limited(lic, "cloud_backup.upload_requested"):
        return _error("rate_limited", "Too many upload requests. Please try again later.", 429)

    backup_id = _parse_backup_id(data.get("backup_id"))
    checksum = str(data.get("checksum_sha256") or "").lower()
    backup_type = data.get("backup_type") or CloudBackup.TYPE_SCHEDULED
    created_at = _parse_created_at(data.get("created_at"))
    app_version = str(data.get("application_version") or "")
    db_version = str(data.get("database_version") or "")
    key_id = str(data.get("encryption_key_id") or "")
    content_md5_b64 = str(data.get("content_md5") or "")
    try:
        file_size = int(data.get("file_size"))
        fmt_version = int(data.get("backup_format_version") or 1)
    except (TypeError, ValueError):
        return _error("invalid_request", "file_size and backup_format_version must be integers.", 400)

    if backup_id is None:
        return _error("invalid_request", "backup_id must be a UUID.", 400)
    if not _SHA256_RE.match(checksum):
        return _error("invalid_request", "checksum_sha256 must be 64 lowercase hex characters.", 400)
    if content_md5_b64 and not _MD5_B64_RE.match(content_md5_b64):
        return _error("invalid_request", "content_md5 must be a base64-encoded MD5 digest.", 400)
    content_md5_hex = base64.b64decode(content_md5_b64).hex() if content_md5_b64 else ""
    if backup_type not in dict(CloudBackup.TYPE_CHOICES):
        return _error("invalid_request", "Unknown backup_type.", 400)
    if created_at is None:
        return _error("invalid_request", "created_at must be a valid ISO-8601 timestamp.", 400)
    if not (0 < file_size <= services.max_file_bytes()):
        return _error("file_too_large" if file_size > 0 else "invalid_request",
                      "Backup file size is outside the allowed range.", 413 if file_size > 0 else 400)
    if not (_VERSION_RE.match(app_version) and _VERSION_RE.match(db_version) and _VERSION_RE.match(key_id)):
        return _error("invalid_request", "Version/key id fields contain unsupported characters.", 400)

    # The backup must be encrypted with THIS gym's key -- refuse anything
    # else so a misconfigured desktop can't upload something nobody can
    # restore.
    gym_key = getattr(client, "backup_key", None)
    if gym_key is None or key_id != gym_key.key_id:
        return _error("wrong_encryption_key", "Backup is not encrypted with this gym's current backup key.", 409)

    with transaction.atomic():
        existing = CloudBackup.objects.select_for_update().filter(client=client, backup_id=backup_id).first()
        if existing is not None:
            if existing.status == CloudBackup.STATUS_AVAILABLE:
                return JsonResponse({"ok": True, "already_uploaded": True, "backup": _serialize(existing)})
            if existing.status in (CloudBackup.STATUS_DELETED, CloudBackup.STATUS_DELETION_PENDING):
                return _error("superseded", "This backup has already been rotated out by retention.", 409)
            if existing.checksum_sha256 != checksum or existing.file_size != file_size or (
                    content_md5_hex and existing.content_md5 and existing.content_md5 != content_md5_hex):
                return _error("conflict", "A different file was already registered under this backup_id.", 409)
            backup = existing
            backup.status = CloudBackup.STATUS_PENDING_UPLOAD
            if content_md5_hex:
                backup.content_md5 = content_md5_hex
        else:
            if services.is_superseded(client, backup_type, created_at):
                return _error("superseded", "Newer verified cloud backups already cover the retention window.", 409)
            object_key, file_name = services.build_object_key(client, created_at, backup_id)
            backup = CloudBackup(
                backup_id=backup_id, client=client, license=lic, device=device,
                backup_type=backup_type, file_name=file_name, file_size=file_size,
                checksum_sha256=checksum, content_md5=content_md5_hex, application_version=app_version,
                database_version=db_version, backup_format_version=fmt_version,
                encryption_key_id=key_id, backup_created_at=created_at, object_key=object_key,
            )
        backup.upload_requests += 1
        backup.last_upload_request_at = timezone.now()
        backup.last_error = ""
        backup.save()

    metadata = {"sha256": checksum, "backup-id": str(backup_id), "gym": gym_ref_for(client)}
    try:
        url = storage.presign_put(backup.object_key, metadata, services.presign_expiry_seconds(),
                                  content_md5_b64=content_md5_b64 or None)
    except StorageError as exc:
        logger.warning("presign_put failed gym=%s code=%s", gym_ref_for(client), exc.code)
        return _storage_error(exc)

    services.audit("cloud_backup.upload_requested", lic, backup_id=str(backup_id), size=file_size)
    logger.info("cloud upload requested gym=%s backup=%s size=%s", gym_ref_for(client), backup_id, file_size)
    return JsonResponse({
        "ok": True,
        "already_uploaded": False,
        "backup_id": str(backup_id),
        "file_name": backup.file_name,
        "upload_url": url,
        "upload_method": "PUT",
        "upload_headers": {
            "Content-Type": "application/octet-stream",
            "x-amz-meta-sha256": checksum,
            "x-amz-meta-backup-id": str(backup_id),
            "x-amz-meta-gym": gym_ref_for(client),
            **({"Content-MD5": content_md5_b64} if content_md5_b64 else {}),
        },
        "expires_in": services.presign_expiry_seconds(),
    })


@csrf_exempt
@require_POST
def complete_upload(request):
    """Desktop says "PUT finished". The server checks what is actually in
    R2 (size + the sha256 metadata that was part of the signed upload)
    before marking the backup available, then applies retention."""
    data, client, lic, device, err = _resolve(request)
    if err:
        return err
    if not services.is_entitled(lic):
        return _error("not_entitled", "Cloud backup is not included in this license's plan.", 403)

    backup = _get_own_backup(client, data.get("backup_id"))
    if backup is None:
        return _error("not_found", "Backup not found.", 404)
    if backup.status == CloudBackup.STATUS_AVAILABLE:
        return JsonResponse({"ok": True, "status": backup.status, "backup": _serialize(backup), "retention": {"deleted": 0, "delete_failed": 0}})
    if backup.status != CloudBackup.STATUS_PENDING_UPLOAD:
        return _error("invalid_state", f"Backup is {backup.status}; request a new upload.", 409)

    try:
        storage = get_storage()
        info = storage.head(backup.object_key)
    except ObjectNotFound:
        backup.last_error = "object not found at completion"
        backup.save(update_fields=["last_error"])
        return _error("object_not_found", "The uploaded file was not found in cloud storage. Please retry the upload.", 409)
    except StorageError as exc:
        logger.warning("head failed gym=%s backup=%s code=%s", backup.gym_ref, backup.backup_id, exc.code)
        return _storage_error(exc)

    etag_mismatch = bool(backup.content_md5 and info.etag and len(info.etag) == 32 and info.etag != backup.content_md5)
    if info.size != backup.file_size or info.metadata.get("sha256") != backup.checksum_sha256 or etag_mismatch:
        logger.warning("upload verification mismatch gym=%s backup=%s", backup.gym_ref, backup.backup_id)
        try:
            storage.delete(backup.object_key)
        except StorageError:
            pass
        backup.mark(CloudBackup.STATUS_FAILED, "size/checksum mismatch after upload")
        services.audit("cloud_backup.upload_rejected", lic, backup_id=str(backup.backup_id))
        return _error("verification_failed", "Uploaded file did not match the declared size/checksum.", 422)

    backup.mark(CloudBackup.STATUS_AVAILABLE)
    services.audit("cloud_backup.uploaded", lic, backup_id=str(backup.backup_id), size=backup.file_size)
    logger.info("cloud upload completed gym=%s backup=%s", backup.gym_ref, backup.backup_id)

    try:
        retention = services.apply_retention(client, storage=storage, license_obj=lic)
    except Exception as exc:  # retention problems must never fail a good upload
        logger.warning("retention pass failed gym=%s: %s", backup.gym_ref, type(exc).__name__)
        retention = {"deleted": 0, "delete_failed": 0}

    return JsonResponse({"ok": True, "status": backup.status, "backup": _serialize(backup), "retention": retention})


# ======================================================================
# LIST / DOWNLOAD / VERIFY / DELETE
# ======================================================================

@csrf_exempt
@require_POST
def list_backups(request):
    data, client, lic, device, err = _resolve(request)
    if err:
        return err
    qs = CloudBackup.objects.filter(client=client, status=CloudBackup.STATUS_AVAILABLE).order_by("-backup_created_at")[:200]
    return JsonResponse({"ok": True, "gym_ref": gym_ref_for(client), "backups": [_serialize(b) for b in qs]})


@csrf_exempt
@require_POST
def request_download(request):
    """Short-lived GET URL for one of this gym's own backups (cloud
    restore). The desktop verifies the checksum returned here before it
    decrypts or restores anything."""
    data, client, lic, device, err = _resolve(request)
    if err:
        return err
    if services.is_rate_limited(lic, "cloud_backup.download_requested"):
        return _error("rate_limited", "Too many download requests. Please try again later.", 429)

    backup = _get_own_backup(client, data.get("backup_id"))
    if backup is None or backup.status != CloudBackup.STATUS_AVAILABLE:
        return _error("not_found", "Backup not found.", 404)

    try:
        url = get_storage().presign_get(backup.object_key, services.presign_expiry_seconds())
    except StorageError as exc:
        return _storage_error(exc)

    backup.restore_requests += 1
    backup.last_restore_request_at = timezone.now()
    backup.save(update_fields=["restore_requests", "last_restore_request_at"])
    services.audit("cloud_backup.download_requested", lic, backup_id=str(backup.backup_id))
    logger.info("cloud download requested gym=%s backup=%s", backup.gym_ref, backup.backup_id)
    return JsonResponse({
        "ok": True,
        "download_url": url,
        "expires_in": services.presign_expiry_seconds(),
        "backup": _serialize(backup),
    })


@csrf_exempt
@require_POST
def verify_backup(request):
    """Re-checks a stored backup. deep=false: size + metadata (cheap).
    deep=true: the server streams the object and recomputes SHA-256."""
    data, client, lic, device, err = _resolve(request)
    if err:
        return err
    if services.is_rate_limited(lic, "cloud_backup.verify_requested"):
        return _error("rate_limited", "Too many verification requests. Please try again later.", 429)
    backup = _get_own_backup(client, data.get("backup_id"))
    if backup is None or backup.status not in (CloudBackup.STATUS_AVAILABLE, CloudBackup.STATUS_CORRUPT):
        return _error("not_found", "Backup not found.", 404)

    services.audit("cloud_backup.verify_requested", lic, backup_id=str(backup.backup_id))
    deep = bool(data.get("deep"))
    try:
        storage = get_storage()
        if deep:
            digest, size = storage.sha256_of(backup.object_key)
            ok = digest == backup.checksum_sha256 and size == backup.file_size
        else:
            info = storage.head(backup.object_key)
            ok = info.size == backup.file_size and info.metadata.get("sha256") == backup.checksum_sha256
    except ObjectNotFound:
        ok = False
    except StorageError as exc:
        return _storage_error(exc)

    if ok:
        backup.mark(CloudBackup.STATUS_AVAILABLE)
    else:
        backup.mark(CloudBackup.STATUS_CORRUPT, "verification failed")
        logger.warning("cloud backup failed verification gym=%s backup=%s", backup.gym_ref, backup.backup_id)
    return JsonResponse({"ok": True, "verified": ok, "deep": deep, "backup": _serialize(backup)})


@csrf_exempt
@require_POST
def delete_backup(request):
    data, client, lic, device, err = _resolve(request)
    if err:
        return err
    if services.is_rate_limited(lic, "cloud_backup.delete_requested"):
        return _error("rate_limited", "Too many delete requests. Please try again later.", 429)
    backup = _get_own_backup(client, data.get("backup_id"))
    if backup is None or backup.status in (CloudBackup.STATUS_DELETED,):
        return _error("not_found", "Backup not found.", 404)

    if backup.status == CloudBackup.STATUS_AVAILABLE:
        others = CloudBackup.objects.filter(client=client, status=CloudBackup.STATUS_AVAILABLE).exclude(pk=backup.pk).count()
        if others < 1:
            return _error("last_backup", "This is the only remaining cloud backup and cannot be deleted.", 409)

    services.audit("cloud_backup.delete_requested", lic, backup_id=str(backup.backup_id))
    try:
        storage = get_storage()
    except StorageError as exc:
        return _storage_error(exc)
    deleted = services._try_delete(backup, storage, "user_request")
    if not deleted:
        return _error("storage_unavailable", "Cloud storage could not delete the file right now; it will be retried automatically.", 503)
    return JsonResponse({"ok": True, "deleted": True})
