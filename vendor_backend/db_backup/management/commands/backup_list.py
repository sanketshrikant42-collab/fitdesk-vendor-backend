from django.core.management.base import BaseCommand

from db_backup.core import PREFIX, target


class Command(BaseCommand):
    help = "List the server backups that are stored."

    def handle(self, *a, **o):
        t = target()
        self.stdout.write(f"Backups in {t.where()}:")
        for kind in ("daily", "monthly", "manual"):
            keys = t.list(f"{PREFIX}/{kind}/")
            self.stdout.write(f"\n{kind} ({len(keys)})")
            for k in keys:
                self.stdout.write("  " + k)
