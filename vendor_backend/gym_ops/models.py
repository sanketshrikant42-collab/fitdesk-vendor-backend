"""Phase 3B: staff, staff attendance, payroll, expenses, enquiries with
referrals, diet & nutrition, progress tracking, consent forms.

Follows FitDesk Pro v1.2.0 (GymERP) rules unless noted in
README_PHASE3B.md. Every row belongs to one gym; the gym always comes
from the signed-in session.
"""

import uuid
from decimal import Decimal

from django.db import models

from gym_accounts.models import StaffDevice, StaffUser
from gym_core.models import Member, Membership, Plan, Trainer
from licensing.models import Client

ZERO = Decimal("0.00")


def money_field(**kw):
    return models.DecimalField(max_digits=12, decimal_places=2, default=ZERO, **kw)


# --- staff & payroll ---------------------------------------------------------------

class Staff(models.Model):
    BASIS_CALENDAR, BASIS_FIXED30 = "Calendar Days", "Fixed 30 Days"   # v1.2.0 salary_basis values

    client = models.ForeignKey(Client, related_name="staff", on_delete=models.CASCADE)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    gender = models.CharField(max_length=10, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=30, blank=True)
    designation = models.CharField(max_length=80)
    department = models.CharField(max_length=80, blank=True)
    joining_date = models.DateField()
    monthly_salary = money_field()
    salary_basis = models.CharField(max_length=20, default=BASIS_CALENDAR)
    status = models.CharField(max_length=10, default="active")
    trainer = models.OneToOneField(Trainer, null=True, blank=True, related_name="staff_record", on_delete=models.SET_NULL,
                                   help_text="Set when this staff member is also a trainer (PT commission is paid with salary).")
    staff_user = models.OneToOneField(StaffUser, null=True, blank=True, related_name="staff_record", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["client", "code"], name="unique_staff_record_code_per_gym"),
                       models.UniqueConstraint(fields=["client", "phone"], name="unique_staff_record_phone_per_gym")]

    def __str__(self):
        return f"{self.name} ({self.code})"


class StaffAttendance(models.Model):
    STATUSES = ["Present", "Half Day", "Absent", "Paid Leave", "Weekly Off", "Holiday"]   # v1.2.0

    staff = models.ForeignKey(Staff, related_name="attendance", on_delete=models.CASCADE)
    date = models.DateField()
    status = models.CharField(max_length=12)
    check_in = models.CharField(max_length=5, blank=True)
    check_out = models.CharField(max_length=5, blank=True)
    remarks = models.CharField(max_length=200, blank=True)
    marked_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("staff", "date")
        ordering = ["date"]


class SalaryAdvance(models.Model):
    staff = models.ForeignKey(Staff, related_name="advances", on_delete=models.CASCADE)
    date = models.DateField()
    amount = money_field()
    mode = models.CharField(max_length=20)
    reference_no = models.CharField(max_length=60, blank=True)
    remarks = models.CharField(max_length=200, blank=True)
    given_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class PayrollSettlement(models.Model):
    """One per staff member per month, as v1.2.0's payroll_settlements:
    salary + pending PT commission paid together."""

    staff = models.ForeignKey(Staff, related_name="settlements", on_delete=models.CASCADE)
    month = models.CharField(max_length=7, help_text="YYYY-MM")
    earned = money_field()
    advance = money_field()
    deduction = money_field()
    salary_amount = money_field(help_text="Net salary paid.")
    pt_commission = money_field()
    total = money_field()
    breakdown = models.JSONField(default=dict, help_text="Attendance counts and rates used, kept for the record.")
    payment_date = models.DateField()
    mode = models.CharField(max_length=20)
    reference_no = models.CharField(max_length=60, blank=True)
    remarks = models.CharField(max_length=300, blank=True)
    paid_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("staff", "month")
        ordering = ["-month"]


class TrainerPayout(models.Model):
    """PT commission paid to a trainer for an earning month (v1.2.0 trainer_payouts)."""

    client = models.ForeignKey(Client, related_name="trainer_payouts", on_delete=models.CASCADE)
    trainer = models.ForeignKey(Trainer, related_name="payouts", on_delete=models.CASCADE)
    earning_month = models.CharField(max_length=7)
    amount = money_field()
    payout_date = models.DateField()
    mode = models.CharField(max_length=20)
    reference_no = models.CharField(max_length=60, blank=True)
    settlement = models.ForeignKey(PayrollSettlement, null=True, blank=True, related_name="payouts", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


# --- expenses -----------------------------------------------------------------------

class Expense(models.Model):
    CATEGORIES = ["Rent", "Electricity", "Water", "Internet", "Maintenance", "Equipment", "Cleaning", "Marketing",
                  "Office/Admin", "Software", "Travel", "Salaries", "Trainer Payout", "Other"]          # v1.2.0
    MODES = ["Cash", "UPI", "Bank Transfer", "Card", "Cheque", "Other"]                                  # v1.2.0
    STATUSES = ["Paid", "Pending"]

    client = models.ForeignKey(Client, related_name="expenses", on_delete=models.CASCADE)
    date = models.DateField()
    category = models.CharField(max_length=30)
    title = models.CharField(max_length=150)
    amount = money_field()
    mode = models.CharField(max_length=20)
    reference_no = models.CharField(max_length=60, blank=True)
    paid_to = models.CharField(max_length=120, blank=True)
    remarks = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, default="Paid")
    is_deleted = models.BooleanField(default=False)
    created_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["client", "date"])]


# --- enquiries & referrals -------------------------------------------------------------

class Enquiry(models.Model):
    STATUSES = ["New", "Contacted", "Follow-up", "Trial", "Converted", "Lost"]                          # v1.2.0
    OPEN = ["New", "Contacted", "Follow-up", "Trial"]
    SOURCES = ["Walk-in", "Phone Call", "WhatsApp", "Instagram", "Facebook", "Google", "Website", "Referral", "Other"]
    PRIORITIES = ["Hot", "Warm", "Cold"]

    client = models.ForeignKey(Client, related_name="enquiries", on_delete=models.CASCADE)
    code = models.CharField(max_length=20)
    enquiry_date = models.DateField()
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    gender = models.CharField(max_length=10, blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)
    source = models.CharField(max_length=20)
    interested_plan = models.ForeignKey(Plan, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    fitness_goal = models.CharField(max_length=80, blank=True)
    priority = models.CharField(max_length=6, default="Warm")
    status = models.CharField(max_length=12, default="New")
    assigned_to = models.ForeignKey(StaffUser, null=True, blank=True, related_name="assigned_enquiries", on_delete=models.SET_NULL)
    next_follow_up_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    lost_reason = models.CharField(max_length=200, blank=True)
    referred_by = models.ForeignKey(Member, null=True, blank=True, related_name="referrals", on_delete=models.SET_NULL)
    converted_member = models.ForeignKey(Member, null=True, blank=True, related_name="from_enquiry", on_delete=models.SET_NULL)
    converted_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-enquiry_date", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["client", "code"], name="unique_enquiry_code_per_gym")]
        indexes = [models.Index(fields=["client", "status", "next_follow_up_date"])]


class EnquiryFollowUp(models.Model):
    MODES = ["Call", "WhatsApp", "Visit", "SMS", "Email", "Other"]                                     # v1.2.0

    enquiry = models.ForeignKey(Enquiry, related_name="followups", on_delete=models.CASCADE)
    date = models.DateField()
    mode = models.CharField(max_length=10)
    notes = models.CharField(max_length=500, blank=True)
    status_before = models.CharField(max_length=12)
    status_after = models.CharField(max_length=12)
    next_follow_up_date = models.DateField(null=True, blank=True)
    by_user = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ReferralReward(models.Model):
    """Free days earned by a member whose referral joined."""

    STATUS_PENDING, STATUS_APPLIED = "pending", "applied"

    client = models.ForeignKey(Client, related_name="referral_rewards", on_delete=models.CASCADE)
    referrer = models.ForeignKey(Member, related_name="referral_rewards", on_delete=models.CASCADE)
    enquiry = models.OneToOneField(Enquiry, related_name="reward", on_delete=models.CASCADE)
    days = models.PositiveIntegerField()
    status = models.CharField(max_length=10, default=STATUS_PENDING)
    applied_to = models.ForeignKey(Membership, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


# --- diet & nutrition ---------------------------------------------------------------------

class Food(models.Model):
    client = models.ForeignKey(Client, related_name="foods", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    serving_qty = models.FloatField(null=True, blank=True)
    serving_unit = models.CharField(max_length=20, blank=True)
    kcal = models.FloatField(null=True, blank=True)
    protein_g = models.FloatField(null=True, blank=True)
    carbs_g = models.FloatField(null=True, blank=True)
    fat_g = models.FloatField(null=True, blank=True)
    allergens = models.CharField(max_length=60, blank=True)
    source = models.CharField(max_length=10, default="manual")
    source_note = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["client", "name"], name="unique_food_name_per_gym")]

    def as_dict(self):
        return {"id": self.id, "name": self.name, "serving_qty": self.serving_qty, "serving_unit": self.serving_unit,
                "kcal": self.kcal, "protein_g": self.protein_g, "carbs_g": self.carbs_g, "fat_g": self.fat_g,
                "allergens": self.allergens, "source": self.source, "source_note": self.source_note,
                "is_active": self.is_active}


class DietTemplate(models.Model):
    client = models.ForeignKey(Client, related_name="diet_templates", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    goal = models.CharField(max_length=30, blank=True)
    diet_type = models.CharField(max_length=30, blank=True)
    weeks = models.PositiveIntegerField(null=True, blank=True)
    instructions = models.TextField(blank=True)
    meals = models.JSONField(default=list)
    source_starter_key = models.CharField(max_length=80, blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)


class DietPlan(models.Model):
    STATUSES = ["Draft", "Active", "Completed", "Archived"]                                            # v1.2.0
    TRANSITIONS = {"Draft": {"Active", "Archived"}, "Active": {"Completed", "Archived"},
                   "Completed": {"Active", "Archived"}, "Archived": {"Active"}}

    client = models.ForeignKey(Client, related_name="diet_plans", on_delete=models.CASCADE)
    member = models.ForeignKey(Member, related_name="diet_plans", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    goal = models.CharField(max_length=30, blank=True)
    diet_type = models.CharField(max_length=30, blank=True)
    trainer = models.ForeignKey(Trainer, null=True, blank=True, related_name="diet_plans", on_delete=models.SET_NULL)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    review_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default="Draft")
    current_version = models.PositiveIntegerField(default=1)
    source_type = models.CharField(max_length=10, default="scratch")
    source_ref = models.CharField(max_length=80, blank=True)
    created_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class DietPlanVersion(models.Model):
    plan = models.ForeignKey(DietPlan, related_name="versions", on_delete=models.CASCADE)
    version_no = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    goal = models.CharField(max_length=30, blank=True)
    diet_type = models.CharField(max_length=30, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    review_date = models.DateField(null=True, blank=True)
    instructions = models.TextField(blank=True)
    change_note = models.CharField(max_length=200, blank=True)
    meals = models.JSONField(default=list, help_text="Snapshot incl. calculated nutrition, so later library edits don't change it.")
    created_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("plan", "version_no")
        ordering = ["version_no"]


class DietShare(models.Model):
    plan = models.ForeignKey(DietPlan, related_name="shares", on_delete=models.CASCADE)
    version_no = models.PositiveIntegerField()
    channel = models.CharField(max_length=20, default="whatsapp_link")
    recipient = models.CharField(max_length=40, blank=True)
    by_user = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


# --- progress & consent -------------------------------------------------------------------

def progress_photo_path(instance, filename):
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()[:5]
    return f"gyms/GYM-{instance.member.client_id}/progress/{instance.member_id}/{uuid.uuid4().hex}.{ext}"


class ProgressEntry(models.Model):
    member = models.ForeignKey(Member, related_name="progress", on_delete=models.CASCADE)
    date = models.DateField()
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    body_fat_percent = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    chest_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    waist_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    hips_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    arms_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    thighs_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    photo = models.FileField(upload_to=progress_photo_path, blank=True)
    recorded_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at"]


class ConsentForm(models.Model):
    member = models.ForeignKey(Member, related_name="consents", on_delete=models.CASCADE)
    text = models.TextField(help_text="Exact text the member agreed to.")
    health_answers = models.JSONField(default=dict, blank=True)
    signed_name = models.CharField(max_length=120)
    signature_png = models.TextField(help_text="data:image/png;base64,... drawn signature")
    signed_at = models.DateTimeField(auto_now_add=True)
    witnessed_by = models.ForeignKey(StaffUser, null=True, related_name="+", on_delete=models.SET_NULL)
    device = models.ForeignKey(StaffDevice, null=True, related_name="+", on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-signed_at"]
