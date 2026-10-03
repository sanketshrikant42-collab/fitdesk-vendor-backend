"""Verify (and optionally re-wrap) every gym's stored backup key.

    python manage.py check_backup_keys            # read-only check
    python manage.py check_backup_keys --rewrap   # after adding a NEW key in
                                                  # front of the old one

Run it after every deploy and after ANY change to BACKUP_KEY_ENCRYPTION_KEY.
Exit status is non-zero if any stored key can't be decrypted -- that means
new/replacement PCs of those gyms could not restore their cloud backups.
Prints key ids only, never key material.
"""

from django.core.management.base import BaseCommand, CommandError

from cloud_backup import keys


class Command(BaseCommand):
    help = "Verify every stored gym backup key decrypts with BACKUP_KEY_ENCRYPTION_KEY (optionally re-wrap)."

    def add_arguments(self, parser):
        parser.add_argument("--rewrap", action="store_true", help="Re-encrypt readable keys under the first (current) key.")

    def handle(self, *args, **opts):
        try:
            result = keys.audit_stored_keys(rewrap=opts["rewrap"])
        except keys.BackupKeyServiceNotConfigured as exc:
            raise CommandError(str(exc))
        self.stdout.write(
            f"gym keys: total={result['total']} readable={result['ok']} "
            f"unreadable={len(result['unreadable'])} rewrapped={result['rewrapped']}"
        )
        if result["unreadable"]:
            for key_id in result["unreadable"]:
                self.stderr.write(f"  UNREADABLE: {key_id}")
            raise CommandError("Some gym keys cannot be decrypted with the configured BACKUP_KEY_ENCRYPTION_KEY.")
        self.stdout.write(self.style.SUCCESS("All gym backup keys are readable."))
