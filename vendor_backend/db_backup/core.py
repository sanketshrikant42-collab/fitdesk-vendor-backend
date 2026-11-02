"""Nightly backup of the whole server database.

Each backup is one file: every table (except throwaway ones like sessions),
compressed, then encrypted with SERVER_BACKUP_KEY, so the file is useless
to anyone without that key. It goes to Cloudflare R2 under server-db/,
separate from the gyms' own desktop backups. Kept: the newest 30 daily
files and one file per month for 12 months.

Restoring: the whole database into an EMPTY database (disaster recovery or
a restore test), or just one gym inside the live database (gymscope.py).
"""
import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone as dt_tz
from pathlib import Path

import django
from django.conf import settings
from django.core.management import call_command
from django.db.migrations.recorder import MigrationRecorder

PREFIX = "server-db"
EXT = ".fdbk"
MAGIC = b"FITDESK-SERVER-BACKUP-1\n"
EXCLUDE = ["contenttypes", "auth.permission", "sessions", "admin.logentry",
           "gym_accounts.authattempt", "db_backup.serverbackup"]


class BackupError(Exception):
    pass


# --- encryption --------------------------------------------------------------------------------------

def make_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _fernet():
    from cryptography.fernet import Fernet
    key = getattr(settings, "SERVER_BACKUP_KEY", "") or ""
    if not key:
        raise BackupError("SERVER_BACKUP_KEY is not set, so backups can't be encrypted. "
                          "Create one with: python manage.py backup_now --make-key")
    try:
        return Fernet(key.encode())
    except Exception:
        raise BackupError("SERVER_BACKUP_KEY is not a valid key. Create one with: python manage.py backup_now --make-key")


def seal(manifest, data):
    raw = json.dumps({"manifest": manifest, "data": data}, separators=(",", ":"), ensure_ascii=False).encode()
    return MAGIC + _fernet().encrypt(gzip.compress(raw, 6))


def open_blob(blob):
    """-> (manifest, data). Raises BackupError for a wrong key or a damaged file."""
    from cryptography.fernet import InvalidToken
    if not blob.startswith(MAGIC):
        raise BackupError("This isn't a FitDesk server backup file.")
    try:
        raw = gzip.decompress(_fernet().decrypt(blob[len(MAGIC):]))
    except InvalidToken:
        raise BackupError("Can't open this backup: SERVER_BACKUP_KEY is not the key it was made with, or the file is damaged.")
    body = json.loads(raw)
    return body["manifest"], body["data"]


def sha256(b):
    return hashlib.sha256(b).hexdigest()


# --- where files go ----------------------------------------------------------------------------------

class LocalTarget:
    """A folder on this computer (local testing on a PC)."""
    def __init__(self, root):
        self.root = Path(root)

    def where(self):
        return str(self.root)

    def put(self, key, data):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key):
        p = self.root / key
        if not p.exists():
            raise BackupError(f"No backup called {key}.")
        return p.read_bytes()

    def list(self, prefix):
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace(os.sep, "/") for p in base.rglob("*" + EXT))

    def delete(self, key):
        (self.root / key).unlink(missing_ok=True)


class R2Target:
    """Cloudflare R2, through the same credentials the desktop backups use."""
    def __init__(self):
        from cloud_backup.storage import R2Storage
        self.s = R2Storage()

    def where(self):
        return f"Cloudflare R2 bucket {self.s.bucket}"

    def put(self, key, data):
        self.s.put_bytes(key, data, metadata={"kind": "fitdesk-server-backup"})

    def get(self, key):
        try:
            return self.s.get_bytes(key)
        except Exception as e:
            raise BackupError(f"Couldn't download {key}: {e}")

    def list(self, prefix):
        return sorted(k for k in self.s.list_keys(prefix, max_keys=1000) if k.endswith(EXT))

    def delete(self, key):
        self.s.delete(key)


_override = None


def set_target_for_tests(t):
    global _override
    _override = t


def target():
    if _override is not None:
        return _override
    from cloud_backup.storage import is_configured
    mode = (getattr(settings, "SERVER_BACKUP_TARGET", "auto") or "auto").lower()
    if mode == "r2" or (mode == "auto" and is_configured()):
        if not is_configured():
            raise BackupError("SERVER_BACKUP_TARGET=r2 but the R2_* variables are not set.")
        return R2Target()
    if getattr(settings, "ON_RENDER", False):
        raise BackupError("On Render, backups must go to Cloudflare R2 (Render's own disk is wiped on every deploy). "
                          "Set the R2_* environment variables.")
    return LocalTarget(settings.SERVER_BACKUP_DIR)


# --- making a backup ---------------------------------------------------------------------------------

def _migrations():
    out = {}
    for app, name in MigrationRecorder.Migration.objects.values_list("app", "name"):
        out[app] = max(out.get(app, ""), name)
    return out


def _plain(v):
    """Exact text for values JSON can't hold (Django's own dumpdata would
    round times to milliseconds; a backup must give back exactly what it took)."""
    import datetime as _dt
    import uuid
    from decimal import Decimal
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        return str(v.total_seconds())
    if isinstance(v, (Decimal, uuid.UUID)):
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        import base64
        return base64.b64encode(bytes(v)).decode()
    if isinstance(v, (tuple, set)):
        return list(v)
    raise TypeError(f"Can't back up a value of type {type(v).__name__}")


def _models():
    from django.apps import apps
    from django.core.serializers import sort_dependencies
    skip_apps = {x for x in EXCLUDE if "." not in x}
    skip_models = {x for x in EXCLUDE if "." in x}
    todo = [(a, None) for a in apps.get_app_configs() if a.label not in skip_apps and a.models_module is not None]
    return [m for m in sort_dependencies(todo, allow_cycles=True)
            if m._meta.managed and not m._meta.proxy and not m._meta.auto_created
            and m._meta.label_lower not in skip_models]


def dump():
    from django.core import serializers
    data = []
    for m in _models():
        qs = m._base_manager.order_by(m._meta.pk.name)
        data += serializers.serialize("python", qs.iterator(), use_natural_foreign_keys=True)
    return json.loads(json.dumps(data, default=_plain, ensure_ascii=False))


def build(reason="nightly"):
    data = dump()
    counts = Counter(o["model"] for o in data)
    gyms = [{"id": o["pk"], "name": o["fields"].get("gym_name", "")} for o in data if o["model"] == "licensing.client"]
    manifest = {"made_at": datetime.now(dt_tz.utc).isoformat(timespec="seconds"), "reason": reason,
                "django": django.get_version(), "migrations": _migrations(), "counts": dict(sorted(counts.items())),
                "gyms": gyms, "rows": len(data)}
    return manifest, data


def keep_newest(t, prefix, keep):
    keys = t.list(prefix)
    old = keys[:-keep] if keep > 0 else []
    for k in old:
        t.delete(k)
    return len(old)


def run_backup(reason="nightly", now=None):
    """Makes, uploads, re-downloads and checks one backup, then trims old
    ones. Old files are only removed after the new one is proven good."""
    from django.utils import timezone
    from .models import ServerBackup
    now = now or timezone.now()
    rec = ServerBackup.objects.create(started_at=now, reason=reason)
    try:
        t = target()
        manifest, data = build(reason)
        blob = seal(manifest, data)
        digest = sha256(blob)
        stamp = now.astimezone(dt_tz.utc)
        key = f"{PREFIX}/daily/{stamp:%Y-%m-%d_%H%M%S}Z{EXT}" if reason == "nightly" else \
              f"{PREFIX}/manual/{stamp:%Y-%m-%d_%H%M%S}Z-{reason}{EXT}"
        t.put(key, blob)
        back = t.get(key)
        if sha256(back) != digest:
            raise BackupError("The uploaded file doesn't match what was sent.")
        m2, d2 = open_blob(back)
        if len(d2) != len(data):
            raise BackupError("The uploaded file couldn't be read back completely.")
        removed = 0
        if reason == "nightly":
            mkey = f"{PREFIX}/monthly/{stamp:%Y-%m}{EXT}"
            if mkey not in t.list(f"{PREFIX}/monthly/"):
                t.put(mkey, blob)
            removed += keep_newest(t, f"{PREFIX}/daily/", int(getattr(settings, "SERVER_BACKUP_KEEP_DAILY", 30)))
            removed += keep_newest(t, f"{PREFIX}/monthly/", int(getattr(settings, "SERVER_BACKUP_KEEP_MONTHLY", 12)))
        else:
            removed += keep_newest(t, f"{PREFIX}/manual/", int(getattr(settings, "SERVER_BACKUP_KEEP_MANUAL", 20)))
        c = manifest["counts"]
        rec.status, rec.key, rec.size_bytes, rec.sha256 = ServerBackup.STATUS_OK, key, len(blob), digest
        rec.gyms, rec.members, rec.bills = c.get("licensing.client", 0), c.get("gym_core.member", 0), c.get("gym_core.bill", 0)
        rec.rows, rec.removed_old, rec.finished_at = manifest["rows"], removed, timezone.now()
        rec.save()
        return rec
    except Exception as e:
        rec.status, rec.error, rec.finished_at = ServerBackup.STATUS_FAILED, f"{type(e).__name__}: {e}"[:2000], timezone.now()
        rec.save()
        from django.core.mail import mail_admins
        mail_admins("FitDesk server backup FAILED", f"The {reason} backup failed:\n\n{rec.error}", fail_silently=True)
        raise


def resolve(name, t=None):
    """'latest', a stored key, or a file path on this computer -> (label, bytes)."""
    if name and os.path.isfile(name):
        return name, Path(name).read_bytes()
    t = t or target()
    if name in (None, "", "latest"):
        keys = t.list(f"{PREFIX}/daily/") + t.list(f"{PREFIX}/manual/")
        if not keys:
            raise BackupError(f"No backups found in {t.where()}.")
        name = max(keys, key=lambda k: k.rsplit("/", 1)[-1][:19])
    return name, t.get(name)


# --- restoring everything into an empty database --------------------------------------------------------

def _check_migrations(manifest):
    have = _migrations()
    behind = [f"{a} (backup {n}, here {have.get(a, 'none')})" for a, n in manifest.get("migrations", {}).items()
              if have.get(a, "") < n]
    if behind:
        raise BackupError("This server's code is older than the backup. Update the code and run migrate first: " + ", ".join(behind))


def load(data):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "restore.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        call_command("loaddata", str(p), verbosity=0)


def restore_all(manifest, data):
    from django.contrib.auth import get_user_model
    from licensing.models import Client
    if Client.objects.exists() or get_user_model().objects.exists():
        raise BackupError("This database already has data. A full restore only goes into a new, empty database "
                          "(run migrate on it first). To fix one gym in the live database, use --gym.")
    _check_migrations(manifest)
    call_command("flush", interactive=False, verbosity=0)     # drop rows that migrations pre-fill, so the copy is exact
    load(data)
