"""Core gym data on the server (Phase 3A): settings, plans, trainers,
members, memberships, bills and payments, freezes, PT sessions, check-ins.

Rules follow FitDesk Pro v1.2.0 (GymERP) unless a v1.2.0 bug was found;
each such difference is listed in README_PHASE3A.md and marked "FIX" in
billing.py.

Every row belongs to one gym (licensing.Client). The gym is always taken
from the signed-in session, never from the request.
"""

import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone

from gym_accounts.models import StaffDevice, StaffUser
from licensing.models import Client

ZERO = Decimal("0.00")


def money_field(**kw):
    return models.DecimalField(max_digits=12, decimal_places=2, default=ZERO, **kw)


class GymSettings(models.Model):
    """Receipt and billing settings, stored once for every device.
    Replaces v1.2.0's per-PC app_settings (and its built-in P&P defaults)."""

    client = models.OneToOneField(Client, related_name="gym_settings", on_delete=models.CASCADE)
    gym_name = models.CharField(max_length=200)
    tagline = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    gst_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO,
                                      help_text="0 = GST not set up; bills can't apply GST.")
    apply_gst_by_default = models.BooleanField(default=False)
    member_code_prefix = models.CharField(max_length=6, blank=True, help_text="Blank = initials of the gym name (e.g. P&P Fitness → PPF).")
    receipt_prefix = models.CharField(max_length=8, default="RCPT")
    upi_id = models.CharField(max_length=80, blank=True, help_text="Gym owner's UPI ID, e.g. ppfitness@okaxis")
    upi_payee_name = models.CharField(max_length=80, blank=True)
    receipt_footer = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    whatsapp_receipt_template = models.TextField(blank=True, help_text="Blank = the v1.2.0 default message.")
    # v1.2.0 computes the trainer's PT share on the amount paid INCLUDING
    # GST. Kept as the default so numbers match; a gym can switch it off.
    trainer_share_includes_gst = models.BooleanField(default=True)
    # Phase 3B
    holidays_paid = models.BooleanField(default=True, help_text="Holiday days (gym closed for a festival etc.) count as paid. New gyms: on (decided 28 Oct 2026).")
    managers_see_salaries = models.BooleanField(default=False, help_text="Off: only the owner sees salaries and payroll amounts.")
    referral_reward_days = models.PositiveIntegerField(default=0, help_text="Free days added to the referring member's membership when a referred enquiry joins. 0 = off.")
    enquiry_whatsapp_template = models.TextField(blank=True, help_text="Blank = the v1.2.0 default follow-up message.")
    consent_text = models.TextField(blank=True, help_text="Health declaration / waiver shown at joining. Blank = a basic default.")
    # Gym logo for receipts (PNG, resized on upload). Kept in the database so
    # every device and the PDF receipt can use it without separate file hosting.
    logo = models.BinaryField(null=True, blank=True, editable=False)
    logo_updated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Gym settings"

    def __str__(self):
        return self.gym_name

    @classmethod
    def for_client(cls, client):
        obj, _ = cls.objects.get_or_create(client=client, defaults={"gym_name": client.gym_name})
        return obj


class Counter(models.Model):
    """Per-gym running numbers (member codes, receipt numbers). Read and
    increased inside a row lock, so two desks never get the same number."""

    client = models.ForeignKey(Client, related_name="counters", on_delete=models.CASCADE)
    key = models.CharField(max_length=30)
    value = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("client", "key")


class Plan(models.Model):
    STATUS_ACTIVE, STATUS_ARCHIVED = "active", "archived"

    client = models.ForeignKey(Client, related_name="plans", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    duration_days = models.PositiveIntegerField()
    fee = money_field()
    description = models.TextField(blank=True)
    # Shown on the plans list as "final fee" like v1.2.0; not applied
    # automatically at billing (v1.2.0 doesn't either).
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    is_personal_training = models.BooleanField(default=False)
    trainer_share_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    pt_sessions = models.PositiveIntegerField(default=0, help_text="Session pack size; 0 = not a session pack.")
    members_covered = models.PositiveIntegerField(default=1, help_text="1 = single, 2 = couple, 3+ = family.")
    freeze_max_days = models.PositiveIntegerField(default=0, help_text="Total freeze days allowed per membership; 0 = no freezing.")
    freeze_max_times = models.PositiveIntegerField(default=0)
    gst_inclusive = models.BooleanField(default=False, help_text="The fee already includes GST (at the GST % in settings).")
    status = models.CharField(max_length=10, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["duration_days", "name"]
        constraints = [models.UniqueConstraint(fields=["client", "name"], name="unique_plan_name_per_gym")]

    def __str__(self):
        return self.name


class Trainer(models.Model):
    """Trainer record (as in v1.2.0). Optionally linked to a StaffUser so a
    trainer can sign in and see only their own clients."""

    client = models.ForeignKey(Client, related_name="trainers", on_delete=models.CASCADE)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    gender = models.CharField(max_length=10, blank=True)
    specialization = models.CharField(max_length=120, blank=True)
    joining_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default="active")
    staff_user = models.OneToOneField(StaffUser, null=True, blank=True, related_name="trainer_record", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["client", "code"], name="unique_trainer_code_per_gym"),
                       models.UniqueConstraint(fields=["client", "phone"], name="unique_trainer_phone_per_gym")]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Member(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(Client, related_name="members", on_delete=models.CASCADE)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    gender = models.CharField(max_length=10)
    date_of_birth = models.DateField(null=True, blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)
    address = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=30, blank=True)
    biometric_id = models.CharField(max_length=40, blank=True, help_text="eSSL user ID, for the fingerprint link (Phase 7).")
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(StaffUser, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["client", "code"], name="unique_member_code_per_gym"),
            models.UniqueConstraint(fields=["client", "phone"], name="unique_member_phone_per_gym"),
            models.UniqueConstraint(fields=["client", "biometric_id"], condition=~models.Q(biometric_id=""),
                                    name="unique_biometric_per_gym"),
        ]
        indexes = [models.Index(fields=["client", "name"]), models.Index(fields=["client", "updated_at"])]

    def __str__(self):
        return f"{self.name} ({self.code})"


class FitnessProfile(models.Model):
    member = models.OneToOneField(Member, related_name="fitness", on_delete=models.CASCADE)
    current_weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    target_weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    fitness_goal = models.CharField(max_length=60, blank=True)
    activity_level = models.CharField(max_length=40, blank=True)
    workout_days_per_week = models.PositiveIntegerField(null=True, blank=True)
    target_date = models.DateField(null=True, blank=True)
    diet_type = models.CharField(max_length=40, blank=True)
    food_allergies = models.CharField(max_length=200, blank=True)
    food_preferences = models.CharField(max_length=200, blank=True)
    meals_per_day = models.PositiveIntegerField(null=True, blank=True)
    workout_time = models.CharField(max_length=20, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class Bill(models.Model):
    """One receipt (v1.2.0: a membership_transactions row). Money received
    is recorded as Payment rows, so collecting dues later keeps the
    trainer share right (FIX for v1.2.0's mark-as-paid)."""

    KIND_NEW, KIND_RENEWAL = "new", "renewal"
    STATUS_ACTIVE, STATUS_CANCELLED = "active", "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(Client, related_name="bills", on_delete=models.CASCADE)
    receipt_no = models.CharField(max_length=20)
    member = models.ForeignKey(Member, related_name="bills", on_delete=models.PROTECT, help_text="Who pays.")
    plan = models.ForeignKey(Plan, related_name="bills", on_delete=models.PROTECT)
    kind = models.CharField(max_length=10)
    trainer = models.ForeignKey(Trainer, null=True, blank=True, related_name="bills", on_delete=models.PROTECT)
    fee = money_field()
    discount = money_field()
    gst_applied = models.BooleanField(default=False)
    gst_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    gst_amount = money_field()
    gst_inclusive = models.BooleanField(default=False, help_text="GST was inside the fee (plan price includes GST).")
    total = money_field()
    is_personal_training = models.BooleanField(default=False)
    trainer_share_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    trainer_share_includes_gst = models.BooleanField(default=True)
    remarks = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, default=STATUS_ACTIVE)
    cancelled_reason = models.CharField(max_length=200, blank=True)
    client_ref = models.CharField(max_length=64, blank=True, help_text="App-generated id so a retried save never bills twice.")
    created_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    device = models.ForeignKey(StaffDevice, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["client", "receipt_no"], name="unique_receipt_per_gym"),
            models.UniqueConstraint(fields=["client", "client_ref"], condition=~models.Q(client_ref=""), name="unique_bill_client_ref"),
        ]

    def __str__(self):
        return self.receipt_no


class Payment(models.Model):
    MODES = ["Cash", "UPI", "Card", "Bank Transfer", "Cheque", "Other"]

    bill = models.ForeignKey(Bill, related_name="payments", on_delete=models.CASCADE)
    amount = money_field()
    mode = models.CharField(max_length=20)
    reference_no = models.CharField(max_length=60, blank=True, help_text="UPI reference / UTR, cheque no.")
    paid_on = models.DateField()
    trainer_share = money_field()
    gym_share = money_field()
    received_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    device = models.ForeignKey(StaffDevice, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class Membership(models.Model):
    """The period a member may train. A family/couple bill creates one per
    covered member, all pointing at the same bill."""

    STATUS_ACTIVE, STATUS_CANCELLED = "active", "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(Client, related_name="memberships", on_delete=models.CASCADE)
    member = models.ForeignKey(Member, related_name="memberships", on_delete=models.CASCADE)
    bill = models.ForeignKey(Bill, related_name="memberships", on_delete=models.CASCADE)
    plan = models.ForeignKey(Plan, related_name="memberships", on_delete=models.PROTECT)
    trainer = models.ForeignKey(Trainer, null=True, blank=True, related_name="memberships", on_delete=models.SET_NULL)
    start_date = models.DateField()
    end_date = models.DateField(help_text="Includes days added by freezes.")
    original_end_date = models.DateField()
    sessions_total = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]
        indexes = [models.Index(fields=["client", "end_date"]), models.Index(fields=["member", "end_date"])]


class Freeze(models.Model):
    membership = models.ForeignKey(Membership, related_name="freezes", on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.PositiveIntegerField()
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start_date"]


class SessionLog(models.Model):
    """One personal-training session used from a session pack."""

    membership = models.ForeignKey(Membership, related_name="sessions", on_delete=models.CASCADE)
    trainer = models.ForeignKey(Trainer, null=True, related_name="+", on_delete=models.SET_NULL)
    session_date = models.DateField()
    notes = models.CharField(max_length=200, blank=True)
    logged_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class CheckIn(models.Model):
    SOURCES = [("front_desk", "Front desk"), ("trainer", "Trainer"), ("qr", "QR"), ("biometric", "Biometric")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(Client, related_name="checkins", on_delete=models.CASCADE)
    member = models.ForeignKey(Member, related_name="checkins", on_delete=models.CASCADE)
    membership = models.ForeignKey(Membership, null=True, blank=True, related_name="checkins", on_delete=models.SET_NULL)
    checked_in_at = models.DateTimeField(default=timezone.now)
    local_date = models.DateField(help_text="Date in the gym's time zone.")
    source = models.CharField(max_length=12, choices=SOURCES, default="front_desk")
    override_reason = models.CharField(max_length=120, blank=True, help_text="Set when let in despite expired/frozen.")
    by_user = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    device = models.ForeignKey(StaffDevice, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    client_ref = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-checked_in_at"]
        constraints = [models.UniqueConstraint(fields=["client", "member", "local_date"], name="one_checkin_per_member_per_day")]
        indexes = [models.Index(fields=["client", "local_date"])]
