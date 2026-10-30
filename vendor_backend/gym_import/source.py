"""Reading a FitDesk v1.2.0 database (fitdesk.db) safely.

The file is never opened for writing: it is copied with SQLite's online
backup (works while FitDesk is open, includes changes still in the -wal
file) into a temporary file, and everything is read from that copy."""
import hashlib
import os
import shutil
import sqlite3
import tempfile

REQUIRED = ["members", "membership_plans", "membership_transactions"]


class SourceError(Exception):
    pass


def snapshot(path):
    """Returns (temp_copy_path, sha256_of_copy)."""
    if not os.path.exists(path):
        raise SourceError(f"File not found: {path}")
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix="fitdesk_v120_")
    os.close(fd)
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(tmp)
            src.backup(dst)
            dst.close()
        finally:
            src.close()
    except sqlite3.DatabaseError:
        # read-only folders can refuse the WAL handshake: fall back to a plain copy
        shutil.copyfile(path, tmp)
        for ext in ("-wal",):
            if os.path.exists(path + ext):
                shutil.copyfile(path + ext, tmp + ext)
        c = sqlite3.connect(tmp)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
    h = hashlib.sha256()
    with open(tmp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return tmp, h.hexdigest()


class Source:
    """Rows of every v1.2.0 table as plain dicts (missing tables = empty)."""

    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SourceError("The FitDesk database failed SQLite's integrity check.")
        self.tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in REQUIRED if t not in self.tables]
        if missing:
            raise SourceError(f"This doesn't look like a FitDesk v1.2.0 database (missing: {', '.join(missing)}).")

    def rows(self, table, order="id"):
        if table not in self.tables:
            return []
        cols = {r[1] for r in self.db.execute(f'PRAGMA table_info("{table}")')}
        q = f'SELECT * FROM "{table}"' + (f" ORDER BY {order}" if order and order.split()[0] in cols else "")
        return [dict(r) for r in self.db.execute(q)]

    def count(self, table):
        return self.db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table in self.tables else 0

    def settings(self):
        """v1.2.0 keeps settings twice: as key/value rows and as columns on
        row 1. The columns are what the app shows, so they win."""
        rows = self.rows("app_settings")
        kv = {r.get("setting_key"): r.get("setting_value") for r in rows if r.get("setting_key")}
        first = rows[0] if rows else {}

        def pick(col, key):
            v = first.get(col)
            return v if v not in (None, "") else kv.get(key, "")
        return {
            "gym_name": pick("gym_name", "gym_name") or "Imported gym",
            "tagline": first.get("tagline") or "",
            "address": pick("address", "gym_address"),
            "phone": pick("phone", "gym_phone"),
            "email": pick("email", "gym_email"),
            "gstin": pick("gst_number", "gym_gstin"),
            "gst_percent": first.get("gst_percent") or kv.get("gst_percent") or "",
            "receipt_prefix": kv.get("receipt_prefix") or "RCPT",
            "receipt_footer": kv.get("receipt_footer") or first.get("thank_you_message") or "",
            "terms": first.get("terms_conditions") or "",
            "whatsapp_greeting": kv.get("whatsapp_greeting") or "",
            "logo_path": pick("logo_path", "gym_logo"),
        }

    def close(self):
        self.db.close()
