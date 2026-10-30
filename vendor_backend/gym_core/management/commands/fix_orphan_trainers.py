"""Removes trainer records left behind by a failed "Add staff" save.

Before the fix, adding a staff member in the Trainer department created the
trainer first; if the staff save then failed (e.g. mobile already used by
another staff member), the trainer stayed. Such a trainer:
  - has no staff record,
  - has the same mobile as a staff member who is NOT a trainer,
  - has never been used (no bills, memberships or PT sessions).

    python manage.py fix_orphan_trainers           # list only
    python manage.py fix_orphan_trainers --apply   # remove them
"""
from django.core.management.base import BaseCommand

from gym_core.models import SessionLog, Trainer


class Command(BaseCommand):
    help = "Find (and with --apply, remove) trainers left behind by failed staff saves."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually remove them.")

    def handle(self, *args, apply=False, **kw):
        from gym_ops.models import Staff
        found = []
        for t in Trainer.objects.select_related("client"):
            if Staff.objects.filter(trainer=t).exists():
                continue
            if t.bills.exists() or t.memberships.exists() or SessionLog.objects.filter(trainer=t).exists():
                continue
            twin = Staff.objects.filter(client=t.client, phone=t.phone).first()
            if twin is None or (twin.department or "").lower() == "trainer" and twin.trainer_id is None:
                continue
            found.append((t, twin))
        for t, twin in found:
            self.stdout.write(f"{t.client.gym_name}: trainer {t.code} {t.name} {t.phone} "
                              f"(same mobile as staff {twin.code} {twin.name}, {twin.designation})")
            if apply:
                t.delete()
        if not found:
            self.stdout.write("No stray trainers found.")
        elif apply:
            self.stdout.write(self.style.SUCCESS(f"Removed {len(found)} stray trainer(s)."))
        else:
            self.stdout.write("Run again with --apply to remove them.")
