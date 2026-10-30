"""Gym staff accounts, roles, invites, devices and sessions.

Design rules (see README_MULTIUSER.md):
- Everything hangs off licensing.Client (the gym). The gym is always
  derived on the server from the signed-in session, never from a value
  the app sends.
- This app only READS licensing models. It adds no field to them, so the
  legacy /api/validate-license/ contract and the installed v1.x desktop
  builds are unaffected.
- Secrets (passwords, PINs, invite codes, setup codes, device secrets,
  tokens) are stored only as hashes.
"""

import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

from licensing.models import Client

from .permissions import ACTIONS, MODULES, ROLE_CHOICES, ROLE_OWNER

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def random_code(length):
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_gym_code():
    return random_code(6)


class GymProfile(models.Model):
    """Per-gym settings for multi-user access. One per Client, created on
    demand. Also the row we lock (select_for_update) when checking user
    and device limits, so two simultaneous approvals can't exceed them."""

    client = models.OneToOneField(Client, related_name="gym_profile", on_delete=models.CASCADE)
    gym_code = models.CharField(max_length=8, unique=True, default=generate_gym_code,
                                help_text="Short public code staff can enter when one mobile number belongs to several gyms.")
    max_users = models.PositiveIntegerField(default=5, help_text="People with a login, owner included.")
    max_devices = models.PositiveIntegerField(default=3, help_text="Approved devices for the new apps.")
    owner_free_devices = models.PositiveIntegerField(default=1, help_text="How many of the owner's own devices don't count against max_devices.")
    time_zone = models.CharField(max_length=40, default="Asia/Kolkata")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Gym access settings"
        verbose_name_plural = "Gym access settings"

    def __str__(self):
        return f"{self.client.gym_name} · {self.gym_code}"

    @classmethod
    def for_client(cls, client):
        obj, _ = cls.objects.get_or_create(client=client)
        return obj


class StaffUser(models.Model):
    """A person who can sign in to the new FitDesk Pro apps for one gym.
    Separate from django.contrib.auth.User, which stays reserved for you
    (the vendor) in the Django admin."""

    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_DISABLED, "Disabled")]

    client = models.ForeignKey(Client, related_name="staff_users", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, help_text="E.164, e.g. +919876543210")
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    password_hash = models.CharField(max_length=256)
    pin_hash = models.CharField(max_length=256, blank=True, help_text="Optional 4-6 digit PIN for quick switching on a shared device.")
    must_change_password = models.BooleanField(default=False)
    # Free-text link to the trainer/staff record in the gym's data
    # (e.g. TRN-004). Becomes a real foreign key once gym data moves to
    # the server in Phase 3.
    linked_record = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["client", "name"]
        constraints = [
            models.UniqueConstraint(fields=["client", "phone"], name="unique_staff_phone_per_gym"),
            models.UniqueConstraint(fields=["client"], condition=models.Q(role=ROLE_OWNER), name="one_owner_per_gym"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) — {self.client.gym_name}"

    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE

    @property
    def is_owner(self):
        return self.role == ROLE_OWNER

    def set_password(self, raw):
        self.password_hash = make_password(raw)

    def check_password(self, raw):
        return bool(self.password_hash) and check_password(raw, self.password_hash)

    def set_pin(self, raw):
        self.pin_hash = make_password(raw) if raw else ""

    def check_pin(self, raw):
        return bool(self.pin_hash) and check_password(raw, self.pin_hash)


class PermissionOverride(models.Model):
    """Owner's per-person adjustment on top of the role default, e.g. let
    one receptionist also export enquiries."""

    user = models.ForeignKey(StaffUser, related_name="permission_overrides", on_delete=models.CASCADE)
    module = models.CharField(max_length=30, choices=MODULES)
    action = models.CharField(max_length=10, choices=[(a, a) for a in ACTIONS])
    allowed = models.BooleanField()

    class Meta:
        unique_together = ("user", "module", "action")

    def __str__(self):
        return f"{self.user.name}: {self.module}.{self.action} = {self.allowed}"


class Invite(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [(STATUS_PENDING, "Pending"), (STATUS_ACCEPTED, "Accepted"), (STATUS_CANCELLED, "Cancelled")]

    TTL_HOURS = 48
    CODE_LENGTH = 8

    client = models.ForeignKey(Client, related_name="staff_invites", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    linked_record = models.CharField(max_length=60, blank=True)
    salary_access = models.CharField(max_length=10, default="none",
                                     help_text="none / view / manage: salaries & payroll access given on joining.")
    code_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    invited_by = models.ForeignKey(StaffUser, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    accepted_user = models.ForeignKey(StaffUser, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invite {self.name} as {self.role} — {self.client.gym_name} ({self.status})"

    @property
    def is_usable(self):
        return self.status == self.STATUS_PENDING and timezone.now() < self.expires_at

    @classmethod
    def new(cls, **fields):
        """Returns (invite, plaintext_code). The code is shown once."""
        code = random_code(cls.CODE_LENGTH)
        inv = cls.objects.create(code_hash=sha256(code),
                                 expires_at=timezone.now() + timedelta(hours=cls.TTL_HOURS), **fields)
        return inv, code


class OwnerSetupCode(models.Model):
    """One-time code you (the vendor) issue from the Django admin so a
    gym owner can create the first Owner login. The licence key alone is
    not enough, so a leaked key can't be used to take over a gym."""

    TTL_DAYS = 7
    CODE_LENGTH = 10

    client = models.ForeignKey(Client, related_name="owner_setup_codes", on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64, unique=True, editable=False)
    code_hint = models.CharField(max_length=4, editable=False, help_text="Last 4 characters, for reference.")
    created_by = models.CharField(max_length=150, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(editable=False)
    used_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Owner setup code …{self.code_hint} — {self.client.gym_name}"

    @property
    def is_usable(self):
        return self.used_at is None and timezone.now() < self.expires_at

    @classmethod
    def issue(cls, client, created_by=""):
        code = random_code(cls.CODE_LENGTH)
        obj = cls.objects.create(client=client, code_hash=sha256(code), code_hint=code[-4:],
                                 created_by=created_by, expires_at=timezone.now() + timedelta(days=cls.TTL_DAYS))
        return obj, code


class StaffDevice(models.Model):
    """A PC, Mac, phone or tablet running the new app for one gym. Every
    device needs the owner's approval before anyone can sign in on it."""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_REMOVED = "removed"
    STATUS_CHOICES = [(STATUS_PENDING, "Waiting for approval"), (STATUS_APPROVED, "Approved"),
                      (STATUS_REJECTED, "Rejected"), (STATUS_REMOVED, "Removed")]

    PLATFORMS = [("windows", "Windows"), ("macos", "macOS"), ("android", "Android"), ("ios", "iOS"),
                 ("web", "Web"), ("linux", "Linux")]

    client = models.ForeignKey(Client, related_name="staff_devices", on_delete=models.CASCADE)
    # Random id the app generates on first launch and keeps in secure
    # storage. Not a MAC/hostname hash, so it works on phones and survives
    # network-card changes.
    installation_id = models.CharField(max_length=64)
    secret_hash = models.CharField(max_length=64, help_text="sha256 of the device secret the app keeps in secure storage.")
    name = models.CharField(max_length=80)
    platform = models.CharField(max_length=10, choices=PLATFORMS)
    app_version = models.CharField(max_length=30, blank=True)
    shared = models.BooleanField(default=False, help_text="Front-desk PC: any active staff of this gym may sign in, with PIN switching.")
    registered_by = models.ForeignKey(StaffUser, null=True, related_name="registered_devices", on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    decided_by = models.ForeignKey(StaffUser, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["client", "installation_id"], name="unique_installation_per_gym"),
        ]

    def __str__(self):
        return f"{self.name} ({self.platform}) — {self.client.gym_name} [{self.status}]"

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED

    def check_secret(self, raw):
        return bool(raw) and secrets.compare_digest(self.secret_hash, sha256(raw))

    def usable_by(self, user):
        return self.shared or self.registered_by_id == user.id


class StaffSession(models.Model):
    """One signed-in user on one device. Holds hashes of the short-lived
    access token and the rotating refresh token. Revoking a session (or
    disabling the user, or removing the device) takes effect on the very
    next API call, because every call re-checks all three."""

    ACCESS_TTL = timedelta(minutes=30)
    REFRESH_TTL = timedelta(days=30)

    user = models.ForeignKey(StaffUser, related_name="sessions", on_delete=models.CASCADE)
    device = models.ForeignKey(StaffDevice, related_name="sessions", on_delete=models.CASCADE)
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    access_expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def start(cls, user, device):
        """Returns (session, access_token, refresh_token)."""
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = timezone.now()
        s = cls.objects.create(user=user, device=device, access_hash=sha256(access), refresh_hash=sha256(refresh),
                               access_expires_at=now + cls.ACCESS_TTL, refresh_expires_at=now + cls.REFRESH_TTL)
        return s, access, refresh

    def rotate(self):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        now = timezone.now()
        self.access_hash, self.refresh_hash = sha256(access), sha256(refresh)
        self.access_expires_at, self.refresh_expires_at = now + self.ACCESS_TTL, now + self.REFRESH_TTL
        self.last_used_at = now
        self.save(update_fields=["access_hash", "refresh_hash", "access_expires_at", "refresh_expires_at", "last_used_at"])
        return access, refresh

    def revoke(self, reason):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.revoked_reason = reason
            self.save(update_fields=["revoked_at", "revoked_reason"])

    @classmethod
    def revoke_all(cls, reason, **filters):
        return cls.objects.filter(revoked_at__isnull=True, **filters).update(revoked_at=timezone.now(), revoked_reason=reason)


class AuthAttempt(models.Model):
    """Counter for rate limiting sign-in, invite and setup attempts. Kept
    in the database (not memory) so it's correct across gunicorn workers
    and restarts, the same reasoning as licensing/ratelimit.py."""

    kind = models.CharField(max_length=20)
    key = models.CharField(max_length=120)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "key", "created_at"])]


class TeamActivity(models.Model):
    """Append-only log of who did what in Team & Access (and, in later
    phases, billing/edits). Shown to the owner as the Activity log tab."""

    client = models.ForeignKey(Client, related_name="team_activity", on_delete=models.CASCADE)
    actor = models.ForeignKey(StaffUser, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    actor_label = models.CharField(max_length=150, blank=True, help_text="Name at the time, kept if the user is later removed.")
    device = models.ForeignKey(StaffDevice, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)
    target = models.CharField(max_length=150, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["client", "created_at"])]
        verbose_name_plural = "Team activity"

    def __str__(self):
        return f"{self.actor_label or 'system'}: {self.action} {self.target}"

    @classmethod
    def record(cls, client, action, actor=None, device=None, target="", **metadata):
        return cls.objects.create(client=client, actor=actor, actor_label=actor.name if actor else "",
                                  device=device, action=action, target=str(target)[:150], metadata=metadata)
