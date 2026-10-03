"""Per-gym backup encryption keys, encrypted at rest.

Same pattern as licensing/crypto_utils.py (WhatsApp tokens): the real key
material is only ever stored Fernet-encrypted, with the Fernet key living
solely in the BACKUP_KEY_ENCRYPTION_KEY environment variable -- never in
the database, never in git, never in R2.

Generate BACKUP_KEY_ENCRYPTION_KEY once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and store it in Render AND in your own offline password manager.
LOSING THIS VALUE MAKES EVERY GYM'S CLOUD BACKUPS UNRESTORABLE on a new
PC (an existing PC that has already cached its gym key can still restore).

ROTATION (never "just replace it"): set the variable to
    "<NEW key>,<OLD key>"
-- comma-separated, newest first (cryptography's MultiFernet). Every key
in the list can decrypt; only the first encrypts. Then run
    python manage.py check_backup_keys --rewrap
which re-encrypts every stored gym key under the new key and verifies
all of them. Only after it reports 0 unreadable may the old key be
dropped from the list. Replacing the value outright would NOT generate
new gym keys or touch any backup -- but every /api/backups/key/ call for
an existing gym would fail (500 key_unreadable) and
`check_backup_keys` would report it, so the mistake is loud, not silent.
"""

import base64
import os

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import GymBackupKey


class BackupKeyServiceNotConfigured(Exception):
    pass


class BackupKeyUnreadable(Exception):
    pass


def _fernet():
    from cryptography.fernet import Fernet, MultiFernet

    raw = getattr(settings, "BACKUP_KEY_ENCRYPTION_KEY", "") or ""
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts:
        raise BackupKeyServiceNotConfigured("BACKUP_KEY_ENCRYPTION_KEY is not set.")
    try:
        return MultiFernet([Fernet(p.encode("utf-8")) for p in parts])
    except Exception as exc:  # malformed env value (message never includes it)
        raise BackupKeyServiceNotConfigured("BACKUP_KEY_ENCRYPTION_KEY is malformed.") from exc


def is_configured():
    try:
        _fernet()
        return True
    except BackupKeyServiceNotConfigured:
        return False


def get_or_create_gym_key(client):
    """Returns (key_id, raw_32_byte_key) for this Client, creating and
    persisting a fresh random key the first time. Safe under concurrent
    first calls (the OneToOne constraint picks a single winner)."""
    fernet = _fernet()
    existing = GymBackupKey.objects.filter(client=client).first()
    if existing is None:
        raw = os.urandom(32)
        try:
            with transaction.atomic():
                existing = GymBackupKey.objects.create(
                    client=client,
                    key_encrypted=fernet.encrypt(raw).decode("ascii"),
                )
            return existing.key_id, raw
        except IntegrityError:
            existing = GymBackupKey.objects.get(client=client)
    try:
        raw = fernet.decrypt(existing.key_encrypted.encode("ascii"))
    except Exception as exc:
        raise BackupKeyUnreadable("Stored gym backup key could not be decrypted.") from exc
    if len(raw) != 32:
        raise BackupKeyUnreadable("Stored gym backup key has an invalid length.")
    return existing.key_id, raw


def encode_key(raw):
    return base64.b64encode(raw).decode("ascii")


def audit_stored_keys(rewrap=False):
    """Checks every stored gym key decrypts with the configured key(s).
    rewrap=True re-encrypts each readable one under the FIRST (current)
    key -- the rotation step. Returns {"total", "ok", "unreadable", "rewrapped"}.
    Never prints or returns key material."""
    fernet = _fernet()
    result = {"total": 0, "ok": 0, "unreadable": [], "rewrapped": 0}
    for row in GymBackupKey.objects.select_related("client").all():
        result["total"] += 1
        token = row.key_encrypted.encode("ascii")
        try:
            raw = fernet.decrypt(token)
            if len(raw) != 32:
                raise ValueError
        except Exception:
            result["unreadable"].append(row.key_id)
            continue
        result["ok"] += 1
        if rewrap:
            row.key_encrypted = fernet.rotate(token).decode("ascii")
            row.save(update_fields=["key_encrypted"])
            result["rewrapped"] += 1
    return result
