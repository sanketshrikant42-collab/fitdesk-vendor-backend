from django.core.management.base import BaseCommand

from gym_demo.seed import build


class Command(BaseCommand):
    help = "Rebuilds the demo gym (Pulse Fitness Club) with fresh made-up data dated around today."

    def handle(self, *a, **o):
        c = build()
        from gym_core.models import Bill, CheckIn, Member
        self.stdout.write(self.style.SUCCESS(
            f"Demo gym ready: {c.gym_name} (gym id {c.id}) · {Member.objects.filter(client=c).count()} members · "
            f"{Bill.objects.filter(client=c).count()} bills · {CheckIn.objects.filter(client=c).count()} check-ins"))
