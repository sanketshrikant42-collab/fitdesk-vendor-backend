"""Housekeeping for cloud backups. Safe to run any time (idempotent):

    python manage.py cloud_backup_maintenance

  * retries R2 deletions that failed earlier (status deletion_pending),
  * re-applies retention for every gym that has cloud backups,
  * expires upload requests that were never completed (>24h).

It NEVER deletes anything because a license expired -- expired/suspended
gyms keep their backups. Can be run as a Render Cron Job, or by hand.
"""

from django.core.management.base import BaseCommand

from cloud_backup import services
from cloud_backup.models import CloudBackup
from cloud_backup.storage import StorageError, get_storage
from licensing.models import Client


class Command(BaseCommand):
    help = "Retry failed deletions, apply retention, expire abandoned uploads."

    def handle(self, *args, **opts):
        try:
            storage = get_storage()
        except StorageError as exc:
            self.stderr.write(f"Storage not available: {exc.code}")
            return
        totals = {"deleted": 0, "delete_failed": 0}
        client_ids = CloudBackup.objects.values_list("client_id", flat=True).distinct()
        for client in Client.objects.filter(pk__in=client_ids):
            r = services.apply_retention(client, storage=storage)
            totals["deleted"] += r["deleted"]
            totals["delete_failed"] += r["delete_failed"]
        expired = services.expire_stale_pending(storage=storage)
        self.stdout.write(
            f"retention deleted={totals['deleted']} delete_failed={totals['delete_failed']} "
            f"stale_pending_expired={expired}"
        )
