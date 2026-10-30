"""Copies one gym's FitDesk v1.2.0 data into the new server (Phase 4).

Runs inside one database transaction: if anything fails, nothing is
saved. Every v1.2.0 id is remembered (self.map) so links between records
survive (bill → member, plan, trainer; enquiry → member; payroll → staff).

What is kept exactly as it was: member codes (PPF000001), receipt numbers
(RCPT00001), staff and trainer codes, dates, amounts, trainer shares, and
payment modes. Numbering continues after the highest imported number.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from gym_accounts.models import GymProfile, OwnerSetupCode
from gym_core import rules as R
from gym_core.models import (Bill, Counter, FitnessProfile, GymSettings, Member, Membership, Payment, Plan,
                             Trainer)
from gym_ops.models import (DietPlan, DietPlanVersion, DietShare, DietTemplate, Enquiry, EnquiryFollowUp, Expense,
                            Food, PayrollSettlement, SalaryAdvance, Staff, StaffAttendance, TrainerPayout)
from licensing.models import Client, License
from licensing.phone_utils import validate_whatsapp_phone

from .models import ImportRun

IST = ZoneInfo("Asia/Kolkata")
TEST_NAME = re.compile(r"\b(test|testing|install|verification|restore|dummy|demo|sample)\b", re.I)
PLACEHOLDER_DOB = date(2000, 1, 1)


class ImportProblem(Exception):
    """Stops the import (nothing is saved) with a message for the owner."""


# --- small converters -----------------------------------------------------------------------

def money(v):
    try:
        return R.money(Decimal(str(v if v not in (None, "") else 0)))
    except (InvalidOperation, ValueError):
        return R.ZERO


def to_date(v):
    if not v:
        return None
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def to_dt(v, fallback=None):
    """v1.2.0 stores local (India) time without a zone."""
    if not v:
        return fallback
    s = str(v).strip().replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    return fallback


def fit(model, field, v):
    """Text cut to the field's own length."""
    n = model._meta.get_field(field).max_length or 100000
    return str(v or "").strip()[:n]


def txt(v, n):
    return str(v or "").strip()[:n]


def phone(v):
    p, err = validate_whatsapp_phone(_trunk(v))
    return p if not err else None


def _trunk(v):
    """Same clean-up as the app: 09876543210 / 0091… → +91…"""
    s = str(v or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits.startswith("0091") and len(digits) == 14:
        return "+" + digits[2:]
    if digits.startswith("0") and len(digits.lstrip("0")) == 10:
        return digits.lstrip("0")
    return s


def code_number(code, prefix):
    m = re.fullmatch(re.escape(prefix) + r"(\d+)", code or "")
    return int(m.group(1)) if m else 0


def stamp(model, pk, **fields):
    """Sets auto_now / auto_now_add fields to the original v1.2.0 values."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        model.objects.filter(pk=pk).update(**fields)


# --- the importer ----------------------------------------------------------------------------

class Importer:
    def __init__(self, src, gym_name=None, owner_name="", owner_phone="", licence_days=365, today=None):
        self.src = src
        self.s = src.settings()
        self.gym_name = gym_name or self.s["gym_name"]
        self.owner_name = owner_name or "Owner"
        self.owner_phone = owner_phone or self.s["phone"] or "0000000000"
        self.licence_days = licence_days
        self.today = today or timezone.localdate(timezone=IST)
        self.map = {k: {} for k in ("plan", "trainer", "member", "staff", "bill", "enquiry", "salary", "settlement",
                                    "diet_plan", "diet_version", "template", "food")}
        self.counts = {}            # table → number imported
        self.skipped = {}           # table → [(id, reason)]
        self.changes = []           # (what, detail) things changed on the way in
        self.cleanup = []           # (kind, detail, suggestion)

    # helpers
    def _n(self, key, k=1):
        self.counts[key] = self.counts.get(key, 0) + k

    def _skip(self, table, rid, reason):
        self.skipped.setdefault(table, []).append((rid, reason))

    # entry point
    @transaction.atomic
    def run(self, replace_client=None):
        if replace_client is not None:
            wipe_gym(replace_client)                         # everything of that gym goes (it was an import)
        self.client = Client.objects.create(gym_name=self.gym_name, owner_name=self.owner_name, phone=self.owner_phone,
                                            email=self.s["email"] or "", notes="Imported from FitDesk v1.2.0")
        self.licence = License.objects.create(client=self.client, expiry_date=self.today + timedelta(days=self.licence_days))
        prof = GymProfile.for_client(self.client)
        prof.time_zone = "Asia/Kolkata"
        prof.save()
        self.settings()
        self.plans()
        self.trainers()
        self.staff()
        self.members()
        self.bills()
        self.payroll()
        self.expenses()
        self.enquiries()
        self.foods()
        self.diet()
        self.counters()
        _, self.setup_code = OwnerSetupCode.issue(self.client, created_by="import_v120")
        return self.client

    # settings ---------------------------------------------------------------------------------
    def settings(self):
        s = self.s
        gst = money(s["gst_percent"]) if s["gst_percent"] not in ("", None) else R.ZERO
        prefixes = {re.match(r"[A-Za-z]+", r["member_code"] or "").group(0)
                    for r in self.src.rows("members") if re.match(r"[A-Za-z]+\d+$", r.get("member_code") or "")}
        prefix = "PPF" if "PPF" in prefixes or not prefixes else sorted(prefixes)[0]
        GymSettings.objects.create(
            client=self.client, gym_name=self.gym_name, tagline=txt(s["tagline"], 200), address=s["address"] or "",
            phone=txt(s["phone"], 30), email=txt(s["email"], 254), gstin=txt(s["gstin"], 20), gst_percent=gst,
            member_code_prefix=prefix[:6], receipt_prefix=txt(s["receipt_prefix"], 8) or "RCPT",
            receipt_footer=s["receipt_footer"] or "", terms=s["terms"] or "")
        self.prefix = prefix
        self._n("settings")
        if s["logo_path"]:
            self.cleanup.append(("Logo", f"v1.2.0 logo file: {s['logo_path']}", "Upload it in Settings → Gym logo."))
        if prefix == "PPF" and not self.gym_name.upper().startswith("P"):
            self.changes.append(("Member code prefix", "Kept as PPF so new members continue the same numbering "
                                 "(v1.2.0 used PPF for every gym). It can be changed in Settings."))

    # plans --------------------------------------------------------------------------------------
    def plans(self):
        seen = set()
        for r in self.src.rows("membership_plans"):
            name = txt(r.get("plan_name"), 80) or f"Plan {r['id']}"
            base, n = name, 2
            while name.lower() in seen:
                name = f"{base} ({n})"
                n += 1
            if name != base:
                self.changes.append(("Plan renamed", f"“{base}” appears twice; the second is “{name}”."))
            seen.add(name.lower())
            p = Plan.objects.create(
                client=self.client, name=name, duration_days=max(int(r.get("duration_days") or 1), 1),
                fee=money(r.get("original_fee")), description=txt(r.get("description"), 300),
                discount_percent=money(r.get("discount_percent")), is_personal_training=bool(r.get("is_personal_training")),
                trainer_share_percent=money(r.get("trainer_share_percent")),
                gst_inclusive=False,
                status=Plan.STATUS_ACTIVE if (r.get("status") or "Active").lower() == "active" else Plan.STATUS_ARCHIVED)
            if r.get("gst_inclusive"):
                self.cleanup.append(("Plan GST", f"“{name}” was marked GST-inclusive in v1.2.0, but v1.2.0 never used that "
                                     "at billing.", "Tick “Price includes GST” on the plan if the price really includes GST."))
            stamp(Plan, p.pk, created_at=to_dt(r.get("created_at")), updated_at=to_dt(r.get("updated_at")))
            self.map["plan"][r["id"]] = p
            self._n("membership_plans")

    # trainers & staff ----------------------------------------------------------------------------
    def trainers(self):
        for r in self.src.rows("trainers"):
            t = Trainer.objects.create(
                client=self.client, code=txt(r.get("trainer_code"), 20) or f"TR{r['id']:06d}", name=txt(r.get("full_name"), 120),
                phone=phone(r.get("phone")) or txt(r.get("phone"), 20), gender=txt(r.get("gender"), 10),
                specialization=txt(r.get("specialization"), 120), joining_date=to_date(r.get("joining_date")),
                status="active" if (r.get("status") or "Active").lower() == "active" else "inactive")
            stamp(Trainer, t.pk, created_at=to_dt(r.get("created_at")))
            self.map["trainer"][r["id"]] = t
            self._n("trainers")

    def staff(self):
        for r in self.src.rows("staff"):
            basis = r.get("salary_basis") or Staff.BASIS_CALENDAR
            if basis not in (Staff.BASIS_CALENDAR, Staff.BASIS_FIXED30):
                self.changes.append(("Salary basis", f"{r.get('full_name')}: “{basis}” → “Calendar Days”. v1.2.0 already "
                                     "worked this out as Calendar Days, so pay doesn't change."))
                basis = Staff.BASIS_CALENDAR
            st = Staff.objects.create(
                client=self.client, code=txt(r.get("employee_code"), 20) or f"SF{r['id']:06d}", name=txt(r.get("full_name"), 120),
                phone=phone(r.get("phone")) or txt(r.get("phone"), 20), gender=txt(r.get("gender"), 10),
                date_of_birth=to_date(r.get("date_of_birth")), address=r.get("address") or "",
                emergency_contact=txt(r.get("emergency_contact"), 30), designation=txt(r.get("designation"), 80) or "Staff",
                department=txt(r.get("department"), 40), joining_date=to_date(r.get("joining_date")) or self.today,
                monthly_salary=money(r.get("monthly_salary")), salary_basis=basis,
                status="active" if (r.get("status") or "Active").lower() == "active" else "inactive",
                trainer=self.map["trainer"].get(r.get("trainer_id")))
            stamp(Staff, st.pk, created_at=to_dt(r.get("created_at")))
            self.map["staff"][r["id"]] = st
            self._n("staff")
            if r.get("photo_path"):
                self.cleanup.append(("Staff photo", f"{st.name}: photo is a file on the old PC ({r['photo_path']}).",
                                     "Photos aren't moved; add them again later if needed."))
        for r in self.src.rows("staff_attendance"):
            st = self.map["staff"].get(r.get("staff_id"))
            d = to_date(r.get("attendance_date"))
            if not st or not d or r.get("status") not in StaffAttendance.STATUSES:
                self._skip("staff_attendance", r["id"], "unknown staff, date or status")
                continue
            StaffAttendance.objects.update_or_create(staff=st, date=d, defaults=dict(
                status=r["status"], check_in=txt(r.get("check_in"), 8), check_out=txt(r.get("check_out"), 8),
                remarks=txt(r.get("remarks"), 200)))
            self._n("staff_attendance")

    # members --------------------------------------------------------------------------------------
    def members(self):
        by_phone = {}
        for r in self.src.rows("members"):
            name = txt(r.get("full_name"), 120)
            ph = phone(r.get("phone"))
            if not ph:
                raise ImportProblem(f"Member {r.get('member_code')} ({name}) has an invalid mobile number "
                                    f"“{r.get('phone')}”. Fix it in FitDesk v1.2.0 and run the import again.")
            last10 = ph[-10:]
            if last10 in by_phone:
                raise ImportProblem(f"Members {by_phone[last10]} and {r.get('member_code')} ({name}) have the same mobile "
                                    f"number {ph}. The new system allows one member per number: merge or fix them in "
                                    "v1.2.0 first, then run the import again.")
            by_phone[last10] = f"{r.get('member_code')} ({name})"
            dob, age = to_date(r.get("date_of_birth")), r.get("age")
            if dob and age not in (None, ""):
                implied = self.today.year - dob.year - ((self.today.month, self.today.day) < (dob.month, dob.day))
                if abs(implied - int(age)) > 1:
                    self.changes.append(("Birthday removed", f"{r.get('member_code')} {name}: date of birth {dob:%d %b %Y} "
                                         f"doesn't match the saved age {age}, so it looks like a placeholder. Age kept, "
                                         "birthday left blank (so no wrong birthday wishes)."))
                    dob = None
            if dob == PLACEHOLDER_DOB:
                self.cleanup.append(("Birthday", f"{r.get('member_code')} {name}: 1 Jan 2000 may be a placeholder.",
                                     "Correct it, or birthday wishes will go on 1 January."))
            m = Member.objects.create(
                client=self.client, code=txt(r.get("member_code"), 20), name=name, phone=ph, email=txt(r.get("email"), 254).lower(),
                gender=txt(r.get("gender"), 10) or "Other", date_of_birth=dob,
                age=int(age) if str(age or "").isdigit() else None, address=r.get("address") or "",
                emergency_contact=txt(r.get("emergency_contact"), 30), biometric_id=txt(r.get("biometric_id"), 40),
                is_archived=bool(r.get("is_archived")))
            stamp(Member, m.pk, created_at=to_dt(r.get("created_at")), updated_at=to_dt(r.get("updated_at")))
            self.map["member"][r["id"]] = m
            self._n("members")
            if TEST_NAME.search(name):
                self.cleanup.append(("Test entry", f"{m.code} {name} looks like a test entry.",
                                     "Delete or archive it (Members → open → Remove member…)."))
        emails = {}
        for m in self.map["member"].values():
            if m.email:
                emails.setdefault(m.email, []).append(f"{m.code} {m.name}")
        for e, who in emails.items():
            if len(who) > 1:
                self.cleanup.append(("Shared email", f"{e} is on {', '.join(who)}.", "Fine if intended; receipts by email "
                                     "go to the same inbox."))
        for r in self.src.rows("member_profiles"):
            m = self.map["member"].get(r.get("member_id"))
            if not m:
                continue
            FitnessProfile.objects.update_or_create(member=m, defaults=dict(
                current_weight_kg=r.get("current_weight_kg"), height_cm=r.get("height_cm"),
                target_weight_kg=r.get("target_weight_kg"), fitness_goal=txt(r.get("fitness_goal"), 40),
                activity_level=txt(r.get("activity_level"), 30), workout_days_per_week=r.get("workout_days_per_week"),
                target_date=to_date(r.get("target_date")), diet_type=txt(r.get("diet_type"), 30),
                food_allergies=fit(FitnessProfile, "food_allergies", r.get("food_allergies")),
                food_preferences=fit(FitnessProfile, "food_preferences", r.get("food_preferences")),
                meals_per_day=r.get("meals_per_day"), workout_time=txt(r.get("workout_time"), 20)))
            self._n("member_profiles")

    # bills ---------------------------------------------------------------------------------------
    def bills(self):
        for r in self.src.rows("membership_transactions"):
            m, p = self.map["member"].get(r.get("member_id")), self.map["plan"].get(r.get("plan_id"))
            if not m or not p:
                raise ImportProblem(f"Receipt {r.get('receipt_no')} points to a member or plan that doesn't exist in v1.2.0.")
            fee, disc, gst = money(r.get("original_fee")), money(r.get("discount")), money(r.get("gst_amount"))
            applied = bool(r.get("is_gst_applied")) and gst > 0
            total = fee - disc + (gst if applied else R.ZERO)
            paid = money(r.get("amount_paid"))
            start, end = to_date(r.get("joining_date")), to_date(r.get("expiry_date"))
            if not start or not end:
                raise ImportProblem(f"Receipt {r.get('receipt_no')} has no start or end date.")
            trainer = self.map["trainer"].get(r.get("trainer_id"))
            share_pct = p.trainer_share_percent if p.is_personal_training else R.ZERO
            cancelled = (r.get("status") or "Active").lower() in ("cancelled", "canceled", "void")
            b = Bill.objects.create(
                client=self.client, receipt_no=txt(r.get("receipt_no"), 20), member=m, plan=p,
                kind=Bill.KIND_RENEWAL if (r.get("transaction_type") or "").lower().startswith("renew") else Bill.KIND_NEW,
                trainer=trainer, fee=fee, discount=disc, gst_applied=applied,
                gst_percent=money(r.get("gst_percent")) if applied else R.ZERO, gst_amount=gst if applied else R.ZERO,
                total=total, is_personal_training=p.is_personal_training, trainer_share_percent=share_pct,
                trainer_share_includes_gst=True, remarks=txt(r.get("remarks"), 300),
                status=Bill.STATUS_CANCELLED if cancelled else Bill.STATUS_ACTIVE,
                cancelled_reason="Cancelled in v1.2.0" if cancelled else "", client_ref=f"v120-tx-{r['id']}")
            stamp(Bill, b.pk, created_at=to_dt(r.get("created_at"), to_dt(r.get("payment_date"))))
            Membership.objects.create(client=self.client, member=m, bill=b, plan=p, trainer=trainer, start_date=start,
                                      end_date=end, original_end_date=end,
                                      status=Membership.STATUS_CANCELLED if cancelled else Membership.STATUS_ACTIVE)
            if paid > 0:
                mode = r.get("payment_mode") or "Cash"
                if mode not in Payment.MODES:
                    self.changes.append(("Payment mode", f"{b.receipt_no}: “{mode}” saved as “Other”."))
                    mode = "Other"
                pay = Payment.objects.create(bill=b, amount=paid, mode=mode, reference_no=txt(r.get("reference_no"), 60),
                                             paid_on=to_date(r.get("payment_date")) or start,
                                             trainer_share=money(r.get("trainer_share")), gym_share=money(r.get("gym_share")))
                stamp(Payment, pay.pk, created_at=to_dt(r.get("updated_at") or r.get("created_at")))
            self.map["bill"][r["id"]] = b
            self._n("membership_transactions")
            old_status = r.get("payment_status")
            new_status = R.payment_status(total, paid)
            if old_status and old_status != new_status:
                self.changes.append(("Payment status", f"{b.receipt_no}: v1.2.0 said “{old_status}”, now “{new_status}” "
                                     f"(total {total}, paid {paid}). v1.2.0 called a bill Paid once the fee before GST "
                                     "was covered."))

    # payroll -------------------------------------------------------------------------------------
    def payroll(self):
        records = {r["id"]: r for r in self.src.rows("salary_records")}
        paid_records = set()
        for r in self.src.rows("payroll_settlements"):
            st = self.map["staff"].get(r.get("staff_id"))
            if not st:
                self._skip("payroll_settlements", r["id"], "staff not found")
                continue
            rec = records.get(r.get("salary_record_id")) or {}
            ps, created = PayrollSettlement.objects.get_or_create(staff=st, month=txt(r.get("salary_month"), 7), defaults=dict(
                earned=money(rec.get("earned_salary")), advance=money(rec.get("advance_amount")),
                deduction=money(rec.get("deduction_amount")), salary_amount=money(r.get("salary_amount")),
                pt_commission=money(r.get("pt_commission_amount")), total=money(r.get("total_amount")),
                breakdown={k: rec.get(k) for k in ("basic_salary", "calendar_days", "working_days", "present_days",
                                                   "half_days", "paid_leave_days", "absent_days") if k in rec}
                | {"imported_from": "v1.2.0"},
                payment_date=to_date(r.get("payment_date")) or self.today, mode=r.get("payment_mode") or "Cash",
                reference_no=txt(r.get("reference_no"), 60), remarks=txt(r.get("remarks"), 300)))
            if not created:
                self._skip("payroll_settlements", r["id"], "second settlement for the same month")
                continue
            stamp(PayrollSettlement, ps.pk, created_at=to_dt(r.get("created_at")))
            self.map["settlement"][r["id"]] = ps
            paid_records.add(r.get("salary_record_id"))
            self._n("payroll_settlements")
        # salary paid without a combined settlement (older v1.2.0 screens)
        for rid, rec in records.items():
            if rid in paid_records or (rec.get("status") or "") != "Paid":
                continue
            st = self.map["staff"].get(rec.get("staff_id"))
            pays = [p for p in self.src.rows("salary_payments") if p.get("salary_record_id") == rid]
            if not st or PayrollSettlement.objects.filter(staff=st, month=rec.get("salary_month")).exists():
                continue
            amt = sum((money(p.get("amount")) for p in pays), R.ZERO)
            last = pays[-1] if pays else {}
            PayrollSettlement.objects.create(
                staff=st, month=txt(rec.get("salary_month"), 7), earned=money(rec.get("earned_salary")),
                advance=money(rec.get("advance_amount")), deduction=money(rec.get("deduction_amount")), salary_amount=amt,
                total=amt, breakdown={"imported_from": "v1.2.0 salary_payments"},
                payment_date=to_date(last.get("payment_date")) or self.today, mode=last.get("payment_mode") or "Cash",
                reference_no=txt(last.get("reference_no"), 60), remarks=txt(last.get("remarks"), 300))
            self._n("payroll_settlements")
        for r in self.src.rows("salary_advances"):
            st = self.map["staff"].get(r.get("staff_id"))
            if not st:
                self._skip("salary_advances", r["id"], "staff not found")
                continue
            SalaryAdvance.objects.create(staff=st, date=to_date(r.get("advance_date")) or self.today, amount=money(r.get("amount")),
                                         mode=r.get("payment_mode") or "Cash", reference_no=txt(r.get("reference_no"), 60),
                                         remarks=txt(r.get("remarks"), 300))
            self._n("salary_advances")
        for r in self.src.rows("trainer_payouts"):
            t = self.map["trainer"].get(r.get("trainer_id"))
            if not t:
                self._skip("trainer_payouts", r["id"], "trainer not found")
                continue
            settle = None
            for sid, ps in self.map["settlement"].items():
                if ps.staff.trainer_id == t.pk and ps.month == r.get("earning_month"):
                    settle = ps
            TrainerPayout.objects.create(client=self.client, trainer=t, earning_month=txt(r.get("earning_month"), 7)
                                         or (to_date(r.get("payout_date")) or self.today).strftime("%Y-%m"),
                                         amount=money(r.get("amount")), payout_date=to_date(r.get("payout_date")) or self.today,
                                         mode=r.get("payment_mode") or "Cash", reference_no=txt(r.get("reference_no"), 60),
                                         settlement=settle)
            self._n("trainer_payouts")

    # expenses / enquiries -------------------------------------------------------------------------
    def expenses(self):
        for r in self.src.rows("expenses"):
            cat = r.get("category") or "Other"
            if cat not in Expense.CATEGORIES:
                self.changes.append(("Expense category", f"“{r.get('expense_title')}”: “{cat}” saved as “Other”."))
                cat = "Other"
            mode = r.get("payment_mode") or "Cash"
            e = Expense.objects.create(
                client=self.client, date=to_date(r.get("expense_date")) or self.today, category=cat,
                title=txt(r.get("expense_title"), 200) or cat, amount=money(r.get("amount")),
                mode=mode if mode in Expense.MODES else "Other", reference_no=txt(r.get("reference_no"), 60),
                paid_to=txt(r.get("paid_to"), 120), remarks=txt(r.get("remarks"), 300),
                status=r.get("status") if r.get("status") in Expense.STATUSES else "Paid",
                is_deleted=(r.get("status") or "").lower() == "deleted")
            stamp(Expense, e.pk, created_at=to_dt(r.get("created_at")))
            self._n("expenses")

    def enquiries(self):
        top = 0
        for r in self.src.rows("enquiries"):
            st = r.get("status") if r.get("status") in Enquiry.STATUSES else "New"
            src = r.get("source") if r.get("source") in Enquiry.SOURCES else "Other"
            e = Enquiry.objects.create(
                client=self.client, code=txt(r.get("enquiry_code"), 20) or f"ENQ-{r['id']:06d}",
                enquiry_date=to_date(r.get("enquiry_date")) or self.today, name=txt(r.get("full_name"), 120),
                phone=phone(r.get("phone")) or txt(r.get("phone"), 20), email=txt(r.get("email"), 254),
                gender=txt(r.get("gender"), 10), age=r.get("age") if str(r.get("age") or "").isdigit() else None,
                source=src, interested_plan=self.map["plan"].get(r.get("interested_plan_id")),
                fitness_goal=txt(r.get("fitness_goal"), 60),
                priority=r.get("priority") if r.get("priority") in Enquiry.PRIORITIES else "Warm", status=st,
                next_follow_up_date=to_date(r.get("next_follow_up_date")), notes=r.get("notes") or "",
                lost_reason=txt(r.get("lost_reason"), 200), converted_member=self.map["member"].get(r.get("converted_member_id")),
                converted_at=to_dt(r.get("converted_at")), is_archived=bool(r.get("is_archived")))
            stamp(Enquiry, e.pk, created_at=to_dt(r.get("created_at")), updated_at=to_dt(r.get("updated_at")))
            self.map["enquiry"][r["id"]] = e
            top = max(top, code_number(e.code, "ENQ-"))
            self._n("enquiries")
            if r.get("assigned_to"):
                self.changes.append(("Enquiry owner", f"{e.code}: was assigned to a v1.2.0 login; assign it again once "
                                     "staff have their new logins."))
        self.enquiry_top = top
        for r in self.src.rows("enquiry_followups"):
            e = self.map["enquiry"].get(r.get("enquiry_id"))
            if not e:
                continue
            f = EnquiryFollowUp.objects.create(
                enquiry=e, date=to_date(r.get("followup_date")) or self.today, mode=txt(r.get("mode"), 20) or "Call",
                notes=fit(EnquiryFollowUp, "notes", r.get("notes")), status_before=txt(r.get("status_before"), 12) or "New",
                status_after=txt(r.get("status_after"), 12) or "New", next_follow_up_date=to_date(r.get("next_follow_up_date")))
            stamp(EnquiryFollowUp, f.pk, created_at=to_dt(r.get("created_at")))
            self._n("enquiry_followups")

    # diet ------------------------------------------------------------------------------------------
    def foods(self):
        for r in self.src.rows("diet_foods"):
            f = Food.objects.create(client=self.client, name=txt(r.get("name"), 120), serving_qty=r.get("serving_qty") or 100,
                                    serving_unit=txt(r.get("serving_unit"), 20) or "g", kcal=r.get("kcal"),
                                    protein_g=r.get("protein_g"), carbs_g=r.get("carbs_g"), fat_g=r.get("fat_g"),
                                    allergens=r.get("allergens") or "", source=txt(r.get("source"), 40),
                                    source_note=txt(r.get("source_note"), 200), is_active=bool(r.get("is_active", 1)))
            self.map["food"][r["id"]] = f
            self._n("diet_foods")

    def _meals(self, key, ref):
        meals = [m for m in self.src.rows("diet_meals", order="sort_order") if m.get(key) == ref]
        items = self.src.rows("diet_meal_items", order="sort_order")
        out = []
        for m in meals:
            its = []
            for i in (x for x in items if x.get("meal_id") == m["id"]):
                f = self.map["food"].get(i.get("food_id"))
                it = {"food_name": i.get("food_name") or "", "portion": i.get("portion") or "", "quantity": i.get("quantity"),
                      "unit": i.get("unit") or "", "allergens": i.get("allergens") or "", "notes": i.get("notes") or ""}
                if f:
                    it["food_id"] = f.id
                for n in ("kcal", "protein_g", "carbs_g", "fat_g"):
                    it[n] = i.get(n)
                its.append(it)
            out.append({"name": m.get("name") or "Meal", "time": m.get("meal_time") or "", "notes": m.get("notes") or "",
                        "items": its})
        return out

    def diet(self):
        from gym_ops.services import clean_meals, snapshot
        for r in self.src.rows("diet_templates"):
            meals = snapshot(self.client, clean_meals(self._meals("template_id", r["id"])))
            t = DietTemplate.objects.create(client=self.client, name=txt(r.get("name"), 120), goal=txt(r.get("goal"), 30),
                                            diet_type=txt(r.get("diet_type"), 30), weeks=r.get("weeks"),
                                            instructions=r.get("instructions") or "", meals=meals,
                                            source_starter_key=txt(r.get("source_starter_key"), 60),
                                            is_archived=bool(r.get("is_archived")))
            self.map["template"][r["id"]] = t
            self._n("diet_templates")
        versions = self.src.rows("diet_plan_versions")
        for r in self.src.rows("diet_plans"):
            m = self.map["member"].get(r.get("member_id"))
            if not m:
                self._skip("diet_plans", r["id"], "member not found")
                continue
            status = r.get("status") if r.get("status") in DietPlan.STATUSES else "Draft"
            p = DietPlan.objects.create(
                client=self.client, member=m, name=txt(r.get("name"), 120), goal=txt(r.get("goal"), 30),
                diet_type=txt(r.get("diet_type"), 30), trainer=self.map["trainer"].get(r.get("trainer_id")),
                start_date=to_date(r.get("start_date")), end_date=to_date(r.get("end_date")),
                review_date=to_date(r.get("review_date")), status=status, current_version=r.get("current_version") or 1,
                source_type=txt(r.get("source_type"), 20) or "scratch",
                source_ref=str(self.map["template"][int(r["source_ref"])].id) if r.get("source_type") == "template"
                and str(r.get("source_ref") or "").isdigit() and int(r["source_ref"]) in self.map["template"]
                else txt(r.get("source_ref"), 60))
            stamp(DietPlan, p.pk, created_at=to_dt(r.get("created_at")), updated_at=to_dt(r.get("updated_at")))
            self.map["diet_plan"][r["id"]] = p
            self._n("diet_plans")
            for v in (x for x in versions if x.get("plan_id") == r["id"]):
                meals = snapshot(self.client, clean_meals(self._meals("plan_version_id", v["id"])))
                dv = DietPlanVersion.objects.create(
                    plan=p, version_no=v.get("version_no") or 1, name=txt(v.get("name"), 120) or p.name,
                    goal=txt(v.get("goal"), 30), diet_type=txt(v.get("diet_type"), 30), start_date=to_date(v.get("start_date")),
                    end_date=to_date(v.get("end_date")), review_date=to_date(v.get("review_date")),
                    instructions=v.get("instructions") or "", change_note=txt(v.get("change_note"), 200), meals=meals)
                stamp(DietPlanVersion, dv.pk, created_at=to_dt(v.get("created_at")))
                self._n("diet_plan_versions")
                self._n("diet_meal_items", sum(len(x["items"]) for x in meals))
            if not p.versions.filter(version_no=p.current_version).exists():
                last = p.versions.order_by("-version_no").first()
                if last is None:
                    raise ImportProblem(f"Diet plan “{p.name}” for {m.name} has no saved version in v1.2.0.")
                p.current_version = last.version_no
                p.save(update_fields=["current_version"])
        for r in self.src.rows("diet_plan_shares"):
            p = self.map["diet_plan"].get(r.get("plan_id"))
            if p:
                s = DietShare.objects.create(plan=p, version_no=r.get("version_no") or 1,
                                             channel=txt(r.get("channel"), 12) or "whatsapp_link",
                                             recipient=txt(r.get("recipient"), 40))
                stamp(DietShare, s.pk, created_at=to_dt(r.get("created_at")))
                self._n("diet_plan_shares")

    # numbering -------------------------------------------------------------------------------------
    def counters(self):
        gs = GymSettings.objects.get(client=self.client)
        tops = {
            "member": max([code_number(m.code, self.prefix) for m in self.map["member"].values()] + [0]),
            "receipt": max([code_number(b.receipt_no, gs.receipt_prefix) for b in self.map["bill"].values()] + [0]),
            "enquiry": getattr(self, "enquiry_top", 0),
        }
        for k, v in tops.items():
            Counter.objects.update_or_create(client=self.client, key=k, defaults={"value": v})
        self.next_codes = {"member": R.member_code(self.prefix, tops["member"] + 1),
                           "receipt": f"{gs.receipt_prefix}{tops['receipt'] + 1:05d}",
                           "staff": R.staff_code(self.prefix, 1), "trainer": R.trainer_code(self.prefix, 1)}

    def record(self, source_path, sha, ok=True, summary=None):
        return ImportRun.objects.create(client=self.client, source_path=str(source_path)[:500], source_sha256=sha,
                                        source_gym_name=self.s["gym_name"], ok=ok, summary=summary or {})


def wipe_gym(client):
    """Deletes a gym the importer created, with all its records. Bills,
    payments and memberships are protected against accidental deletion in
    normal use, so they are removed first, explicitly."""
    from django.db.models.deletion import ProtectedError
    from gym_core.models import CheckIn
    Payment.objects.filter(bill__client=client).delete()
    CheckIn.objects.filter(client=client).delete()
    Membership.objects.filter(client=client).delete()
    Bill.objects.filter(client=client).delete()
    for _ in range(5):
        try:
            client.delete()
            return
        except ProtectedError as e:                     # anything else protected: remove it, then retry
            for obj in e.protected_objects:
                obj.delete()
    client.delete()


def find_previous_import(source_gym_name=None, client_id=None):
    """The gym a previous import created (only those can be replaced)."""
    qs = ImportRun.objects.filter(created_gym=True, client__isnull=False, ok=True)
    if client_id:
        qs = qs.filter(client_id=client_id)
    elif source_gym_name:
        qs = qs.filter(source_gym_name=source_gym_name)
    return qs.select_related("client").first()
