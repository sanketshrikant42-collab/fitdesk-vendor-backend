"""Open, check and restore a server backup.

  python manage.py backup_restore latest                 # check it opens; show what's inside
  python manage.py backup_restore latest --gym 6         # show what restoring gym 6 would change
  python manage.py backup_restore latest --gym 6 --yes   # restore gym 6 (other gyms untouched)
  python manage.py backup_restore latest --all --yes     # everything, into a NEW EMPTY database

The backup can be 'latest', a stored name from backup_list, or a file path.
Before restoring one gym, a safety backup of how things are now is made first.
"""
from django.core.management.base import BaseCommand, CommandError

from db_backup import gymscope as G
from db_backup.core import BackupError, open_blob, resolve, restore_all, run_backup

LABELS = {"gym_core.member": "Members", "gym_core.bill": "Bills", "gym_core.payment": "Payments",
          "gym_core.membership": "Memberships", "gym_core.checkin": "Check-ins", "gym_core.plan": "Plans",
          "gym_ops.expense": "Expenses", "gym_ops.enquiry": "Enquiries", "gym_ops.staff": "Staff",
          "gym_ops.dietplan": "Diet plans", "gym_accounts.staffuser": "Logins"}


class Command(BaseCommand):
    help = "Check or restore a server backup (whole database, or one gym)."

    def add_arguments(self, p):
        p.add_argument("backup", nargs="?", default="latest")
        p.add_argument("--gym", type=int, help="Restore only this gym (its id, shown by the check).")
        p.add_argument("--all", action="store_true", help="Restore everything into a new, empty database.")
        p.add_argument("--yes", action="store_true", help="Actually do it (otherwise only shows what would happen).")
        p.add_argument("--no-safety-backup", action="store_true")

    def handle(self, *a, **o):
        try:
            name, blob = resolve(o["backup"])
            manifest, data = open_blob(blob)
        except BackupError as e:
            raise CommandError(str(e))
        w = self.stdout.write
        w(f"Backup {name}: made {manifest['made_at']} ({manifest['reason']}), {manifest['rows']} rows. Opens fine.")
        w("Gyms inside: " + ", ".join(f"{g['id']} = {g['name']}" for g in manifest["gyms"]))
        c = manifest["counts"]
        w("Totals: " + ", ".join(f"{v} {c.get(k, 0)}" for k, v in LABELS.items()))
        try:
            if o["all"]:
                if not o["yes"]:
                    w(self.style.WARNING("Add --yes to load everything into this (empty) database."))
                    return
                restore_all(manifest, data)
                w(self.style.SUCCESS("Restored everything. Check it, then point the server at this database."))
            elif o["gym"]:
                gid = o["gym"]
                before = G.counts_live(gid)
                after = G.counts_rows([r for r in G.in_backup(data, gid) if r["model"] not in G.NOT_RESTORED])
                w(f"\nGym {gid}: now on the server  ->  after restoring")
                for k, v in LABELS.items():
                    w(f"  {v:<12} {before.get(k, 0):>6}  ->  {after.get(k, 0)}")
                if not o["yes"]:
                    w(self.style.WARNING("Nothing changed. Add --yes to restore this gym. Its staff will need to sign in again."))
                    return
                if not o["no_safety_backup"]:
                    rec = run_backup(reason="before-restore")
                    w(f"Safety backup of how things are now: {rec.key}")
                G.restore_gym(manifest, data, gid)
                w(self.style.SUCCESS(f"Gym {gid} restored. Other gyms were not touched."))
        except BackupError as e:
            raise CommandError(str(e))
