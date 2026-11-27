"""The morning job for every gym with an active licence: builds the day's
"to send" list (renewals, dues, birthdays, inactive members, owner's
summary). Safe to run more than once a day.

On Render, add a Cron Job running daily at 06:00 IST (00:30 UTC):
    python manage.py run_daily_jobs
Until then the list builds itself the first time someone opens it each day.
"""
from django.core.management.base import BaseCommand

from gym_accounts.api import licence_for
from gym_auto.services import generate
from gym_core.services import parse_date, today_for
from licensing.models import Client, License


class Command(BaseCommand):
    help = "Build today's reminder / summary list for every active gym."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: today in each gym's time zone)")

    def handle(self, *args, date=None, **kw):
        self.refresh_demo()
        n = 0
        for client in Client.objects.all():
            if licence_for(client)[1] != License.STATUS_ACTIVE:
                continue
            on = parse_date(date, "date") if date else today_for(client)
            created = generate(client, on)
            n += 1
            self.stdout.write(f"{client.gym_name}: {on} {created or 'nothing new'}")
        self.stdout.write(self.style.SUCCESS(f"Done for {n} gym(s)."))

    def refresh_demo(self):
        """The demo gym gets fresh made-up data once a day (the first run of
        the day, i.e. the 6 am job), so leads always see a tidy, current gym."""
        from django.utils import timezone
        from gym_demo.models import DemoGym
        from gym_demo.seed import IST, build
        d = DemoGym.objects.first()
        if d is None or timezone.localtime(d.built_at, IST).date() == timezone.localdate(timezone=IST):
            return
        try:
            c = build()
            self.stdout.write(f"Demo gym rebuilt: {c.gym_name} (gym id {c.id})")
        except Exception as e:                    # noqa: BLE001 - never stop the real gyms' jobs
            self.stderr.write(f"Demo gym rebuild failed: {e}")
