import secrets
from datetime import date

from django.db import models
from django.utils import timezone


def generate_license_key():
    """Human-friendly key: FDPX-XXXX-XXXX-XXXX"""
    parts = [secrets.token_hex(2).upper() for _ in range(3)]
    return "FDPX-" + "-".join(parts)


class Client(models.Model):
    gym_name = models.CharField(max_length=200)
    owner_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.gym_name} ({self.owner_name})"

    @property
    def current_license(self):
        return self.licenses.order_by("-issued_at").first()


class License(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_REVOKED = "revoked"
    # "expired" is derived from expiry_date, not stored, so it can never
    # go stale relative to today's date.
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_REVOKED, "Revoked"),
    ]

    client = models.ForeignKey(Client, related_name="licenses", on_delete=models.CASCADE)
    key = models.CharField(max_length=64, unique=True, default=generate_license_key)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    issued_at = models.DateTimeField(auto_now_add=True)
    expiry_date = models.DateField()
    max_activations = models.PositiveIntegerField(default=1)
    machine_id = models.CharField(max_length=255, blank=True, help_text="Auto-filled on first successful validation")
    last_check_in = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"{self.key} — {self.client.gym_name}"

    @property
    def is_expired(self):
        return date.today() > self.expiry_date

    @property
    def effective_status(self):
        """What the client software should actually see."""
        if self.status in (self.STATUS_SUSPENDED, self.STATUS_REVOKED):
            return self.status
        if self.is_expired:
            return "expired"
        return self.STATUS_ACTIVE


class SupportQuery(models.Model):
    STATUS_OPEN = "open"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    client = models.ForeignKey(Client, related_name="queries", on_delete=models.CASCADE)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Support queries"

    def __str__(self):
        return f"{self.subject} — {self.client.gym_name}"

    def mark_resolved(self):
        self.status = self.STATUS_RESOLVED
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_at"])
