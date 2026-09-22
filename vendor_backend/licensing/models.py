import secrets
from datetime import date, timedelta

from django.db import models
from django.utils import timezone

from .crypto_utils import encrypt_token, decrypt_token


def generate_license_key():
    """Human-friendly key: FDPX-XXXX-XXXX-XXXX"""
    parts = [secrets.token_hex(2).upper() for _ in range(3)]
    return "FDPX-" + "-".join(parts)


def generate_connect_session_token():
    # A plain function (not a lambda) -- Django's migration writer can't
    # serialize a lambda default into a migration file.
    return secrets.token_urlsafe(32)


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

    # Feature-entitlement layer (added for the licensing/feature-entitlement
    # architecture -- see Phase 2 design doc). `plan` is nullable only so
    # existing rows survive the migration for the instant before the data
    # migration backfills every License onto the "Legacy Full Access" plan;
    # a License created after that point should always have one.
    plan = models.ForeignKey("Plan", null=True, blank=True, on_delete=models.PROTECT, related_name="licenses")

    # max_activations is kept (read-only, never deleted) for historical
    # compatibility with any external tooling/reports that already refer to
    # it, but was never actually enforced anywhere in this codebase. New
    # code should use max_devices below instead.
    max_activations = models.PositiveIntegerField(default=1)
    max_devices = models.PositiveIntegerField(default=1, help_text="How many devices may be simultaneously activated on this license.")

    # machine_id is kept, unmodified, purely for backward compatibility with
    # any already-installed FitDesk Pro build that doesn't yet understand
    # the Device model. New activations go through Device (see below); this
    # field is no longer the source of truth once a License has Device rows,
    # but nothing here stops writing to it so an old EXE's read of it (if
    # any external code relies on it) keeps working.
    machine_id = models.CharField(max_length=255, blank=True, help_text="Legacy single-device field -- superseded by the Device model. Kept for backward compatibility only.")

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

    @property
    def resolved_features(self):
        """The final feature-code set this license grants: everything its
        Plan includes, plus any license-level override=True add-ons, minus
        any override=False explicit revocations. A License with no Plan
        (shouldn't normally happen post-migration) resolves to an empty
        set rather than raising, so a misconfigured license fails locked,
        not open."""
        plan_codes = set(self.plan.features.values_list("code", flat=True)) if self.plan_id else set()
        overrides = {
            ov.feature.code: ov.enabled
            for ov in self.feature_overrides.select_related("feature").all()
        }
        for code, enabled in overrides.items():
            if enabled:
                plan_codes.add(code)
            else:
                plan_codes.discard(code)
        return sorted(plan_codes)

    @property
    def active_device_count(self):
        return self.devices.filter(status="active").count()

    @property
    def device_status(self):
        """active | limit_reached -- what validate_license reports so the
        desktop can show a clear message instead of a generic error."""
        if self.active_device_count < self.max_devices:
            return "active"
        return "limit_reached"

    def get_or_activate_device(self, fingerprint, device_name=""):
        """The ONE place device binding happens -- called from both
        views.validate_license and license_utils.resolve_active_client_and_license
        so the two code paths can never disagree about which machines this
        license is allowed to run on (previously validate_license and the
        WhatsApp endpoints each had their own copy of this logic against
        the old single machine_id field).

        Returns (device_or_none, error_code_or_none). error_code is one of:
        "device_blocked", "device_limit_reached". Updates last_seen_at on
        an existing active device as a side effect.
        """
        if not fingerprint:
            return None, None

        try:
            device = self.devices.get(fingerprint=fingerprint)
        except Device.DoesNotExist:
            device = None

        if device is not None:
            if device.status == Device.STATUS_BLOCKED:
                return None, "device_blocked"
            device.last_seen_at = timezone.now()
            if device_name and not device.device_name:
                device.device_name = device_name
            device.save(update_fields=["last_seen_at", "device_name"])
            return device, None

        if self.active_device_count >= self.max_devices:
            return None, "device_limit_reached"

        device = self.devices.create(
            fingerprint=fingerprint,
            device_name=device_name,
            status=Device.STATUS_ACTIVE,
        )
        return device, None


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


class WhatsAppSendLog(models.Model):
    """One row per /api/send-whatsapp-receipt/ attempt -- both the audit
    trail the admin dashboard shows, and the source of truth
    licensing/ratelimit.py counts against for the per-license rate limit.
    Every attempt that got far enough to be validated is logged here,
    success or failure, so a sustained abuse pattern is visible and the
    rate limiter has something real to count."""

    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    MODE_TEST = "test"
    MODE_PRODUCTION = "production"
    MODE_CHOICES = [
        (MODE_TEST, "Test"),
        (MODE_PRODUCTION, "Production"),
    ]

    client = models.ForeignKey(Client, related_name="whatsapp_sends", on_delete=models.CASCADE)
    license = models.ForeignKey(License, related_name="whatsapp_sends", on_delete=models.CASCADE)
    phone = models.CharField(max_length=20)
    message_type = models.CharField(max_length=40)
    document_reference = models.CharField(max_length=100, blank=True)
    mode = models.CharField(max_length=12, choices=MODE_CHOICES, default=MODE_PRODUCTION)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    error_code = models.CharField(max_length=40, blank=True)
    provider_message_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["license", "created_at"]),
        ]

    def __str__(self):
        return f"{self.phone} — {self.message_type} ({self.status})"


class WhatsAppConfiguration(models.Model):
    """Exactly one per Client -- this gym's OWN Meta WhatsApp Business
    account, connected via Embedded Signup. There is deliberately no
    global/shared WhatsApp credential anywhere in this project: every
    send for this client uses only the token stored here, and this row
    only ever comes from that client's own successful connect flow (see
    licensing/views.py's whatsapp_connect_callback), never from a value
    the desktop app or any request body supplies directly.

    access_token is never stored in plaintext -- see
    licensing/crypto_utils.py. It's also excluded from admin list views
    and from every API response this backend returns."""

    STATUS_NOT_CONNECTED = "NOT_CONNECTED"
    STATUS_CONNECTED = "CONNECTED"
    STATUS_ERROR = "ERROR"
    STATUS_CHOICES = [
        (STATUS_NOT_CONNECTED, "Not connected"),
        (STATUS_CONNECTED, "Connected"),
        (STATUS_ERROR, "Error"),
    ]

    client = models.OneToOneField(Client, related_name="whatsapp_configuration", on_delete=models.CASCADE)

    enabled = models.BooleanField(default=False)
    connection_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NOT_CONNECTED)

    business_name = models.CharField(max_length=200, blank=True)
    phone_number = models.CharField(max_length=32, blank=True, help_text="Display number, e.g. +91 98765 43210")
    phone_number_id = models.CharField(max_length=64, blank=True, help_text="Meta phone_number_id")
    waba_id = models.CharField(max_length=64, blank=True, help_text="Meta WhatsApp Business Account ID")

    # Never read/written directly -- always go through
    # access_token / set_access_token() below.
    access_token_encrypted = models.TextField(blank=True)
    token_type = models.CharField(max_length=30, blank=True, default="system_user")

    template_name = models.CharField(max_length=100, blank=True, default="receipt_notification")
    template_language = models.CharField(max_length=10, blank=True, default="en_US")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_connection_check = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "WhatsApp configuration"
        verbose_name_plural = "WhatsApp configurations"

    def __str__(self):
        return f"{self.client.gym_name} — {self.connection_status}"

    @property
    def access_token(self):
        return decrypt_token(self.access_token_encrypted)

    def set_access_token(self, plaintext_token):
        self.access_token_encrypted = encrypt_token(plaintext_token)

    @property
    def has_token(self):
        return bool(self.access_token_encrypted)

    def is_ready(self):
        """True only when there's a decryptable token AND a phone number
        id AND the connection is marked CONNECTED -- the exact bar
        licensing/whatsapp_provider.py's per-client provider factory
        checks before it will attempt any Meta call for this client."""
        return bool(
            self.enabled
            and self.connection_status == self.STATUS_CONNECTED
            and self.phone_number_id
            and self.access_token
        )

    def disconnect(self):
        """Clears this Client's WhatsApp connection only -- never touches
        the Client, License, or any other Client's configuration."""
        self.enabled = False
        self.connection_status = self.STATUS_NOT_CONNECTED
        self.access_token_encrypted = ""
        self.phone_number_id = ""
        self.waba_id = ""
        self.business_name = ""
        self.phone_number = ""
        self.last_error = ""
        self.save()


class WhatsAppConnectSession(models.Model):
    """A short-lived, single-use token tying one Meta Embedded Signup
    attempt back to the exact Client that started it (via a validated
    license+machine_id at /api/whatsapp/connect/start/). The signup
    landing page (a plain web page the desktop app opens in the system
    browser -- Meta's JS SDK needs a real page context, not a native
    widget) is the only thing that carries this token, and
    /api/whatsapp/connect/callback/ refuses to attach WhatsApp assets to
    any Client without a live, matching, unconsumed session -- this is
    what makes it impossible for one gym's completed signup to land on
    a different gym's Client record."""

    token = models.CharField(max_length=64, unique=True, default=generate_connect_session_token)
    client = models.ForeignKey(Client, related_name="whatsapp_connect_sessions", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"connect-session for {self.client.gym_name} ({'consumed' if self.consumed_at else 'pending'})"

    @classmethod
    def create_for(cls, client, ttl_minutes=15):
        return cls.objects.create(client=client, expires_at=timezone.now() + timedelta(minutes=ttl_minutes))

    @property
    def is_valid(self):
        return self.consumed_at is None and timezone.now() < self.expires_at

    def consume(self):
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])


# ==========================================================================
# FEATURE ENTITLEMENT LAYER
#
# Kept deliberately separate from License validity (active/expired/
# suspended -- still just fields on License above) and from device
# activation (the Device model below). See the Phase 2 design doc
# (project doc: claude/licensing-phase2-design.md) for the full rationale.
# ==========================================================================

class Feature(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_RETIRED, "Retired"),
    ]

    code = models.SlugField(max_length=40, unique=True, help_text="Stable identifier the desktop app checks, e.g. 'whatsapp'. Never change once shipped.")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name


class Plan(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_RETIRED, "Retired"),
    ]

    plan_id = models.SlugField(max_length=40, unique=True, help_text="e.g. 'basic', 'standard', 'premium'.")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    features = models.ManyToManyField(Feature, through="PlanFeature", related_name="plans", blank=True)

    class Meta:
        ordering = ["plan_id"]

    def __str__(self):
        return self.name


class PlanFeature(models.Model):
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE)
    feature = models.ForeignKey(Feature, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("plan", "feature")

    def __str__(self):
        return f"{self.plan.plan_id} → {self.feature.code}"


class LicenseFeatureOverride(models.Model):
    """A license-level add-on or revoke on top of whatever its Plan
    grants. enabled=True is an add-on purchased separately from the plan
    (e.g. WhatsApp bought on top of Basic); enabled=False is an explicit
    revoke even if the plan would otherwise include it (rare, but keeps
    the override table symmetric rather than needing two different
    mechanisms for the two directions)."""

    license = models.ForeignKey(License, related_name="feature_overrides", on_delete=models.CASCADE)
    feature = models.ForeignKey(Feature, on_delete=models.CASCADE)
    enabled = models.BooleanField(help_text="True = add-on grant. False = explicit revoke, overriding the plan.")
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True, help_text="Optional -- why this override exists (e.g. 'WhatsApp add-on, paid 2026-09-22').")

    class Meta:
        unique_together = ("license", "feature")

    def __str__(self):
        return f"{self.license.key}: {self.feature.code} = {self.enabled}"


class Device(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_BLOCKED, "Blocked"),
    ]

    license = models.ForeignKey(License, related_name="devices", on_delete=models.CASCADE)
    fingerprint = models.CharField(max_length=128, help_text="The desktop's stable machine id (see utils/machine_id.py).")
    device_name = models.CharField(max_length=120, blank=True, help_text="Informational only -- hostname reported at activation time.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    activated_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-activated_at"]
        unique_together = ("license", "fingerprint")

    def __str__(self):
        return f"{self.device_name or self.fingerprint[:12]} — {self.license.key} ({self.status})"


class AuditLog(models.Model):
    """Append-only. Nothing in this codebase ever updates or deletes an
    AuditLog row -- see AuditLogAdmin.has_add/change/delete_permission."""

    license = models.ForeignKey(License, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    action = models.CharField(max_length=60, help_text="e.g. 'feature_override_changed', 'plan_changed', 'device_activated', 'device_deactivated'.")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["license", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action} — {self.client or self.license} ({self.created_at:%Y-%m-%d %H:%M})"

    @classmethod
    def record(cls, action, license=None, client=None, **metadata):
        return cls.objects.create(
            action=action,
            license=license,
            client=client or (license.client if license else None),
            metadata=metadata,
        )
