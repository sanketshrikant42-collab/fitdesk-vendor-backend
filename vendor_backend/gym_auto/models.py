"""Phase 3C: the daily "to send" list (renewal / dues / birthday / inactive
reminders and the owner's summary) and its settings.

Until the gym's Meta WhatsApp account is verified, every item is sent by a
person tapping a WhatsApp link (channel "link"); the server prepares the
text, the phone number and the UPI payment link, and records who sent it.
"""
from django.db import models

from gym_accounts.models import StaffDevice, StaffUser
from gym_core.models import Member
from licensing.models import Client


class ReminderSettings(models.Model):
    """Per gym. Blank templates use the built-in wording (see texts.py)."""

    client = models.OneToOneField(Client, related_name="reminder_settings", on_delete=models.CASCADE)
    renewal_on = models.BooleanField(default=True)
    renewal_days = models.CharField(max_length=60, default="7,3,1,0,-3",
                                    help_text="Days before the membership ends (0 = the last day); negative = days after it ended.")
    dues_on = models.BooleanField(default=True)
    dues_every_days = models.PositiveIntegerField(default=7, help_text="Remind about the same dues again after this many days.")
    birthday_on = models.BooleanField(default=True)
    inactive_on = models.BooleanField(default=True)
    inactive_after_days = models.PositiveIntegerField(default=7, help_text="Active members not seen for this many days go on the call list.")
    owner_summary_on = models.BooleanField(default=True)
    owner_summary_phone = models.CharField(max_length=20, blank=True, help_text="Blank = the owner's own mobile.")
    tpl_renewal = models.TextField(blank=True)
    tpl_expired = models.TextField(blank=True)
    tpl_dues = models.TextField(blank=True)
    tpl_birthday = models.TextField(blank=True)
    tpl_inactive = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def for_client(cls, client):
        obj, _ = cls.objects.get_or_create(client=client)
        return obj

    def offsets(self):
        out = []
        for part in self.renewal_days.replace(" ", "").split(","):
            try:
                out.append(int(part))
            except ValueError:
                pass
        return sorted(set(out), reverse=True)


class Reminder(models.Model):
    KIND_RENEWAL, KIND_EXPIRED, KIND_DUES = "renewal", "expired", "dues"
    KIND_BIRTHDAY, KIND_INACTIVE, KIND_SUMMARY = "birthday", "inactive", "owner_summary"
    KINDS = [KIND_RENEWAL, KIND_EXPIRED, KIND_DUES, KIND_BIRTHDAY, KIND_INACTIVE, KIND_SUMMARY]
    PENDING, SENT, SKIPPED, DONE = "pending", "sent", "skipped", "done"
    STATUSES = [PENDING, SENT, SKIPPED, DONE]
    CALL_OUTCOMES = ["Reached", "Coming back", "No answer", "Not interested", "Moved / left", "Other"]

    client = models.ForeignKey(Client, related_name="reminders", on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    key = models.CharField(max_length=120, help_text="Stops the same reminder being made twice.")
    for_date = models.DateField(help_text="The day's list this item belongs to (gym's time zone).")
    member = models.ForeignKey(Member, null=True, blank=True, related_name="reminders", on_delete=models.CASCADE)
    phone = models.CharField(max_length=20)
    text = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    upi_link = models.CharField(max_length=300, blank=True)
    days = models.IntegerField(null=True, blank=True, help_text="Days left (renewal), days since (expired / inactive).")
    meta = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, default=PENDING)
    channel = models.CharField(max_length=10, default="link", help_text="link = sent by tapping WhatsApp; meta = sent automatically.")
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(StaffUser, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    sent_device = models.ForeignKey(StaffDevice, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    call_outcome = models.CharField(max_length=30, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["for_date", "kind", "id"]
        constraints = [models.UniqueConstraint(fields=["client", "key"], name="unique_reminder_key_per_gym")]
        indexes = [models.Index(fields=["client", "for_date"]), models.Index(fields=["client", "status"])]


class DailyRun(models.Model):
    """One row per gym per day once the morning job has run (by the
    scheduled command, or on first opening of the day's list)."""

    client = models.ForeignKey(Client, related_name="daily_runs", on_delete=models.CASCADE)
    date = models.DateField()
    ran_at = models.DateTimeField(auto_now=True)
    created = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["client", "date"], name="unique_daily_run_per_gym")]


class EmailSettings(models.Model):
    """How this gym sends email (receipts, diet plans, reminders).

    "own": the gym's own mailbox over SMTP (e.g. Gmail with an app
    password), so members see the gym's address and replies come back to it.
    "fitdesk": the server's mailbox (EMAIL_HOST etc. on the server), with the
    gym's email as Reply-To. The password is stored encrypted."""

    PROVIDERS = {
        "gmail": ("smtp.gmail.com", 587, "tls"),
        "outlook": ("smtp.office365.com", 587, "tls"),
        "zoho": ("smtp.zoho.in", 587, "tls"),
        "custom": ("", 587, "tls"),
    }
    client = models.OneToOneField(Client, related_name="email_settings", on_delete=models.CASCADE)
    mode = models.CharField(max_length=10, default="own", help_text="own = gym's mailbox; fitdesk = the server's mailbox.")
    provider = models.CharField(max_length=10, default="gmail")
    sender_email = models.EmailField(blank=True)
    from_name = models.CharField(max_length=120, blank=True, help_text="Blank = the gym's name.")
    reply_to = models.EmailField(blank=True)
    smtp_host = models.CharField(max_length=120, blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    security = models.CharField(max_length=5, default="tls", help_text="tls (port 587) or ssl (port 465).")
    username = models.CharField(max_length=254, blank=True, help_text="Blank = the sender email.")
    password_enc = models.TextField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True, help_text="When a test email last went through.")
    last_error = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def for_client(cls, client):
        obj, _ = cls.objects.get_or_create(client=client)
        return obj


class EmailLog(models.Model):
    """Every email the gym sends, for "was it sent?" questions."""
    client = models.ForeignKey(Client, related_name="email_logs", on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)               # receipt / diet_plan / reminder / test
    ref = models.CharField(max_length=60, blank=True)    # receipt no., plan id, reminder id
    member = models.ForeignKey(Member, null=True, blank=True, related_name="email_logs", on_delete=models.SET_NULL)
    to = models.CharField(max_length=254)
    subject = models.CharField(max_length=200)
    status = models.CharField(max_length=10)             # sent / failed
    error = models.CharField(max_length=300, blank=True)
    sent_by = models.ForeignKey(StaffUser, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
