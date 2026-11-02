import shutil
import tempfile
from datetime import datetime, timezone as dt_tz
from io import BytesIO

from django.test import override_settings

from gym_core.models import Bill, CheckIn, Member, Payment
from gym_core.tests import Base

from . import core, gymscope
from .core import BackupError, LocalTarget, make_key, open_blob, run_backup, seal
from .models import ServerBackup

KEY = make_key()


@override_settings(SERVER_BACKUP_KEY=KEY)
class BackupBase(Base):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        self.t = LocalTarget(self.dir)
        core.set_target_for_tests(self.t)

    def tearDown(self):
        core.set_target_for_tests(None)
        shutil.rmtree(self.dir, ignore_errors=True)
        super().tearDown()

    def paid_member(self, tok, name, plan):
        m = self.member(tok, name=name)
        s, b = self.bill(tok, m["id"], plan, amount_paid="1500")
        self.assertEqual(s, 201, b)
        return m


class BackupFileTests(BackupBase):
    def test_backup_is_encrypted_checked_and_kept_30_days_plus_monthly(self):
        self.paid_member(self.rec, "Asha Rao", self.monthly)
        rec = run_backup(now=datetime(2026, 11, 2, 0, 30, tzinfo=dt_tz.utc))
        self.assertEqual((rec.status, rec.gyms, rec.members, rec.bills), ("ok", 1, 1, 1))
        blob = self.t.get(rec.key)
        self.assertNotIn(b"Asha Rao", blob)                                   # encrypted
        manifest, data = open_blob(blob)
        self.assertEqual(manifest["counts"]["gym_core.member"], 1)
        self.assertNotIn("db_backup.serverbackup", manifest["counts"])
        self.assertEqual(self.t.list("server-db/monthly/"), ["server-db/monthly/2026-11.fdbk"])
        with override_settings(SERVER_BACKUP_KEEP_DAILY=2, SERVER_BACKUP_KEEP_MONTHLY=2):
            for day in (3, 4, 5):
                run_backup(now=datetime(2026, 11, day, 0, 30, tzinfo=dt_tz.utc))
            run_backup(now=datetime(2026, 12, 1, 0, 30, tzinfo=dt_tz.utc))
            run_backup(now=datetime(2027, 1, 1, 0, 30, tzinfo=dt_tz.utc))
        self.assertEqual(self.t.list("server-db/daily/"), ["server-db/daily/2026-12-01_003000Z.fdbk",
                                                          "server-db/daily/2027-01-01_003000Z.fdbk"])
        self.assertEqual(self.t.list("server-db/monthly/"), ["server-db/monthly/2026-12.fdbk",
                                                            "server-db/monthly/2027-01.fdbk"])

    def test_wrong_or_missing_key(self):
        blob = seal({"x": 1}, [])
        with override_settings(SERVER_BACKUP_KEY=make_key()):
            with self.assertRaises(BackupError):
                open_blob(blob)
        with override_settings(SERVER_BACKUP_KEY=""):
            with self.assertRaises(BackupError):
                run_backup()
        self.assertEqual(ServerBackup.objects.filter(status="failed").count(), 1)

    def test_render_needs_r2(self):
        core.set_target_for_tests(None)
        with override_settings(ON_RENDER=True, R2_ACCOUNT_ID="", R2_ENDPOINT="", R2_ACCESS_KEY_ID="", R2_SECRET_ACCESS_KEY=""):
            with self.assertRaises(BackupError):
                core.target()

    def test_full_restore_only_into_empty_database(self):
        manifest, data = open_blob(self.t.get(run_backup().key))
        with self.assertRaises(BackupError):
            core.restore_all(manifest, data)

    def test_times_kept_exactly(self):
        m = self.paid_member(self.rec, "Exact Time", self.monthly)
        _, data = open_blob(self.t.get(run_backup().key))
        row = next(o for o in data if o["model"] == "gym_core.member" and o["pk"] == m["id"])
        self.assertEqual(row["fields"]["created_at"], Member.objects.get(pk=m["id"]).created_at.isoformat())


class OneGymRestoreTests(BackupBase):
    def test_restore_one_gym_leaves_others_alone(self):
        a = self.paid_member(self.rec, "Asha Rao", self.monthly)
        CheckIn.objects.create(client=self.gym, member_id=a["id"], local_date="2026-11-01")
        other = self.make_gym(name="Other Gym")
        _, otok = self.user(other, "owner")
        s, p = self.req("POST", "plans/", otok, {"name": "Monthly", "duration_days": 30, "fee": "1500"})
        b = self.paid_member(otok, "Bilal Khan", p["plan"]["id"])
        manifest, data = open_blob(self.t.get(run_backup().key))
        self.assertEqual({g["name"] for g in manifest["gyms"]}, {"P&P Fitness", "Other Gym"})

        # after the backup: gym 1 loses payments and check-ins and gets a wrong name; gym 2 changes too
        Payment.objects.filter(bill__client=self.gym).delete()
        CheckIn.objects.filter(client=self.gym).delete()
        Member.objects.filter(pk=a["id"]).update(name="WRONG")
        self.member(self.rec, name="Added After Backup")
        Member.objects.filter(pk=b["id"]).update(notes="kept")

        gymscope.restore_gym(manifest, data, self.gym.id)
        self.assertEqual(Member.objects.get(pk=a["id"]).name, "Asha Rao")
        self.assertFalse(Member.objects.filter(name="Added After Backup").exists())
        self.assertEqual(Payment.objects.filter(bill__client=self.gym).count(), 1)
        self.assertEqual(CheckIn.objects.filter(client=self.gym).count(), 1)
        self.assertEqual(Member.objects.get(pk=b["id"]).notes, "kept")             # other gym untouched
        self.assertEqual(Bill.objects.filter(client=other).count(), 1)
        s, j = self.req("GET", "members/", self.owner)                         # gym 1 signs in again
        self.assertEqual(s, 401)
        s, j = self.req("GET", "members/", otok)
        self.assertEqual(s, 200)
        # the app keeps numbering from the restored counters
        n = self.member(otok, name="Next One")
        self.assertTrue(n["code"])

    def test_gym_not_in_backup(self):
        manifest, data = open_blob(self.t.get(run_backup().key))
        late = self.make_gym(name="Joined Later")
        with self.assertRaises(BackupError):
            gymscope.restore_gym(manifest, data, late.id)


class DataExportTests(BackupBase):
    def test_owner_downloads_everything_without_secrets(self):
        from openpyxl import load_workbook
        self.paid_member(self.rec, "Asha Rao", self.monthly)
        r = self.client.get("/api/v1/data-export/", HTTP_AUTHORIZATION=f"Bearer {self.owner}")
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertIn("all data", r["Content-Disposition"])
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(wb.sheetnames[0], "Members")
        self.assertIn("Asha Rao", [c.value for c in wb["Members"]["B"]] + [c.value for row in wb["Members"].iter_rows() for c in row])
        heads = {c.value.lower() for ws in wb for c in ws[1] if c.value}
        self.assertFalse([h for h in heads if "password" in h or "secret" in h or "hash" in h])
        self.assertFalse([n for n in wb.sheetnames if "session" in n.lower()])
        r = self.client.get("/api/v1/data-export/", HTTP_AUTHORIZATION=f"Bearer {self.rec}")
        self.assertEqual(r.status_code, 403)
