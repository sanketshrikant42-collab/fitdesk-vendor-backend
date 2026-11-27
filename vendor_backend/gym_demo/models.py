from django.db import models

from licensing.models import Client


class DemoGym(models.Model):
    """Marks the one gym that leads try from the "Try the demo" button.
    Its data is made up and rebuilt every night (python manage.py demo_reset)."""

    client = models.OneToOneField(Client, related_name="demo", on_delete=models.CASCADE)
    built_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Demo: {self.client.gym_name}"
