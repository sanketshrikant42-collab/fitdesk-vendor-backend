"""Makes a backup of the whole server database now.

On Render this runs every night from the Cron Job, after run_daily_jobs:
    python manage.py run_daily_jobs && python manage.py backup_now
"""
from django.core.management.base import BaseCommand, CommandError

from db_backup.core import BackupError, make_key, run_backup, target


class Command(BaseCommand):
    help = "Back up the whole server database (encrypted) to R2, or the local backup folder on a PC."

    def add_arguments(self, parser):
        parser.add_argument("--make-key", action="store_true", help="Print a new SERVER_BACKUP_KEY and stop.")
        parser.add_argument("--reason", default="nightly", help="nightly (default, kept 30 days + monthly) or manual")

    def handle(self, *a, **o):
        if o["make_key"]:
            self.stdout.write(make_key())
            self.stdout.write(self.style.WARNING(
                "Save this key in Render (SERVER_BACKUP_KEY) AND somewhere safe offline. "
                "Without it, no backup can ever be opened."))
            return
        try:
            rec = run_backup(reason=o["reason"])
        except BackupError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(
            f"Backup OK: {rec.key} in {target().where()} · {rec.size_bytes / 1024:.0f} KB · "
            f"{rec.gyms} gym(s), {rec.members} members, {rec.bills} bills · {rec.removed_old} old file(s) removed"))
