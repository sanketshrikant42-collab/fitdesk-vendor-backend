"""Phase 3B operations. Every function works only inside the signed-in
user's gym."""

from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from gym_accounts.api import ApiError
from gym_accounts.models import StaffUser
from gym_accounts.permissions import ROLE_OWNER, SCOPE_OWN, has_perm, scope_for
from gym_core import rules as R
from gym_core import services as C
from gym_core.models import Bill, GymSettings, Member, Membership, Payment, Plan, Trainer
from licensing.models import SupportQuery

from . import diet_nutrition as N
from . import diet_starter_plans as SP
from .models import (ConsentForm, DietPlan, DietPlanVersion, DietShare, DietTemplate, Enquiry, EnquiryFollowUp, Expense,
                     Food, PayrollSettlement, ProgressEntry, ReferralReward, SalaryAdvance, Staff, StaffAttendance,
                     TrainerPayout)
from .rules import payroll as payroll_rule

dec, parse_date, text, clean_phone, log, today_for, get_in_gym = (C.dec, C.parse_date, C.text, C.clean_phone, C.log,
                                                                   C.today_for, C.get_in_gym)


def choice(value, allowed, field, default=None):
    v = str(value or "").strip() or default
    if v not in allowed:
        raise ApiError(400, f"bad_{field}", f"{field.replace('_', ' ').title()} must be one of: {', '.join(allowed)}.")
    return v


def month_param(value, client):
    m = str(value or "").strip() or today_for(client).strftime("%Y-%m")
    try:
        y, mo = (int(x) for x in m.split("-"))
        assert 2000 <= y <= 2100 and 1 <= mo <= 12
    except (ValueError, AssertionError):
        raise ApiError(400, "bad_month", "Month must look like 2026-10.")
    return f"{y:04d}-{mo:02d}"


# =============================================================================
# STAFF
# =============================================================================

def can_see_salaries(user):
    """Owner always; anyone else only if the owner gave them salary access
    (view or manage) when inviting them or later in Team & access."""
    return user.role == ROLE_OWNER or has_perm(user, "salaries", "view")


def can_manage_salaries(user):
    """Set salaries, pay salary, give advances: owner, or "manage" access."""
    return user.role == ROLE_OWNER or has_perm(user, "salaries", "edit")


def staff_json(s, salaries=False):
    out = {"id": s.id, "code": s.code, "name": s.name, "phone": s.phone, "gender": s.gender,
           "designation": s.designation, "department": s.department, "joining_date": s.joining_date.isoformat(),
           "status": s.status, "address": s.address, "emergency_contact": s.emergency_contact,
           "date_of_birth": s.date_of_birth.isoformat() if s.date_of_birth else None,
           "trainer": {"id": s.trainer_id, "name": s.trainer.name} if s.trainer_id else None,
           "login": {"id": s.staff_user_id, "name": s.staff_user.name, "role": s.staff_user.role} if s.staff_user_id else None}
    if salaries:
        out.update({"monthly_salary": str(s.monthly_salary), "salary_basis": s.salary_basis})
    return out


def save_staff(request, data, staff=None):
    s = staff or Staff(client=request.client)
    if staff is None or "name" in data:
        s.name = text(data, "name", 120, required=True)
    if staff is None or "phone" in data:
        s.phone = clean_phone(data.get("phone"))
    if staff is None or "designation" in data:
        s.designation = text(data, "designation", 80, required=True)
    for f, n in (("gender", 10), ("department", 80), ("address", 500), ("emergency_contact", 30), ("status", 10)):
        if f in data:
            setattr(s, f, text(data, f, n))
    if staff is None or "joining_date" in data:
        s.joining_date = parse_date(data.get("joining_date"), "joining_date", today_for(request.client))
    if "date_of_birth" in data:
        s.date_of_birth = parse_date(data["date_of_birth"], "date_of_birth") if data["date_of_birth"] else None
    if "monthly_salary" in data or "salary_basis" in data:
        if not can_manage_salaries(request.staff):
            raise ApiError(403, "permission_denied", "Only the owner (or someone the owner allows) can set salaries.",
                           module="salaries", action="edit")
        if "monthly_salary" in data:
            s.monthly_salary = dec(data["monthly_salary"], "monthly_salary")
        if "salary_basis" in data:
            s.salary_basis = choice(data["salary_basis"], [Staff.BASIS_CALENDAR, Staff.BASIS_FIXED30], "salary_basis")
    if "trainer_id" in data:
        s.trainer = get_in_gym(Trainer, request.client, data["trainer_id"], "Trainer") if data["trainer_id"] else None
    if "staff_user_id" in data:
        s.staff_user = (StaffUser.objects.filter(client=request.client, pk=data["staff_user_id"]).first()
                        if data["staff_user_id"] else None)
        if data["staff_user_id"] and s.staff_user is None:
            raise ApiError(400, "bad_staff_user", "Pick a login from this gym.")
    if Staff.objects.filter(client=request.client, phone=s.phone).exclude(pk=s.pk).exists():
        raise ApiError(409, "phone_taken", "A staff member with this mobile number already exists.")
    try:
        with transaction.atomic():
            # Trainer record and staff record are saved together: if the staff
            # save fails, no stray trainer is left behind (bug seen in testing).
            is_trainer_dept = (s.department or "").strip().lower() == "trainer"
            if is_trainer_dept and s.trainer_id is None:
                # v1.2.0 decision: staff in the Trainer department are auto-enrolled as trainers.
                existing = Trainer.objects.filter(client=request.client, phone=s.phone).first()
                s.trainer = existing or C.save_trainer(request, {"name": s.name, "phone": s.phone, "gender": s.gender})
                if existing and existing.status != "active":
                    existing.status = "active"
                    existing.save(update_fields=["status"])
            elif is_trainer_dept and s.trainer.status != "active":
                s.trainer.status = "active"
                s.trainer.save(update_fields=["status"])
            elif not is_trainer_dept and staff is not None and s.trainer_id and "department" in data:
                # Moved out of the Trainer department: stop listing them as a trainer.
                s.trainer.status = "inactive"
                s.trainer.save(update_fields=["status"])
            if s.status != "active" and s.trainer_id and s.trainer.status == "active":
                s.trainer.status = "inactive"
                s.trainer.save(update_fields=["status"])
            if staff is None:
                st = GymSettings.for_client(request.client)
                prefix = st.member_code_prefix or R.code_prefix(st.gym_name)
                s.code = R.staff_code(prefix, C.next_number(request.client, "staff"))   # e.g. PPF-S001
            s.save()
    except IntegrityError:
        raise ApiError(409, "phone_taken", "A staff member with this mobile number already exists.")
    log(request, "staff_saved" if staff else "staff_added", s.name)
    return s


def mark_attendance(request, rows):
    """rows: [{staff_id, date, status, check_in?, check_out?, remarks?}] — the
    daily grid saves many at once (v1.2.0 "Save All")."""
    if not isinstance(rows, list) or not rows:
        raise ApiError(400, "bad_rows", "Send a list of attendance rows.")
    saved = 0
    with transaction.atomic():
        for r in rows:
            s = get_in_gym(Staff, request.client, (r or {}).get("staff_id"), "Staff")
            d = parse_date(r.get("date"), "date", today_for(request.client))
            if d > today_for(request.client):
                raise ApiError(400, "future_date", "Attendance can't be marked for a future date.")
            status = choice(r.get("status"), StaffAttendance.STATUSES, "status")
            StaffAttendance.objects.update_or_create(staff=s, date=d, defaults={
                "status": status, "check_in": str(r.get("check_in") or "")[:5], "check_out": str(r.get("check_out") or "")[:5],
                "remarks": str(r.get("remarks") or "")[:200], "marked_by": request.staff})
            saved += 1
    log(request, "staff_attendance_saved", f"{saved} row(s)")
    return saved


def attendance_month(staff, month):
    """One staff member's month: a row per day (blank = not marked) and
    counts per status, for the staff profile."""
    import calendar
    y, m = (int(x) for x in month.split("-"))
    marks = {a.date: a for a in staff.attendance.filter(date__year=y, date__month=m)}
    counts = {k: 0 for k in StaffAttendance.STATUSES}
    days = []
    for d in range(1, calendar.monthrange(y, m)[1] + 1):
        day = date(y, m, d)
        a = marks.get(day)
        if a:
            counts[a.status] += 1
        days.append({"date": day.isoformat(), "weekday": day.strftime("%a"), "status": a.status if a else "",
                     "check_in": a.check_in if a else "", "check_out": a.check_out if a else "",
                     "remarks": a.remarks if a else ""})
    return {"month": month, "counts": counts, "marked": len(marks), "days": days}


def pt_earned_for(staff, month):
    """PT commission earned in the month = trainer share of PT payments
    received that month (v1.2.0 summed trainer_share by payment month)."""
    if not staff.trainer_id:
        return Decimal("0")
    y, m = (int(x) for x in month.split("-"))
    return Payment.objects.filter(bill__trainer_id=staff.trainer_id, bill__is_personal_training=True,
                                  bill__status=Bill.STATUS_ACTIVE, paid_on__year=y, paid_on__month=m
                                  ).aggregate(s=Sum("trainer_share"))["s"] or Decimal("0")


def calculate_payroll(request, staff, month, deduction=0):
    y, m = (int(x) for x in month.split("-"))
    statuses = dict(staff.attendance.filter(date__year=y, date__month=m).values_list("date", "status"))
    advance = staff.advances.filter(date__year=y, date__month=m).aggregate(s=Sum("amount"))["s"] or 0
    pt_paid = (TrainerPayout.objects.filter(trainer_id=staff.trainer_id, earning_month=month).aggregate(s=Sum("amount"))["s"] or 0
               ) if staff.trainer_id else 0
    st = GymSettings.for_client(request.client)
    out = payroll_rule(staff.monthly_salary, staff.salary_basis, month, today_for(request.client), staff.joining_date,
                       statuses, advance, deduction, pt_earned_for(staff, month), pt_paid, st.holidays_paid)
    existing = staff.settlements.filter(month=month).first()
    out["staff"] = {"id": staff.id, "name": staff.name, "code": staff.code, "monthly_salary": str(staff.monthly_salary),
                    "salary_basis": staff.salary_basis}
    out["settled"] = ({"payment_date": existing.payment_date.isoformat(), "total": str(existing.total), "mode": existing.mode,
                       "reference_no": existing.reference_no} if existing else None)
    return out


def pay_payroll(request, staff, month, data):
    deduction = dec(data.get("deduction"), "deduction", R.ZERO)
    mode = choice(data.get("mode"), ["Cash", "UPI", "Bank Transfer", "Cheque"], "mode", "Cash")
    ref = text(data, "reference_no", 60)
    if mode != "Cash" and not ref:
        raise ApiError(400, "reference_required", "Enter the payment reference for non-cash payments.")   # v1.2.0 rule
    with transaction.atomic():
        Staff.objects.select_for_update().get(pk=staff.pk)
        if staff.settlements.filter(month=month).exists():
            raise ApiError(409, "already_settled", "This month's payroll has already been settled.")
        d = calculate_payroll(request, staff, month, deduction)
        if d["total"] <= 0:
            raise ApiError(400, "nothing_to_pay", "There is nothing pending to pay.")
        paid_on = parse_date(data.get("payment_date"), "payment_date", today_for(request.client))
        st = PayrollSettlement.objects.create(
            staff=staff, month=month, earned=R.money(d["earned"]), advance=R.money(d["advance"]), deduction=R.money(d["deduction"]),
            salary_amount=R.money(d["net"]), pt_commission=R.money(d["pt_pending"]), total=R.money(d["total"]),
            breakdown={k: d[k] for k in ("counts", "weekly_off_inferred", "paid_days", "divisor", "daily_rate", "holidays_paid")},
            payment_date=paid_on, mode=mode, reference_no=ref, remarks=text(data, "remarks", 300), paid_by=request.staff)
        if d["pt_pending"] > 0 and staff.trainer_id:
            TrainerPayout.objects.create(client=request.client, trainer_id=staff.trainer_id, earning_month=month,
                                         amount=R.money(d["pt_pending"]), payout_date=paid_on, mode=mode,
                                         reference_no=ref, settlement=st)
    log(request, "payroll_paid", staff.name, month=month, total=str(st.total))
    return st


def add_advance(request, staff, data):
    a = SalaryAdvance.objects.create(staff=staff, date=parse_date(data.get("date"), "date", today_for(request.client)),
                                     amount=dec(data.get("amount"), "amount"),
                                     mode=choice(data.get("mode"), Expense.MODES, "mode", "Cash"),
                                     reference_no=text(data, "reference_no", 60), remarks=text(data, "remarks", 200),
                                     given_by=request.staff)
    if a.amount <= 0:
        a.delete()
        raise ApiError(400, "bad_amount", "Enter the advance amount.")
    log(request, "salary_advance", staff.name, amount=str(a.amount))
    return a


# =============================================================================
# EXPENSES
# =============================================================================

def expense_json(e):
    return {"id": e.id, "date": e.date.isoformat(), "category": e.category, "title": e.title, "amount": str(e.amount),
            "mode": e.mode, "reference_no": e.reference_no, "paid_to": e.paid_to, "remarks": e.remarks, "status": e.status,
            "created_by": e.created_by.name if e.created_by_id else None}


def save_expense(request, data, e=None):
    e = e or Expense(client=request.client, created_by=request.staff)
    if e.pk is None or "date" in data:
        e.date = parse_date(data.get("date"), "date", today_for(request.client))
    if e.pk is None or "category" in data:
        e.category = choice(data.get("category"), Expense.CATEGORIES, "category")
    if e.pk is None or "title" in data:
        e.title = text(data, "title", 150, required=True)
    if e.pk is None or "amount" in data:
        e.amount = dec(data.get("amount"), "amount")
        if e.amount <= 0:
            raise ApiError(400, "bad_amount", "Amount must be more than zero.")
    if e.pk is None or "mode" in data:
        e.mode = choice(data.get("mode"), Expense.MODES, "mode", "Cash")
    if "status" in data or e.pk is None:
        e.status = choice(data.get("status"), Expense.STATUSES, "status", "Paid")
    for f, n in (("reference_no", 60), ("paid_to", 120), ("remarks", 300)):
        if f in data:
            setattr(e, f, text(data, f, n))
    e.save()
    log(request, "expense_saved", e.title, amount=str(e.amount))
    return e


# =============================================================================
# ENQUIRIES & REFERRALS
# =============================================================================

DEFAULT_ENQUIRY_WHATSAPP = ("Hi {first_name},\n\n"
                            "This is {gym_name}. Just following up on your enquiry about our {plan_name} membership. "
                            "Do you have any questions, or would you like to visit us?\n\n"
                            "Reply here and we'll help you get started.")     # v1.2.0 default


def enquiry_json(e, detail=False):
    st = GymSettings.for_client(e.client)
    out = {"id": e.id, "code": e.code, "enquiry_date": e.enquiry_date.isoformat(), "name": e.name, "phone": e.phone,
           "email": e.email, "gender": e.gender, "age": e.age, "source": e.source,
           "interested_plan": {"id": e.interested_plan_id, "name": e.interested_plan.name} if e.interested_plan_id else None,
           "fitness_goal": e.fitness_goal, "priority": e.priority, "status": e.status,
           "assigned_to": {"id": e.assigned_to_id, "name": e.assigned_to.name} if e.assigned_to_id else None,
           "next_follow_up_date": e.next_follow_up_date.isoformat() if e.next_follow_up_date else None,
           "notes": e.notes, "lost_reason": e.lost_reason, "is_archived": e.is_archived,
           "referred_by": {"id": str(e.referred_by_id), "name": e.referred_by.name, "code": e.referred_by.code} if e.referred_by_id else None,
           "converted_member": {"id": str(e.converted_member_id), "code": e.converted_member.code} if e.converted_member_id else None}
    msg = R.render_template(st.enquiry_whatsapp_template or DEFAULT_ENQUIRY_WHATSAPP, {
        "first_name": e.name.split()[0] if e.name else "", "full_name": e.name, "gym_name": st.gym_name,
        "plan_name": e.interested_plan.name if e.interested_plan_id else "gym"})
    out["whatsapp"] = {"text": msg, "link": R.whatsapp_link(e.phone, msg)}
    if detail:
        out["followups"] = [{"id": f.id, "date": f.date.isoformat(), "mode": f.mode, "notes": f.notes,
                             "status_before": f.status_before, "status_after": f.status_after,
                             "next_follow_up_date": f.next_follow_up_date.isoformat() if f.next_follow_up_date else None,
                             "by": f.by_user.name if f.by_user_id else None} for f in e.followups.select_related("by_user")]
        r = getattr(e, "reward", None)
        out["referral_reward"] = {"days": r.days, "status": r.status} if r else None
    return out


def save_enquiry(request, data, e=None):
    new = e is None
    e = e or Enquiry(client=request.client, created_by=request.staff)
    if new or "name" in data:
        e.name = text(data, "name", 120, required=True)
    if new or "phone" in data:
        e.phone = clean_phone(data.get("phone"))
    if new or "source" in data:
        e.source = choice(data.get("source"), Enquiry.SOURCES, "source", "Walk-in")
    if new or "priority" in data:
        e.priority = choice(data.get("priority"), Enquiry.PRIORITIES, "priority", "Warm")
    if new:
        e.enquiry_date = parse_date(data.get("enquiry_date"), "enquiry_date", today_for(request.client))
    for f, n in (("email", 254), ("gender", 10), ("fitness_goal", 80), ("notes", 2000)):
        if f in data:
            setattr(e, f, text(data, f, n))
    if "age" in data:
        e.age = int(data["age"]) if str(data["age"] or "").isdigit() else None
    if "interested_plan_id" in data:
        e.interested_plan = get_in_gym(Plan, request.client, data["interested_plan_id"], "Plan") if data["interested_plan_id"] else None
    if "assigned_to_id" in data:
        e.assigned_to = (StaffUser.objects.filter(client=request.client, pk=data["assigned_to_id"]).first()
                         if data["assigned_to_id"] else None)
    if "next_follow_up_date" in data:
        e.next_follow_up_date = parse_date(data["next_follow_up_date"], "next_follow_up_date") if data["next_follow_up_date"] else None
    if "referred_by_member_id" in data:
        e.referred_by = get_in_gym(Member, request.client, data["referred_by_member_id"], "Member") if data["referred_by_member_id"] else None
        if e.referred_by and e.source == "Walk-in" and new:
            e.source = "Referral"
    with transaction.atomic():
        if new:
            e.code = f"ENQ-{C.next_number(request.client, 'enquiry'):06d}"        # v1.2.0 format
        e.save()
    log(request, "enquiry_added" if new else "enquiry_updated", f"{e.name} ({e.code})")
    matches = list(Member.objects.filter(client=request.client, phone=e.phone).values("id", "code", "name")) if new else []
    return e, [{"id": str(m["id"]), "code": m["code"], "name": m["name"]} for m in matches]


def add_followup(request, e, data):
    """v1.2.0 add_followup(): moves a New enquiry to Contacted by default."""
    if e.status in ("Converted", "Lost"):
        raise ApiError(400, "enquiry_closed", f"This enquiry is already {e.status}.")
    mode = choice(data.get("mode"), EnquiryFollowUp.MODES, "mode")
    new_status = data.get("new_status")
    if new_status in ("Converted", "Lost"):
        raise ApiError(400, "use_convert_or_lost", "Use Convert or Mark as lost for that.")
    after = choice(new_status, Enquiry.OPEN, "new_status") if new_status else ("Contacted" if e.status == "New" else e.status)
    when = parse_date(data.get("date"), "date", today_for(request.client))
    nxt = parse_date(data["next_follow_up_date"], "next_follow_up_date") if data.get("next_follow_up_date") else None
    if nxt and nxt < when:
        raise ApiError(400, "bad_next_follow_up_date", "Next follow-up can't be before this follow-up.")
    with transaction.atomic():
        EnquiryFollowUp.objects.create(enquiry=e, date=when, mode=mode, notes=text(data, "notes", 500),
                                       status_before=e.status, status_after=after, next_follow_up_date=nxt, by_user=request.staff)
        e.status, e.next_follow_up_date = after, nxt
        e.save(update_fields=["status", "next_follow_up_date", "updated_at"])
    log(request, "enquiry_followup", e.name, mode=mode)
    return e


def mark_lost(request, e, data):
    if e.status in ("Converted", "Lost"):
        raise ApiError(400, "enquiry_closed", f"This enquiry is already {e.status}.")
    reason = text(data, "reason", 200, required=True)
    EnquiryFollowUp.objects.create(enquiry=e, date=today_for(request.client), mode="Other", notes=reason,
                                   status_before=e.status, status_after="Lost", by_user=request.staff)
    e.status, e.lost_reason, e.next_follow_up_date = "Lost", reason, None
    e.save(update_fields=["status", "lost_reason", "next_follow_up_date", "updated_at"])
    log(request, "enquiry_lost", e.name, reason=reason)
    return e


def reopen(request, e, data):
    if e.status != "Lost":
        raise ApiError(400, "not_lost", "Only a lost enquiry can be reopened.")
    status = choice(data.get("status"), Enquiry.OPEN, "status", "Follow-up")
    EnquiryFollowUp.objects.create(enquiry=e, date=today_for(request.client), mode="Other", notes="Reopened",
                                   status_before="Lost", status_after=status, by_user=request.staff)
    e.status, e.lost_reason = status, ""
    e.save(update_fields=["status", "lost_reason", "updated_at"])
    return e


def convert(request, e, data):
    """Link to an existing member, or create the member from the enquiry
    (v1.2.0 opened Add Member pre-filled). Awards the referral reward."""
    if e.status == "Converted":
        raise ApiError(400, "already_converted", "This enquiry is already converted.")
    with transaction.atomic():
        if data.get("member_id"):
            m = get_in_gym(Member, request.client, data["member_id"], "Member")
        else:
            if not has_perm(request.staff, "members", "add"):
                raise ApiError(403, "permission_denied", "Your role can't add members.", module="members", action="add")
            m = C.save_member(request, {"name": e.name, "phone": e.phone, "email": e.email,
                                        "gender": data.get("gender") or e.gender or "Other", "age": e.age})
        EnquiryFollowUp.objects.create(enquiry=e, date=today_for(request.client), mode="Visit",
                                       notes=f"Converted to member {m.code}", status_before=e.status,
                                       status_after="Converted", by_user=request.staff)
        e.status, e.converted_member, e.converted_at = "Converted", m, timezone.now()
        e.next_follow_up_date, e.lost_reason = None, ""
        e.save()
        reward = None
        days = GymSettings.for_client(request.client).referral_reward_days
        if e.referred_by_id and days > 0 and e.referred_by_id != m.id:
            reward = ReferralReward.objects.create(client=request.client, referrer=e.referred_by, enquiry=e, days=days)
            apply_reward(request, reward)
    log(request, "enquiry_converted", e.name, member=m.code)
    return e, m, reward


def apply_reward(request, reward):
    """Adds the free days to the referrer's running (or next) membership.
    Stays 'pending' if they have none; applied automatically later when
    asked via the API."""
    if reward.status == ReferralReward.STATUS_APPLIED:
        return reward
    on = today_for(request.client)
    ms = Membership.objects.filter(member=reward.referrer, status=Membership.STATUS_ACTIVE, end_date__gte=on).order_by("end_date").last()
    if ms is None:
        return reward
    ms.end_date = ms.end_date + timedelta(days=reward.days)
    ms.save(update_fields=["end_date"])
    reward.status, reward.applied_to, reward.applied_at = ReferralReward.STATUS_APPLIED, ms, timezone.now()
    reward.save(update_fields=["status", "applied_to", "applied_at"])
    log(request, "referral_reward_applied", reward.referrer.name, days=reward.days)
    return reward


def enquiry_stats(request, qs):
    """v1.2.0 dashboard_stats()."""
    on = today_for(request.client)
    mstart = on.replace(day=1)
    base = qs.filter(is_archived=False)
    month = base.filter(enquiry_date__gte=mstart, enquiry_date__lte=on)
    month_conv = month.filter(status="Converted").count()
    return {"today": on.isoformat(), "open": base.filter(status__in=Enquiry.OPEN).count(),
            "new_today": base.filter(enquiry_date=on).count(),
            "due_today": base.filter(status__in=Enquiry.OPEN, next_follow_up_date=on).count(),
            "overdue": base.filter(status__in=Enquiry.OPEN, next_follow_up_date__lt=on).count(),
            "this_month": month.count(),
            "converted_this_month": base.filter(status="Converted", converted_at__date__gte=mstart, converted_at__date__lte=on).count(),
            "conversion_rate": round(100.0 * month_conv / month.count(), 1) if month.count() else 0.0,
            "by_status": {s: base.filter(status=s).count() for s in Enquiry.STATUSES},
            "by_source": {s: n for s in Enquiry.SOURCES if (n := month.filter(source=s).count())}}


# =============================================================================
# DIET & NUTRITION
# =============================================================================

def food_index(client):
    """(by_id, by_name). The built-in food list first, then the gym's own
    foods on top: a gym food with the same name replaces the built-in one."""
    from .food_db import builtin_foods, norm
    by_id, by_name = {}, {}
    for f in builtin_foods():
        by_id[f["id"]] = f
        for n in [f["name"]] + f["aliases"]:
            by_name.setdefault(norm(n), f)
    for f in (x.as_dict() for x in Food.objects.filter(client=client)):
        by_id[f["id"]] = f
        if f["is_active"]:
            replaced = by_name.get(norm(f["name"]))
            if replaced is not None and replaced.get("builtin"):
                for k, v in list(by_name.items()):      # its other names ("kanda poha") now mean the gym's food too
                    if v is replaced:
                        by_name[k] = f
            by_name[norm(f["name"])] = f
    return by_id, by_name


def clean_meals(meals):
    """v1.2.0 _clean_meals()."""
    if not isinstance(meals, list):
        raise ApiError(400, "bad_meals", "meals must be a list.")
    out = []
    for i, meal in enumerate(meals):
        meal = meal or {}
        name = str(meal.get("name") or "").strip()
        if not name:
            raise ApiError(400, "bad_meals", f"Meal {i + 1} needs a name.")
        items = []
        for j, item in enumerate(meal.get("items") or []):
            food = str(item.get("food_name") or item.get("food") or "").strip()
            if not food:
                raise ApiError(400, "bad_meals", f"{name}: food {j + 1} needs a name.")
            qty = item.get("quantity")
            if qty not in (None, ""):
                try:
                    qty = float(qty)
                    assert qty > 0
                except (TypeError, ValueError, AssertionError):
                    raise ApiError(400, "bad_meals", f"{name}: quantity for {food} must be more than zero.")
            else:
                qty = None
            al = item.get("allergens") or ""
            al = ",".join(al) if isinstance(al, (list, tuple)) else str(al)
            row = {"food_id": item.get("food_id"), "food_name": food, "portion": item.get("portion") or None,
                   "quantity": qty, "unit": item.get("unit") or None, "allergens": al,
                   "notes": item.get("notes") or None}
            if item.get("manual"):          # values typed by hand for this item's whole amount
                row["manual"] = True
                for n in N.NUTRIENTS:
                    v = item.get(n)
                    if v in (None, ""):
                        row[n] = None
                        continue
                    try:
                        row[n] = round(float(v), 1)
                        assert 0 <= row[n] <= 10000
                    except (TypeError, ValueError, AssertionError):
                        raise ApiError(400, "bad_meals", f"{name}: {food}: {n.replace('_g', '')} must be a number from 0 to 10000.")
            items.append(row)
        out.append({"name": name, "time": meal.get("time") or None, "notes": meal.get("notes") or None, "items": items})
    return out


def snapshot(client, meals):
    """v1.2.0 _snapshot(): store each item's calculated values so a saved
    plan doesn't change when the food library is edited later."""
    by_id, by_name = food_index(client)
    for meal in meals:
        for item in meal["items"]:
            food = N._food_for(item, by_id, by_name)
            calc = N.calc_item(item, food)
            for n in N.NUTRIENTS:
                item[n] = calc[n]
            if food is not None:
                item["food_id"] = item.get("food_id") or food["id"]
                if not item.get("allergens") and food.get("allergens"):
                    item["allergens"] = food["allergens"]
    return meals


def plan_fields(data):
    f = {"name": str(data.get("name") or "").strip()[:120], "goal": str(data.get("goal") or "")[:30],
         "diet_type": str(data.get("diet_type") or "")[:30]}
    if not f["name"]:
        raise ApiError(400, "bad_name", "Enter a plan name.")
    for k in ("start_date", "end_date", "review_date"):
        f[k] = parse_date(data[k], k) if data.get(k) else None
    if f["start_date"] and f["end_date"] and f["end_date"] < f["start_date"]:
        raise ApiError(400, "bad_dates", "End date must be on or after the start date.")
    if f["review_date"] and f["start_date"] and f["review_date"] < f["start_date"]:
        raise ApiError(400, "bad_dates", "Review date must be on or after the start date.")
    if f["review_date"] and f["end_date"] and f["review_date"] > f["end_date"]:
        raise ApiError(400, "bad_dates", "Review date must be on or before the end date.")
    return f


def can_manage_library(user):
    return has_perm(user, "diet", "edit") and scope_for(user, "diet") != SCOPE_OWN


def diet_plan_json(p, version_no=None, with_nutrition=True):
    v = p.versions.filter(version_no=version_no or p.current_version).first()
    out = {"id": p.id, "member": {"id": str(p.member_id), "name": p.member.name, "code": p.member.code, "phone": p.member.phone},
           "name": p.name, "goal": p.goal, "diet_type": p.diet_type, "status": p.status,
           "trainer": {"id": p.trainer_id, "name": p.trainer.name} if p.trainer_id else None,
           "start_date": p.start_date.isoformat() if p.start_date else None,
           "end_date": p.end_date.isoformat() if p.end_date else None,
           "review_date": p.review_date.isoformat() if p.review_date else None,
           "current_version": p.current_version, "source_type": p.source_type, "source_ref": p.source_ref,
           "updated_at": p.updated_at.isoformat()}
    if v is not None:
        out["version"] = {"version_no": v.version_no, "instructions": v.instructions, "change_note": v.change_note,
                          "meals": v.meals, "created_at": v.created_at.isoformat()}
        if with_nutrition:
            out["nutrition"] = N.from_snapshot(v.meals)
            prof = getattr(p.member, "fitness", None)
            profile = {"food_allergies": prof.food_allergies, "diet_type": prof.diet_type} if prof else {}
            out["review_flags"] = N.review_flags(v.meals, profile, p.member.age, p.diet_type, result=out["nutrition"])
        out["versions"] = list(p.versions.values_list("version_no", flat=True))
    return out


def create_diet_plan(request, member, data, meals, status="Draft", source_type="scratch", source_ref=""):
    if status not in DietPlan.STATUSES:
        raise ApiError(400, "bad_status", "Unknown status.")
    f = plan_fields(data)
    trainer = None
    if data.get("trainer_id"):
        trainer = get_in_gym(Trainer, request.client, data["trainer_id"], "Trainer")
    elif scope_for(request.staff, "diet") == SCOPE_OWN:
        trainer = C.trainer_for(request.staff)
    meals = snapshot(request.client, clean_meals(meals))
    with transaction.atomic():
        p = DietPlan.objects.create(client=request.client, member=member, trainer=trainer, status=status, current_version=1,
                                    source_type=source_type, source_ref=str(source_ref or ""), created_by=request.staff, **f)
        DietPlanVersion.objects.create(plan=p, version_no=1, instructions=str(data.get("instructions") or ""),
                                       change_note="Created", meals=meals, created_by=request.staff, **f)
    log(request, "diet_plan_created", member.name, plan=p.name)
    return p


def update_draft(request, p, data, meals):
    if p.status != "Draft":
        raise ApiError(400, "not_draft", "Only a draft can be edited in place. Revise the plan instead.")
    f = plan_fields(data)
    with transaction.atomic():
        for k, v in f.items():
            setattr(p, k, v)
        p.save()
        v = p.versions.get(version_no=p.current_version)
        for k, val in f.items():
            setattr(v, k, val)
        v.instructions = str(data.get("instructions") or "")
        v.meals = snapshot(request.client, clean_meals(meals))
        v.save()
    return p


def revise_plan(request, p, data, meals):
    if p.status == "Draft":
        raise ApiError(400, "is_draft", "A draft is edited in place, not revised.")
    if p.status == "Archived":
        raise ApiError(400, "archived", "An archived plan can't be revised.")
    f = plan_fields(data)
    with transaction.atomic():
        p = DietPlan.objects.select_for_update().get(pk=p.pk)
        new_no = p.current_version + 1
        DietPlanVersion.objects.create(plan=p, version_no=new_no, instructions=str(data.get("instructions") or ""),
                                       change_note=text(data, "change_note", 200) or "Revised",
                                       meals=snapshot(request.client, clean_meals(meals)), created_by=request.staff, **f)
        for k, v in f.items():
            setattr(p, k, v)
        p.current_version = new_no
        p.save()
    log(request, "diet_plan_revised", p.member.name, version=new_no)
    return p


def set_plan_status(request, p, status):
    if status == p.status:
        return p
    if status not in DietPlan.TRANSITIONS.get(p.status, set()):
        raise ApiError(400, "bad_transition", f"A {p.status} plan can't be marked {status}.")
    p.status = status
    p.save(update_fields=["status", "updated_at"])
    return p


def replace_active_plans(request, member, keep):
    """Giving a member a new plan in place of the current one: their other
    Active plans are marked Completed (kept in history, not deleted)."""
    old = list(DietPlan.objects.filter(client=request.client, member=member, status="Active").exclude(pk=keep.pk))
    for p in old:
        p.status = "Completed"
        p.save(update_fields=["status", "updated_at"])
        log(request, "diet_plan_replaced", member.name, plan=p.name, by=keep.name)
    return len(old)


def assign_source(request, member, data):
    """From a starter plan (key) or a gym template (id) — v1.2.0 assign_template()."""
    if data.get("starter_key"):
        src = SP.get_starter_plan(data["starter_key"])
        if src is None:
            raise ApiError(404, "not_found", "Starter plan not found.")
        meals = [{"name": m["name"], "time": m["time"],
                  "items": [{"food_name": i["food"], "portion": i["portion"], "allergens": list(i["allergens"])} for i in m["items"]]}
                 for m in src["meals"]]
        stype, sref, weeks = "starter", src["key"], src.get("weeks")
        base = {"name": data.get("name") or src["name"], "goal": src["goal"], "diet_type": src["diet_type"],
                "instructions": src.get("instructions")}
    else:
        t = get_in_gym(DietTemplate, request.client, data.get("template_id"), "Template")
        meals, stype, sref, weeks = t.meals, "template", t.id, t.weeks
        base = {"name": data.get("name") or t.name, "goal": t.goal, "diet_type": t.diet_type, "instructions": t.instructions}
    start = parse_date(data.get("start_date"), "start_date", today_for(request.client))
    end = parse_date(data["end_date"], "end_date") if data.get("end_date") else (start + timedelta(weeks=weeks) if weeks else None)
    body = {**base, "start_date": start.isoformat(), "end_date": end.isoformat() if end else None,
            "review_date": data.get("review_date"), "trainer_id": data.get("trainer_id")}
    return create_diet_plan(request, member, body, meals, status=data.get("status") or "Active", source_type=stype, source_ref=sref)


def diet_share_text(p, pdf_url=None):
    v = p.versions.get(version_no=p.current_version)
    st = GymSettings.for_client(p.client)
    lines = [f"*{st.gym_name}* — Diet plan for {p.member.name}", f"_{p.name}_ (v{v.version_no})", ""]
    if pdf_url:
        lines += ["📄 Full plan with calories (PDF):", pdf_url, ""]
    for meal in v.meals:
        lines.append(f"*{meal['name']}*" + (f" · {meal['time']}" if meal.get("time") else ""))
        for it in meal["items"]:
            lines.append(f"• {it['food_name']}" + (f" — {it['portion']}" if it.get("portion") else ""))
        lines.append("")
    if v.instructions:
        lines += ["Notes: " + v.instructions, ""]
    lines.append("_Powered by FitDesk Pro_")
    return "\n".join(lines)


# =============================================================================
# PROGRESS & CONSENT
# =============================================================================

MEASURES = ["weight_kg", "body_fat_percent", "chest_cm", "waist_cm", "hips_cm", "arms_cm", "thighs_cm"]
MAX_PHOTO_BYTES = 3 * 1024 * 1024


def progress_json(e):
    out = {"id": e.id, "date": e.date.isoformat(), "notes": e.notes, "has_photo": bool(e.photo),
           "recorded_by": e.recorded_by.name if e.recorded_by_id else None}
    for k in MEASURES:
        v = getattr(e, k)
        out[k] = str(v) if v is not None else None
    return out


def add_progress(request, member, data, photo=None):
    e = ProgressEntry(member=member, date=parse_date(data.get("date"), "date", today_for(request.client)),
                      notes=text(data, "notes", 300), recorded_by=request.staff)
    any_value = False
    for k in MEASURES:
        if data.get(k) not in (None, ""):
            v = dec(data[k], k).quantize(Decimal("0.1"))
            if v > 500:
                raise ApiError(400, f"bad_{k}", f"{k.replace('_', ' ')} looks wrong.")
            setattr(e, k, v)
            any_value = True
    if photo is not None:
        from django.conf import settings
        if getattr(settings, "MEDIA_EPHEMERAL", False):
            raise ApiError(400, "photos_not_set_up", "Photo storage isn't set up on this server yet. "
                           "Save the measurements without a photo for now.")
        if photo.size > MAX_PHOTO_BYTES:
            raise ApiError(400, "photo_too_big", "Photo must be under 3 MB.")
        if (photo.content_type or "") not in ("image/jpeg", "image/png", "image/webp"):
            raise ApiError(400, "bad_photo", "Photo must be JPG, PNG or WEBP.")
        e.photo = photo
        any_value = True
    if not any_value:
        raise ApiError(400, "empty_entry", "Enter at least one measurement or a photo.")
    e.save()
    if e.weight_kg is not None:
        from gym_core.models import FitnessProfile
        fp, _ = FitnessProfile.objects.get_or_create(member=member)
        latest = member.progress.exclude(weight_kg=None).order_by("-date", "-created_at").first()
        if latest and latest.pk == e.pk:
            fp.current_weight_kg = e.weight_kg
            fp.save(update_fields=["current_weight_kg", "updated_at"])
    log(request, "progress_recorded", member.name)
    return e


DEFAULT_CONSENT = (
    "I confirm that I am in good health and have told the gym about any medical condition, injury, pregnancy or "
    "medication that could affect my exercise. I will follow the trainers' instructions and the gym rules, and I "
    "understand that exercise carries some risk of injury. I will tell the gym if my health changes."
)


def sign_consent(request, member, data):
    sig = str(data.get("signature_png") or "")
    if not sig.startswith("data:image/png;base64,") or len(sig) > 300_000:
        raise ApiError(400, "bad_signature", "Draw a signature (PNG, under 300 KB).")
    answers = data.get("health_answers") or {}
    if not isinstance(answers, dict):
        raise ApiError(400, "bad_health_answers", "health_answers must be an object.")
    c = ConsentForm.objects.create(member=member, text=GymSettings.for_client(request.client).consent_text or DEFAULT_CONSENT,
                                   health_answers=answers, signed_name=text(data, "signed_name", 120, required=True),
                                   signature_png=sig, witnessed_by=request.staff, device=request.device)
    log(request, "consent_signed", member.name)
    return c


# =============================================================================
# SUPPORT
# =============================================================================

def raise_ticket(request, data):
    subject = text(data, "subject", 180, required=True)
    message = text(data, "message", 4000, required=True)
    category = choice(data.get("category"), ["Technical Issue", "Account & Access", "Billing & Payment", "Feature Request", "Other"],
                      "category", "Other")
    body = f"{message}\n\n— {request.staff.name} ({request.staff.role}) · {request.staff.phone} · device: {request.device.name}"
    q = SupportQuery.objects.create(client=request.client, subject=f"[{category}] {subject}", message=body)
    try:
        from licensing.notifications import send_telegram_notification
        send_telegram_notification(f"🆘 <b>{request.client.gym_name}</b>\n{subject}\nFrom {request.staff.name}")
    except Exception:
        pass
    log(request, "support_ticket", subject)
    return q
