"""Business rules for Cloud Backup: object keys, entitlement, retention,
rate limits. Kept out of views.py so the management commands and tests
use the exact same rules as the API."""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from licensing.models import AuditLog

from .models import CloudBackup, gym_ref_for
from .storage import StorageError, get_storage

logger = logging.getLogger("cloud_backup")

FEATURE_CODE = "cloud_backup"


def setting(name, default):
    return getattr(settings, name, default)


def retention_limits():
    return {
        CloudBackup.TYPE_SCHEDULED: int(setting("CLOUD_BACKUP_RETENTION_DAILY", 30)),
        CloudBackup.TYPE_MANUAL: int(setting("CLOUD_BACKUP_RETENTION_MANUAL", 10)),
    }


def max_file_bytes():
    return int(setting("CLOUD_BACKUP_MAX_FILE_MB", 512)) * 1024 * 1024


def presign_expiry_seconds():
    return int(setting("CLOUD_BACKUP_PRESIGN_EXPIRY_SECONDS", 900))


def is_entitled(license_obj):
    """Cloud backup is a Plan/Feature entitlement like every other paid
    module (see licensing.models.License.resolved_features). Local
    backups on the desktop never depend on this."""
    try:
        return FEATURE_CODE in license_obj.resolved_features
    except Exception:
        return False


def build_object_key(client, backup_created_at, backup_id):
    """gyms/GYM-00007/backups/2026/10/FDPRO_GYM-00007_2026-10-03_020000_1a2b3c4d.fdb

    Built entirely on the server from the resolved Client -- the caller
    never supplies any part of the path, so it is impossible to aim an
    upload/download at another gym's prefix."""
    prefix = (setting("R2_OBJECT_PREFIX", "") or "").strip("/")
    gym = gym_ref_for(client)
    ts = backup_created_at
    file_name = f"FDPRO_{gym}_{ts:%Y-%m-%d_%H%M%S}_{str(backup_id)[:8]}.fdb"
    key = f"gyms/{gym}/backups/{ts:%Y}/{ts:%m}/{file_name}"
    if prefix:
        key = f"{prefix}/{key}"
    return key, file_name


# ----------------------------------------------------------------------
# Rate limiting -- DB-backed (same reasoning as licensing/ratelimit.py:
# Render may run several gunicorn workers, an in-memory counter would
# under-count). Counted against append-only AuditLog rows this module
# already writes for every sensitive action.
# ----------------------------------------------------------------------

RATE_LIMITS_PER_HOUR = {
    "cloud_backup.upload_requested": 60,  # a PC back online after weeks offline may flush ~30 at once
    "cloud_backup.download_requested": 30,
    "cloud_backup.verify_requested": 12,
    "cloud_backup.delete_requested": 30,
    "cloud_backup.key_requested": 30,
}


def is_rate_limited(license_obj, action):
    limit = RATE_LIMITS_PER_HOUR.get(action)
    if not limit:
        return False
    cutoff = timezone.now() - timedelta(hours=1)
    count = AuditLog.objects.filter(license=license_obj, action=action, created_at__gte=cutoff).count()
    return count >= limit


def audit(action, license_obj, **metadata):
    # Never put secrets, URLs or key material in here -- ids/sizes only.
    try:
        AuditLog.record(action, license=license_obj, **metadata)
    except Exception as exc:  # audit failure must never break the request
        logger.warning("audit write failed for %s: %s", action, type(exc).__name__)


# ----------------------------------------------------------------------
# Retention
# ----------------------------------------------------------------------

def _try_delete(backup, storage, reason):
    backup.status = CloudBackup.STATUS_DELETION_PENDING
    backup.deletion_attempts += 1
    backup.save(update_fields=["status", "deletion_attempts"])
    try:
        storage.delete(backup.object_key)
    except StorageError as exc:
        backup.last_error = f"delete failed: {exc.code}"[:255]
        backup.save(update_fields=["last_error"])
        logger.warning(
            "retention delete failed gym=%s backup=%s code=%s (will retry)",
            backup.gym_ref, backup.backup_id, exc.code,
        )
        return False
    backup.mark(CloudBackup.STATUS_DELETED)
    audit("cloud_backup.deleted", backup.license, backup_id=str(backup.backup_id), reason=reason)
    logger.info("cloud backup deleted gym=%s backup=%s reason=%s", backup.gym_ref, backup.backup_id, reason)
    return True


def apply_retention(client, storage=None, license_obj=None):
    """Keep the newest N *verified* backups per pool (scheduled: 30,
    manual: 10); everything older in that pool is deleted from R2.

    Rules (from the spec):
      * count-based, never calendar-month based;
      * only AVAILABLE (uploaded + verified) backups count toward N and
        only they are ever candidates -- a pending/failed upload is never
        "rotated out";
      * never delete the only remaining usable copy: if a pass would
        leave this gym with zero AVAILABLE backups it stops;
      * a failed delete is recorded (status deletion_pending, last_error)
        and retried on the next pass / by `manage.py cloud_backup_maintenance`.

    Called after every successful upload. NOT called on license expiry --
    an expired/suspended gym's backups are left exactly as they are.
    Returns {"deleted": n, "delete_failed": n}.
    """
    storage = storage or get_storage()
    deleted = failed = 0

    # 1) retry deletions that failed previously
    for b in CloudBackup.objects.filter(client=client, status=CloudBackup.STATUS_DELETION_PENDING):
        if _try_delete(b, storage, "retry"):
            deleted += 1
        else:
            failed += 1

    # 2) rotate each pool
    for backup_type, keep in retention_limits().items():
        keep = max(1, keep)
        pool = list(
            CloudBackup.objects.filter(
                client=client, backup_type=backup_type, status=CloudBackup.STATUS_AVAILABLE,
            ).order_by("-backup_created_at", "-id")
        )
        for b in pool[keep:]:
            remaining = CloudBackup.objects.filter(client=client, status=CloudBackup.STATUS_AVAILABLE).exclude(pk=b.pk).count()
            if remaining < 1:
                break  # never remove the last usable copy
            if _try_delete(b, storage, "retention"):
                deleted += 1
            else:
                failed += 1

    if deleted or failed:
        audit("cloud_backup.retention", license_obj, gym=gym_ref_for(client), deleted=deleted, delete_failed=failed)
    return {"deleted": deleted, "delete_failed": failed}


def expire_stale_pending(older_than_hours=24, storage=None):
    """Upload requests that were never completed (desktop went offline
    mid-upload, etc.). The record is marked failed and any partial object
    removed; the desktop simply requests a fresh upload next time."""
    storage = storage or get_storage()
    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    count = 0
    for b in CloudBackup.objects.filter(status=CloudBackup.STATUS_PENDING_UPLOAD, last_upload_request_at__lt=cutoff):
        try:
            storage.delete(b.object_key)
        except StorageError:
            continue
        b.mark(CloudBackup.STATUS_FAILED, "upload never completed")
        count += 1
    return count


def is_superseded(client, backup_type, backup_created_at):
    """True when this gym already has >= N verified backups in the same
    pool that are all NEWER than the one being offered -- uploading it
    would only have retention delete it again immediately."""
    keep = max(1, retention_limits().get(backup_type, 30))
    newer = CloudBackup.objects.filter(
        client=client, backup_type=backup_type, status=CloudBackup.STATUS_AVAILABLE,
        backup_created_at__gt=backup_created_at,
    ).count()
    return newer >= keep
