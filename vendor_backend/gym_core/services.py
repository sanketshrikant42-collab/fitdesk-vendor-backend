"""Database operations for Phase 3A. Every function takes the signed-in
user and works only inside that user's gym."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from gym_accounts.api import ApiError
from gym_accounts.models import GymProfile, TeamActivity
from gym_accounts.permissions import SCOPE_OWN, has_perm, scope_for
from licensing.phone_utils import validate_whatsapp_phone

from . import rules as R
from .models import (Bill, CheckIn, Counter, FitnessProfile, Freeze, GymSettings, Member, Membership, Payment, Plan,
                     SessionLog, Trainer)


# --- small helpers ----------------------------------------------------------------

def gym_tz(client):
    return ZoneInfo(GymProfile.for_client(client).time_zone or "Asia/Kolkata")


def today_for(client):
    """'Today' in the gym's own time zone (FIX: v1.2.0's server compared
    dates in UTC, so a licence ended at 05:30 IST)."""
    tz = ZoneInfo(GymProfile.for_client(client).time_zone or "Asia/Kolkata")
    return timezone.now().astimezone(tz).date()


def dec(value, field, default=None):
    if value in (None, ""):
        if default is not None:
            return default
        raise ApiError(400, f"bad_{field}", f"{field.replace('_', ' ').title()} is required.")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ApiError(400, f"bad_{field}", f"{field.replace('_', ' ').title()} must be a number.")
    if d < 0:
        raise ApiError(400, f"bad_{field}", f"{field.replace('_', ' ').title()} can't be negative.")
    return R.money(d)


def parse_date(value, field, default=None):
    if value in (None, ""):
        if default is not None:
            return default
        raise ApiError(400, f"bad_{field}", f"{field.replace('_', ' ')} is required.")
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ApiError(400, f"bad_{field}", f"{field.replace('_', ' ')} must be a date like 2026-10-27.")


def clean_phone(raw):
    """Indian numbers typed with the trunk 0 ("09028644659", "+0 90286 44659")
    or with 0091 become +919028644659, so the same person can't be added
    twice under two spellings of one number."""
    s = str(raw or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits.startswith("0091") and len(digits) == 14:
        s = "+" + digits[2:]
    elif digits.startswith("0") and len(digits.lstrip("0")) == 10:
        s = digits.lstrip("0")
    phone, err = validate_whatsapp_phone(s)
    if err:
        raise ApiError(400, "bad_phone", err)
    return phone


def clean_email(raw):
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    v = str(raw or "").strip().lower()
    if not v:
        return ""
    try:
        validate_email(v)
    except ValidationError:
        raise ApiError(400, "bad_email", "Enter a valid email address, e.g. name@gmail.com.")
    return v


def same_phone_member(client, phone, exclude=None):
    """Another member (archived too) whose number ends in the same 10 digits."""
    last10 = "".join(ch for ch in phone if ch.isdigit())[-10:]
    qs = Member.objects.filter(client=client, phone__endswith=last10)
    if exclude:
        qs = qs.exclude(pk=exclude)
    return qs.first()


def text(data, key, maxlen, required=False):
    v = str(data.get(key) or "").strip()
    if required and not v:
        raise ApiError(400, f"bad_{key}", f"{key.replace('_', ' ').title()} is required.")
    return v[:maxlen]


def get_in_gym(model, client, pk, name="Item"):
    try:
        obj = model.objects.filter(client=client, pk=pk).first()
    except (ValueError, Exception):
        obj = None
    if obj is None:
        raise ApiError(404, "not_found", f"{name} not found.")
    return obj


def next_number(client, key):
    with transaction.atomic():
        c, _ = Counter.objects.select_for_update().get_or_create(client=client, key=key)
        c.value += 1
        c.save(update_fields=["value"])
        return c.value


def log(request, action, target="", **meta):
    TeamActivity.record(request.client, action, actor=request.staff, device=request.device, target=target, **meta)


# --- trainers' "own clients" scope ----------------------------------------------------

def trainer_for(user):
    t = Trainer.objects.filter(client=user.client, staff_user=user).first()
    if t is None and user.linked_record:
        t = Trainer.objects.filter(client=user.client, code=user.linked_record).first()
    return t


def scoped_members(user, module="members"):
    qs = Member.objects.filter(client=user.client)
    if scope_for(user, module) == SCOPE_OWN:
        t = trainer_for(user)
        if t is None:
            return qs.none()
        qs = qs.filter(memberships__trainer=t, memberships__status=Membership.STATUS_ACTIVE).distinct()
    return qs


def require_member_in_scope(user, member, module="members"):
    if not scoped_members(user, module).filter(pk=member.pk).exists():
        raise ApiError(404, "not_found", "Member not found.")


# --- settings -------------------------------------------------------------------------

SETTINGS_FIELDS = ["gym_name", "tagline", "address", "phone", "email", "gstin", "member_code_prefix", "receipt_prefix",
                   "upi_id", "upi_payee_name", "receipt_footer", "terms", "whatsapp_receipt_template",
                   "enquiry_whatsapp_template", "consent_text"]


def settings_json(s):
    out = {f: getattr(s, f) for f in SETTINGS_FIELDS}
    out.update({"gst_percent": str(s.gst_percent), "apply_gst_by_default": s.apply_gst_by_default,
                "trainer_share_includes_gst": s.trainer_share_includes_gst,
                "managers_see_salaries": s.managers_see_salaries, "holidays_paid": s.holidays_paid, "referral_reward_days": s.referral_reward_days,
                "effective_member_code_prefix": s.member_code_prefix or R.code_prefix(s.gym_name),
                "time_zone": GymProfile.for_client(s.client).time_zone,
                "has_logo": bool(s.logo), "logo_updated_at": s.logo_updated_at.isoformat() if s.logo_updated_at else None})
    return out


def update_settings(request, data):
    s = GymSettings.for_client(request.client)
    for f in SETTINGS_FIELDS:
        if f in data:
            setattr(s, f, str(data[f] or "").strip()[:2000])
    if "gst_percent" in data:
        pct = dec(data["gst_percent"], "gst_percent", R.ZERO)
        if pct > 28:
            raise ApiError(400, "bad_gst_percent", "GST % looks wrong (max 28).")
        s.gst_percent = pct
    if "referral_reward_days" in data:
        try:
            s.referral_reward_days = max(0, min(365, int(data["referral_reward_days"] or 0)))
        except (TypeError, ValueError):
            raise ApiError(400, "bad_referral_reward_days", "Referral reward days must be a number.")
    for f in ("apply_gst_by_default", "trainer_share_includes_gst", "managers_see_salaries", "holidays_paid"):
        if f in data:
            setattr(s, f, bool(data[f]))
    if not s.gym_name:
        raise ApiError(400, "bad_gym_name", "Gym name is required.")
    s.member_code_prefix = "".join(ch for ch in s.member_code_prefix.upper() if ch.isalnum())[:6]
    s.receipt_prefix = "".join(ch for ch in s.receipt_prefix.upper() if ch.isalnum())[:8] or "RCPT"
    s.save()
    log(request, "settings_changed", "Gym settings")
    return s


# --- plans ----------------------------------------------------------------------------

def plan_json(p):
    final = R.money(p.fee - p.fee * p.discount_percent / 100)
    return {"id": p.id, "name": p.name, "duration_days": p.duration_days, "fee": str(p.fee), "final_fee": str(final),
            "description": p.description, "discount_percent": str(p.discount_percent),
            "is_personal_training": p.is_personal_training, "trainer_share_percent": str(p.trainer_share_percent),
            "pt_sessions": p.pt_sessions, "members_covered": p.members_covered,
            "freeze_max_days": p.freeze_max_days, "freeze_max_times": p.freeze_max_times, "status": p.status,
            "gst_inclusive": p.gst_inclusive}


def save_plan(request, data, plan=None):
    p = plan or Plan(client=request.client)
    if plan is None or "name" in data:
        p.name = text(data, "name", 120, required=True)
    if plan is None or "duration_days" in data:
        try:
            p.duration_days = int(data.get("duration_days"))
            assert 1 <= p.duration_days <= 3660
        except (TypeError, ValueError, AssertionError):
            raise ApiError(400, "bad_duration_days", "Duration must be 1 to 3660 days.")
    if plan is None or "fee" in data:
        p.fee = dec(data.get("fee"), "fee")
    for f in ("description",):
        if f in data:
            p.description = text(data, f, 2000)
    for f in ("discount_percent", "trainer_share_percent"):
        if f in data:
            v = dec(data[f], f, R.ZERO)
            if v > 100:
                raise ApiError(400, f"bad_{f}", "Percent can't be more than 100.")
            setattr(p, f, v)
    if "is_personal_training" in data:
        p.is_personal_training = bool(data["is_personal_training"])
    if "gst_inclusive" in data:
        p.gst_inclusive = bool(data["gst_inclusive"])
        if p.gst_inclusive and GymSettings.for_client(request.client).gst_percent <= 0:
            raise ApiError(400, "gst_not_set_up", "Set the GST % in Settings → Receipts & GST first.")
    for f, lo, hi in (("pt_sessions", 0, 500), ("members_covered", 1, 10), ("freeze_max_days", 0, 365), ("freeze_max_times", 0, 50)):
        if f in data:
            try:
                v = int(data[f])
                assert lo <= v <= hi
            except (TypeError, ValueError, AssertionError):
                raise ApiError(400, f"bad_{f}", f"{f.replace('_', ' ')} must be {lo} to {hi}.")
            setattr(p, f, v)
    if p.pt_sessions and not p.is_personal_training:
        raise ApiError(400, "bad_pt_sessions", "Only personal-training plans can be session packs.")
    try:
        with transaction.atomic():
            p.save()
    except IntegrityError:
        raise ApiError(409, "plan_exists", "A plan with this name already exists.")
    log(request, "plan_saved" if plan else "plan_created", p.name)
    return p


# --- trainers ---------------------------------------------------------------------------

def trainer_json(t):
    return {"id": t.id, "code": t.code, "name": t.name, "phone": t.phone, "gender": t.gender,
            "specialization": t.specialization, "status": t.status,
            "joining_date": t.joining_date.isoformat() if t.joining_date else None,
            "login": {"id": t.staff_user_id, "name": t.staff_user.name} if t.staff_user_id else None,
            "staff": _trainer_staff(t)}


def _trainer_staff(t):
    try:
        s = t.staff_record
    except Exception:          # no linked staff record (freelance trainer) or gym_ops not installed
        return None
    return {"id": s.id, "code": s.code, "designation": s.designation, "department": s.department, "status": s.status}


def remove_trainer(request, t):
    """Delete a trainer nobody has used yet; otherwise mark inactive so
    old bills and sessions keep their trainer."""
    used = t.bills.exists() or t.memberships.exists() or SessionLog.objects.filter(trainer=t).exists()
    if used:
        t.status = "inactive"
        t.save(update_fields=["status"])
        log(request, "trainer_deactivated", t.name)
        return "inactive"
    log(request, "trainer_removed", t.name)
    t.delete()
    return "deleted"


def trainer_profile(request, t):
    """Everything about one trainer for the profile screen: clients now and
    before, sessions logged, and PT earnings (money only for people who may
    see it: owner, managers if allowed to see salaries, or the trainer themself)."""
    from datetime import timedelta
    on = today_for(request.client)
    out = trainer_json(t)
    staff = getattr(t, "staff_record", None) if hasattr(t, "staff_record") else None
    out["staff_record"] = {"id": staff.id, "code": staff.code, "designation": staff.designation} if staff else None
    ms = list(Membership.objects.filter(client=request.client, trainer=t, status=Membership.STATUS_ACTIVE)
              .select_related("member", "plan", "bill").prefetch_related("sessions").order_by("-end_date"))
    current, past_ids = [], set()
    for m in ms:
        if m.start_date <= on <= m.end_date or m.start_date > on:
            used = m.sessions.count() if m.sessions_total else 0
            current.append({"membership_id": str(m.id), "member": {"id": str(m.member_id), "code": m.member.code,
                            "name": m.member.name, "phone": m.member.phone},
                            "plan": m.plan.name, "start_date": m.start_date.isoformat(), "end_date": m.end_date.isoformat(),
                            "days_left": (m.end_date - on).days, "upcoming": m.start_date > on,
                            "sessions": {"total": m.sessions_total, "used": used, "left": m.sessions_total - used} if m.sessions_total else None})
        else:
            past_ids.add(m.member_id)
    current_ids = {c["member"]["id"] for c in current}
    past_ids = {str(x) for x in past_ids} - current_ids
    month_start = on.replace(day=1)
    logs = SessionLog.objects.filter(trainer=t).select_related("membership__member").order_by("-session_date", "-created_at")
    out.update({
        "clients": current, "past_clients": len(past_ids),
        "sessions": {"this_month": logs.filter(session_date__gte=month_start).count(), "total": logs.count(),
                     "recent": [{"date": x.session_date.isoformat(), "member": x.membership.member.name,
                                 "member_code": x.membership.member.code, "notes": x.notes} for x in logs[:15]]},
        "money_visible": False,
    })
    me = request.staff
    # PT earnings are pay: same people who see salaries (owner; managers if the
    # owner allows it) plus the trainer themself.
    sees_pay = me.role == "owner" or has_perm(me, "salaries", "view")
    if sees_pay or (t.staff_user_id and t.staff_user_id == me.id):
        pays = Payment.objects.filter(bill__trainer=t, bill__is_personal_training=True, bill__status=Bill.STATUS_ACTIVE)
        months = []
        y, mo = on.year, on.month
        for _ in range(6):
            total = pays.filter(paid_on__year=y, paid_on__month=mo).aggregate(s=Sum("trainer_share"))["s"] or R.ZERO
            months.append({"month": f"{y:04d}-{mo:02d}", "trainer_share": str(R.money(total))})
            y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
        out.update({"money_visible": True, "earnings": {
            "this_month": months[0]["trainer_share"],
            "all_time": str(R.money(pays.aggregate(s=Sum("trainer_share"))["s"] or R.ZERO)),
            "pt_sales_all_time": str(R.money(pays.aggregate(s=Sum("amount"))["s"] or R.ZERO)),
            "by_month": months}})
    return out


def save_trainer(request, data, trainer=None):
    t = trainer or Trainer(client=request.client)
    if trainer is None or "name" in data:
        t.name = text(data, "name", 120, required=True)
    if trainer is None or "phone" in data:
        t.phone = clean_phone(data.get("phone"))
    for f, n in (("gender", 10), ("specialization", 120), ("status", 10)):
        if f in data:
            setattr(t, f, text(data, f, n))
    if "joining_date" in data:
        t.joining_date = parse_date(data["joining_date"], "joining_date") if data["joining_date"] else None
    if "staff_user_id" in data:
        from gym_accounts.models import StaffUser
        if data["staff_user_id"]:
            su = StaffUser.objects.filter(client=request.client, pk=data["staff_user_id"], role="trainer").first()
            if su is None:
                raise ApiError(400, "bad_staff_user", "Pick a trainer login from this gym.")
            t.staff_user = su
        else:
            t.staff_user = None
    if trainer is None:
        st = GymSettings.for_client(request.client)
        t.code = R.trainer_code(st.member_code_prefix or R.code_prefix(st.gym_name), next_number(request.client, "trainer"))
    try:
        with transaction.atomic():
            t.save()
    except IntegrityError:
        raise ApiError(409, "trainer_exists", "A trainer with this phone (or login) already exists.")
    log(request, "trainer_saved", t.name)
    return t


# --- members ----------------------------------------------------------------------------

def membership_state(member, on=None, memberships=None):
    """active | frozen | upcoming | expired | no_plan, plus the current
    membership and days left (v1.2.0: active while the latest expiry >= today)."""
    on = on or today_for(member.client)
    ms = memberships if memberships is not None else list(
        member.memberships.filter(status=Membership.STATUS_ACTIVE).select_related("plan", "trainer").prefetch_related("freezes"))
    ms = [m for m in ms if m.status == Membership.STATUS_ACTIVE]
    current = [m for m in ms if m.start_date <= on <= m.end_date]
    if current:
        m = max(current, key=lambda x: x.end_date)
        frozen = any(f.cancelled_at is None and f.start_date <= on <= f.end_date for f in m.freezes.all())
        return {"state": "frozen" if frozen else "active", "membership": m, "days_left": (m.end_date - on).days}
    future = [m for m in ms if m.start_date > on]
    if future:
        return {"state": "upcoming", "membership": min(future, key=lambda x: x.start_date), "days_left": None}
    if ms:
        m = max(ms, key=lambda x: x.end_date)
        return {"state": "expired", "membership": m, "days_left": (m.end_date - on).days}
    return {"state": "no_plan", "membership": None, "days_left": None}


def dues_for(member):
    out = R.ZERO
    for b in member.bills.filter(status=Bill.STATUS_ACTIVE).annotate(p=Sum("payments__amount")):
        out += R.remaining(b.total, b.p or 0)
    return out


def membership_json(m, on):
    used = m.sessions.count() if m.sessions_total else 0
    return {"id": str(m.id), "plan": {"id": m.plan_id, "name": m.plan.name}, "bill_id": str(m.bill_id),
            "receipt_no": m.bill.receipt_no, "start_date": m.start_date.isoformat(), "end_date": m.end_date.isoformat(),
            "original_end_date": m.original_end_date.isoformat(),
            "trainer": {"id": m.trainer_id, "name": m.trainer.name} if m.trainer_id else None,
            "sessions": {"total": m.sessions_total, "used": used, "left": m.sessions_total - used} if m.sessions_total else None,
            "freezes": [{"id": f.id, "start_date": f.start_date.isoformat(), "end_date": f.end_date.isoformat(),
                         "days": f.days, "reason": f.reason} for f in m.freezes.all() if f.cancelled_at is None],
            "is_current": m.start_date <= on <= m.end_date, "status": m.status}


def member_json(member, detail=False, on=None):
    on = on or today_for(member.client)
    st = membership_state(member, on)
    m = st["membership"]
    out = {"id": str(member.id), "code": member.code, "name": member.name, "phone": member.phone, "email": member.email,
           "gender": member.gender, "age": member.age, "is_archived": member.is_archived,
           "status": st["state"], "days_left": st["days_left"],
           "plan": m.plan.name if m else None, "end_date": m.end_date.isoformat() if m else None,
           "trainer": m.trainer.name if m and m.trainer_id else None,
           "created_at": member.created_at.isoformat(), "version": member.version}
    if detail:
        out.update({"date_of_birth": member.date_of_birth.isoformat() if member.date_of_birth else None,
                    "address": member.address, "emergency_contact": member.emergency_contact,
                    "biometric_id": member.biometric_id, "notes": member.notes, "dues": str(dues_for(member)),
                    "memberships": [membership_json(x, on) for x in member.memberships.select_related("plan", "trainer", "bill").prefetch_related("freezes", "sessions")],
                    "fitness": fitness_json(getattr(member, "fitness", None)),
                    "visits": {"total": member.checkins.count(),
                               "this_month": member.checkins.filter(local_date__gte=on.replace(day=1)).count(),
                               "last": member.checkins.order_by("-checked_in_at").values_list("checked_in_at", flat=True).first(),
                               "recent": [{"date": c.local_date.isoformat(), "at": c.checked_in_at.isoformat(), "source": c.source,
                                           "by": c.by_user.name if c.by_user_id else None, "override_reason": c.override_reason}
                                          for c in member.checkins.select_related("by_user").order_by("-checked_in_at")[:20]]}})
        if out["visits"]["last"]:
            out["visits"]["last"] = out["visits"]["last"].isoformat()
    return out


FITNESS_FIELDS = ["current_weight_kg", "height_cm", "target_weight_kg", "fitness_goal", "activity_level",
                  "workout_days_per_week", "target_date", "diet_type", "food_allergies", "food_preferences",
                  "meals_per_day", "workout_time"]


def fitness_json(f):
    if f is None:
        return None
    out = {}
    for k in FITNESS_FIELDS:
        v = getattr(f, k)
        out[k] = v.isoformat() if isinstance(v, date) else (str(v) if isinstance(v, Decimal) else v)
    return out


def save_member(request, data, member=None):
    m = member or Member(client=request.client, created_by=request.staff)
    if member is not None and "version" in data and int(data["version"] or 0) != member.version:
        raise ApiError(409, "edit_conflict", "Someone else changed this member a moment ago. Reload and try again.",
                       current=member_json(member, detail=True))
    if member is None or "name" in data:
        m.name = text(data, "name", 120, required=True)
    if member is None or "phone" in data:
        m.phone = clean_phone(data.get("phone"))
    if member is None or "gender" in data:
        m.gender = text(data, "gender", 10, required=True)
    for f, n in (("email", 254), ("address", 500), ("emergency_contact", 30), ("biometric_id", 40), ("notes", 2000)):
        if f in data:
            setattr(m, f, text(data, f, n))
    if "email" in data:
        m.email = clean_email(m.email)
    if member is None or "phone" in data:
        same = same_phone_member(request.client, m.phone, exclude=m.pk if member else None)
        if same:
            raise ApiError(409, "phone_taken", f"{same.name} ({same.code}) already has this mobile number.",
                           member={"id": str(same.id), "name": same.name, "code": same.code})
    if "date_of_birth" in data:
        m.date_of_birth = parse_date(data["date_of_birth"], "date_of_birth") if data["date_of_birth"] else None
    if "age" in data:
        m.age = int(data["age"]) if str(data["age"] or "").isdigit() else None
    if m.date_of_birth:
        t = today_for(request.client)
        m.age = t.year - m.date_of_birth.year - ((t.month, t.day) < (m.date_of_birth.month, m.date_of_birth.day))
    try:
        with transaction.atomic():
            if member is None:
                s = GymSettings.for_client(request.client)
                prefix = s.member_code_prefix or R.code_prefix(s.gym_name)
                m.code = R.member_code(prefix, next_number(request.client, "member"))
            else:
                m.version += 1
            m.save()
    except IntegrityError as e:
        msg = str(e).lower()
        if "biometric" in msg:
            raise ApiError(409, "biometric_taken", "This fingerprint ID is already used by another member.")
        raise ApiError(409, "phone_taken", "A member with this mobile number already exists.")
    log(request, "member_updated" if member else "member_added", f"{m.name} ({m.code})")
    return m


def save_fitness(request, member, data):
    f, _ = FitnessProfile.objects.get_or_create(member=member)
    for k in FITNESS_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k in ("current_weight_kg", "height_cm", "target_weight_kg"):
            v = dec(v, k, None).quantize(Decimal("0.1")) if v not in (None, "") else None
        elif k in ("workout_days_per_week", "meals_per_day"):
            v = int(v) if str(v or "").isdigit() else None
        elif k == "target_date":
            v = parse_date(v, k) if v else None
        else:
            v = str(v or "")[:200]
        setattr(f, k, v)
    f.save()
    log(request, "fitness_updated", member.name)
    return f


# --- billing ------------------------------------------------------------------------------

def bill_json(b, with_links=True):
    paid = sum((p.amount for p in b.payments.all()), R.ZERO)
    s = GymSettings.for_client(b.client)
    ms = list(b.memberships.select_related("member"))
    first = ms[0] if ms else None
    out = {"id": str(b.id), "receipt_no": b.receipt_no, "kind": b.kind, "status": b.status,
           "member": {"id": str(b.member_id), "name": b.member.name, "code": b.member.code, "phone": b.member.phone},
           "covers": [{"member_id": str(x.member_id), "name": x.member.name, "code": x.member.code} for x in ms],
           "plan": {"id": b.plan_id, "name": b.plan.name},
           "trainer": {"id": b.trainer_id, "name": b.trainer.name} if b.trainer_id else None,
           "start_date": first.start_date.isoformat() if first else None,
           "end_date": first.original_end_date.isoformat() if first else None,
           "fee": str(b.fee), "discount": str(b.discount), "gst_applied": b.gst_applied, "gst_percent": str(b.gst_percent),
           "gst_inclusive": b.gst_inclusive,
           "gst_amount": str(b.gst_amount), "total": str(b.total), "paid": str(paid),
           "remaining": str(R.remaining(b.total, paid)), "payment_status": R.payment_status(b.total, paid),
           "trainer_share": str(sum((p.trainer_share for p in b.payments.all()), R.ZERO)),
           "gym_share": str(sum((p.gym_share for p in b.payments.all()), R.ZERO)),
           "payments": [{"id": p.id, "amount": str(p.amount), "mode": p.mode, "reference_no": p.reference_no,
                         "paid_on": p.paid_on.isoformat(), "trainer_share": str(p.trainer_share), "gym_share": str(p.gym_share),
                         "received_by": p.received_by.name if p.received_by_id else None} for p in b.payments.all()],
           "remarks": b.remarks, "created_at": b.created_at.isoformat(),
           "created_by": b.created_by.name if b.created_by_id else None, "cancelled_reason": b.cancelled_reason}
    if with_links:
        ctx = {"gym_name": s.gym_name, "member_name": b.member.name, "receipt_no": b.receipt_no, "plan_name": b.plan.name,
               "joining_date": first.start_date.strftime("%d %b %Y") if first else "",
               "expiry_date": first.original_end_date.strftime("%d %b %Y") if first else "", "amount_paid": R.rupees(paid)}
        msg = R.render_template(s.whatsapp_receipt_template or R.DEFAULT_WHATSAPP_RECEIPT, ctx)
        due = R.remaining(b.total, paid)
        out["whatsapp"] = {"text": msg, "link": R.whatsapp_link(b.member.phone, msg)}
        out["upi"] = {"link": R.upi_link(s.upi_id, s.upi_payee_name or s.gym_name, due, f"{b.receipt_no} {b.member.name}"),
                      "amount": str(due), "upi_id": s.upi_id} if due > 0 and s.upi_id else None
    return out


def _bill_inputs(request, data):
    s = GymSettings.for_client(request.client)
    plan = get_in_gym(Plan, request.client, data.get("plan_id"), "Plan")
    if plan.status != Plan.STATUS_ACTIVE:
        raise ApiError(400, "plan_archived", "This plan is no longer offered.")
    payer = get_in_gym(Member, request.client, data.get("member_id"), "Member")
    ids = [str(x) for x in (data.get("member_ids") or [data.get("member_id")])]
    if str(payer.id) not in ids:
        ids = [str(payer.id)] + ids
    ids = list(dict.fromkeys(ids))
    if len(ids) != plan.members_covered:
        raise ApiError(400, "wrong_member_count",
                       f"{plan.name} covers {plan.members_covered} member(s); {len(ids)} chosen.")
    covered = [get_in_gym(Member, request.client, i, "Member") for i in ids]
    trainer = None
    if plan.is_personal_training:
        trainer = get_in_gym(Trainer, request.client, data.get("trainer_id"), "Trainer") if data.get("trainer_id") else None
        if trainer is None:
            raise ApiError(400, "trainer_required", "Personal-training plans need a trainer.")   # v1.2.0 rule
        if trainer.status != "active":
            raise ApiError(400, "trainer_inactive", f"{trainer.name} is no longer an active trainer.")
    kind = data.get("kind") or "new"
    if kind not in (Bill.KIND_NEW, Bill.KIND_RENEWAL):
        raise ApiError(400, "bad_kind", "kind must be new or renewal.")
    if kind == Bill.KIND_RENEWAL and not data.get("start_date"):
        last = Membership.objects.filter(member=payer, status=Membership.STATUS_ACTIVE).order_by("-end_date").first()
        start = R.renewal_start(last.end_date) if last else today_for(request.client)
    else:
        start = parse_date(data.get("start_date"), "start_date", today_for(request.client))
    end = R.expiry_date(start, plan.duration_days, plan.name)
    fee = dec(data.get("fee"), "fee", plan.fee)
    discount = dec(data.get("discount"), "discount", R.ZERO)
    inclusive = plan.gst_inclusive
    gst_applied = True if inclusive else (bool(data["apply_gst"]) if "apply_gst" in data else s.apply_gst_by_default)
    if gst_applied and s.gst_percent <= 0:
        raise ApiError(400, "gst_not_set_up", "Set the GST % in gym settings first.")   # v1.2.0 rule
    try:
        totals = (R.bill_totals_inclusive(fee, discount, s.gst_percent) if inclusive
                  else R.bill_totals(fee, discount, gst_applied, s.gst_percent))
    except ValueError as e:
        raise ApiError(400, "bad_amount", str(e))
    return {"settings": s, "plan": plan, "payer": payer, "covered": covered, "trainer": trainer, "kind": kind,
            "start": start, "end": end, "fee": fee, "discount": discount, "gst_applied": gst_applied,
            "gst_inclusive": inclusive, **totals}


def quote(request, data):
    i = _bill_inputs(request, data)
    paid = dec(data.get("amount_paid"), "amount_paid", R.ZERO)
    t, g = R.trainer_gym_share(paid, i["plan"].is_personal_training, i["plan"].trainer_share_percent, i["net"], i["total"],
                               i["settings"].trainer_share_includes_gst)
    return {"plan": i["plan"].name, "start_date": i["start"].isoformat(), "end_date": i["end"].isoformat(),
            "fee": str(i["fee"]), "discount": str(i["discount"]), "gst_applied": i["gst_applied"], "gst_inclusive": i["gst_inclusive"],
            "taxable": str(i["net"]),
            "gst_percent": str(i["settings"].gst_percent if i["gst_applied"] else 0), "gst_amount": str(i["gst_amount"]),
            "total": str(i["total"]), "amount_paid": str(paid), "remaining": str(R.remaining(i["total"], paid)),
            "payment_status": R.payment_status(i["total"], paid), "trainer_share": str(t), "gym_share": str(g),
            "covers": [m.name for m in i["covered"]]}


def _check_payment(data, due):
    amount = dec(data.get("amount_paid", data.get("amount")), "amount_paid", R.ZERO)
    if amount > due:
        raise ApiError(400, "overpaid", f"Amount is more than what's due ({R.rupees(due)}).")
    mode = str(data.get("payment_mode") or data.get("mode") or "Cash")
    if amount > 0 and mode not in Payment.MODES:
        raise ApiError(400, "bad_mode", f"Payment mode must be one of {Payment.MODES}.")
    return amount, mode, str(data.get("reference_no") or "")[:60]


def create_bill(request, data):
    ref = str(data.get("client_ref") or "")[:64]
    if ref:
        existing = Bill.objects.filter(client=request.client, client_ref=ref).first()
        if existing:
            return existing, False
    i = _bill_inputs(request, data)
    if i["discount"] > 0 and not has_perm(request.staff, "billing", "edit"):
        raise ApiError(403, "permission_denied", "Your role can't give discounts.", module="billing", action="edit")
    if i["fee"] != i["plan"].fee and not has_perm(request.staff, "billing", "edit"):
        raise ApiError(403, "permission_denied", "Your role can't change the plan fee.", module="billing", action="edit")
    amount, mode, refno = _check_payment(data, i["total"])
    paid_on = parse_date(data.get("payment_date"), "payment_date", today_for(request.client))
    s = i["settings"]
    with transaction.atomic():
        b = Bill.objects.create(
            client=request.client, receipt_no=R.receipt_no(s.receipt_prefix, next_number(request.client, "receipt")),
            member=i["payer"], plan=i["plan"], kind=i["kind"], trainer=i["trainer"], fee=i["fee"], discount=i["discount"],
            gst_applied=i["gst_applied"], gst_percent=s.gst_percent if i["gst_applied"] else R.ZERO, gst_amount=i["gst_amount"],
            gst_inclusive=i["gst_inclusive"],
            total=i["total"], is_personal_training=i["plan"].is_personal_training,
            trainer_share_percent=i["plan"].trainer_share_percent, trainer_share_includes_gst=s.trainer_share_includes_gst,
            remarks=str(data.get("remarks") or "")[:300], client_ref=ref, created_by=request.staff, device=request.device)
        for m in i["covered"]:
            Membership.objects.create(client=request.client, member=m, bill=b, plan=i["plan"], trainer=i["trainer"],
                                      start_date=i["start"], end_date=i["end"], original_end_date=i["end"],
                                      sessions_total=i["plan"].pt_sessions)
        if amount > 0:
            _add_payment(request, b, amount, mode, refno, paid_on)
    log(request, "bill_created", b.receipt_no, member=i["payer"].name, plan=i["plan"].name, total=str(b.total), paid=str(amount))
    return b, True


def _add_payment(request, b, amount, mode, refno, paid_on):
    t, g = R.trainer_gym_share(amount, b.is_personal_training, b.trainer_share_percent, b.total - b.gst_amount, b.total,
                               b.trainer_share_includes_gst)
    return Payment.objects.create(bill=b, amount=amount, mode=mode, reference_no=refno, paid_on=paid_on,
                                  trainer_share=t, gym_share=g, received_by=request.staff, device=request.device)


def collect_payment(request, bill, data):
    with transaction.atomic():
        b = Bill.objects.select_for_update().get(pk=bill.pk)
        if b.status != Bill.STATUS_ACTIVE:
            raise ApiError(400, "bill_cancelled", "This bill was cancelled.")
        paid = b.payments.aggregate(s=Sum("amount"))["s"] or R.ZERO
        due = R.remaining(b.total, paid)
        if due <= 0:
            raise ApiError(400, "already_paid", "This bill is already fully paid.")
        if data.get("amount") in (None, "") and data.get("amount_paid") in (None, ""):
            data = {**data, "amount": str(due)}            # v1.2.0 "Mark as paid" = settle the full balance
        amount, mode, refno = _check_payment(data, due)
        if amount <= 0:
            raise ApiError(400, "bad_amount_paid", "Enter the amount received.")
        p = _add_payment(request, b, amount, mode, refno,
                         parse_date(data.get("payment_date"), "payment_date", today_for(request.client)))
    log(request, "payment_received", b.receipt_no, amount=str(p.amount), mode=p.mode)
    return b


def cancel_bill(request, bill, data):
    reason = text(data, "reason", 200, required=True)
    with transaction.atomic():
        bill.status, bill.cancelled_reason = Bill.STATUS_CANCELLED, reason
        bill.save(update_fields=["status", "cancelled_reason"])
        bill.memberships.update(status=Membership.STATUS_CANCELLED)
    log(request, "bill_cancelled", bill.receipt_no, reason=reason)
    return bill


# --- freeze / pause --------------------------------------------------------------------------

def freeze(request, ms, data):
    plan = ms.plan
    if ms.status != Membership.STATUS_ACTIVE:
        raise ApiError(400, "not_active", "This membership is cancelled.")
    if plan.freeze_max_days <= 0:
        raise ApiError(400, "freeze_not_allowed", f"The {plan.name} plan doesn't allow freezing.")
    start = parse_date(data.get("start_date"), "start_date", today_for(request.client))
    try:
        days = int(data.get("days"))
        assert days >= 1
    except (TypeError, ValueError, AssertionError):
        raise ApiError(400, "bad_days", "Enter how many days to freeze.")
    if not (ms.start_date <= start <= ms.end_date):
        raise ApiError(400, "outside_membership", "The freeze must start while the membership is running.")
    active = [f for f in ms.freezes.all() if f.cancelled_at is None]
    used = sum(f.days for f in active)
    if used + days > plan.freeze_max_days:
        raise ApiError(400, "freeze_limit", f"Only {plan.freeze_max_days - used} freeze day(s) left on this membership.")
    if plan.freeze_max_times and len(active) >= plan.freeze_max_times:
        raise ApiError(400, "freeze_limit", f"This plan allows freezing {plan.freeze_max_times} time(s).")
    end = start + timedelta(days=days - 1)
    if any(not (end < f.start_date or start > f.end_date) for f in active):
        raise ApiError(400, "freeze_overlap", "These dates overlap an existing freeze.")
    with transaction.atomic():
        f = Freeze.objects.create(membership=ms, start_date=start, end_date=end, days=days,
                                  reason=text(data, "reason", 200), created_by=request.staff)
        ms.end_date = ms.end_date + timedelta(days=days)
        ms.save(update_fields=["end_date"])
    log(request, "membership_frozen", ms.member.name, days=days, start=start.isoformat(), new_end=ms.end_date.isoformat())
    return f


def unfreeze(request, ms, freeze_obj, data):
    """End a freeze early (member came back sooner). Unused days are taken
    back off the end date."""
    on = parse_date(data.get("date"), "date", today_for(request.client))
    if freeze_obj.cancelled_at is not None:
        raise ApiError(400, "not_frozen", "This freeze was already ended.")
    with transaction.atomic():
        if on <= freeze_obj.start_date:
            give_back = freeze_obj.days
            freeze_obj.cancelled_at = timezone.now()
            freeze_obj.save(update_fields=["cancelled_at"])
        else:
            new_days = min((on - freeze_obj.start_date).days, freeze_obj.days)
            give_back = freeze_obj.days - new_days
            freeze_obj.days, freeze_obj.end_date = new_days, freeze_obj.start_date + timedelta(days=new_days - 1)
            freeze_obj.save(update_fields=["days", "end_date"])
        ms.end_date = ms.end_date - timedelta(days=give_back)
        ms.save(update_fields=["end_date"])
    log(request, "membership_unfrozen", ms.member.name, days_returned=give_back)
    return ms


# --- PT session packs --------------------------------------------------------------------------

def log_session(request, ms, data):
    if not ms.sessions_total:
        raise ApiError(400, "not_session_pack", "This membership isn't a session pack.")
    on = parse_date(data.get("date"), "date", today_for(request.client))
    with transaction.atomic():
        ms = Membership.objects.select_for_update().get(pk=ms.pk)
        if ms.sessions.count() >= ms.sessions_total:
            raise ApiError(400, "no_sessions_left", "All sessions in this pack are used.")
        if not (ms.start_date <= on <= ms.end_date):
            raise ApiError(400, "outside_membership", "This pack isn't valid on that date.")
        SessionLog.objects.create(membership=ms, trainer=trainer_for(request.staff) or ms.trainer, session_date=on,
                                  notes=text(data, "notes", 200), logged_by=request.staff)
    log(request, "pt_session_logged", ms.member.name)
    return ms


# --- check-in ----------------------------------------------------------------------------------

def find_member_for_checkin(request, query):
    q = str(query or "").strip()
    if not q:
        raise ApiError(400, "bad_query", "Type a name, mobile number or member code.")
    qs = scoped_members(request.staff, "attendance").filter(is_archived=False)
    digits = "".join(ch for ch in q if ch.isdigit())
    exact = qs.filter(Q(code__iexact=q) | Q(biometric_id=q) | (Q(phone__endswith=digits[-10:]) if len(digits) >= 10 else Q(pk=None)))
    hits = list(exact[:5]) or list(qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(phone__contains=digits or q))[:10])
    on = today_for(request.client)
    out = []
    for m in hits:
        j = member_json(m, on=on)
        j["dues"] = str(dues_for(m))
        j["checked_in_today"] = m.checkins.filter(local_date=on).values_list("checked_in_at", flat=True).first()
        if j["checked_in_today"]:
            j["checked_in_today"] = j["checked_in_today"].isoformat()
        j["can_check_in"] = j["status"] == "active"
        out.append(j)
    return out


def check_in(request, data):
    member = get_in_gym(Member, request.client, data.get("member_id"), "Member")
    require_member_in_scope(request.staff, member, "attendance")
    on = today_for(request.client)
    st = membership_state(member, on)
    override = text(data, "override_reason", 120)
    if st["state"] != "active":
        if not override:
            msg = {"frozen": "Membership is frozen today.", "expired": "Membership has expired.",
                   "upcoming": "Membership hasn't started yet.", "no_plan": "No membership yet."}[st["state"]]
            raise ApiError(409, "not_allowed_in", msg, member_status=st["state"])
        if not has_perm(request.staff, "attendance", "edit"):
            raise ApiError(403, "permission_denied", "Only the owner or manager can let someone in without an active membership.")
    source = data.get("source") or ("trainer" if request.staff.role == "trainer" else "front_desk")
    if source not in dict(CheckIn.SOURCES):
        source = "front_desk"
    existing = CheckIn.objects.filter(client=request.client, member=member, local_date=on).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            c = CheckIn.objects.create(client=request.client, member=member, membership=st["membership"], local_date=on,
                                       source=source, override_reason=override, by_user=request.staff,
                                       device=request.device, client_ref=str(data.get("client_ref") or "")[:64])
    except IntegrityError:
        return CheckIn.objects.get(client=request.client, member=member, local_date=on), False
    return c, True


def checkin_json(c):
    return {"id": str(c.id), "member": {"id": str(c.member_id), "name": c.member.name, "code": c.member.code},
            "at": c.checked_in_at.isoformat(), "date": c.local_date.isoformat(), "source": c.source,
            "by": c.by_user.name if c.by_user_id else None, "device": c.device.name if c.device_id else None,
            "override_reason": c.override_reason}


# --- dashboard ----------------------------------------------------------------------------------

def dashboard(request):
    client, on = request.client, today_for(request.client)
    members = scoped_members(request.staff).filter(is_archived=False)
    ms = Membership.objects.filter(client=client, status=Membership.STATUS_ACTIVE, member__in=members)
    active_ids = set(ms.filter(start_date__lte=on, end_date__gte=on).values_list("member_id", flat=True))
    out = {"date": on.isoformat(), "members": members.count(), "active_members": len(active_ids),
           "expiring_7_days": ms.filter(end_date__gte=on, end_date__lte=on + timedelta(days=7)).values("member").distinct().count(),
           "checkins_today": CheckIn.objects.filter(client=client, local_date=on, member__in=members).count()}
    if has_perm(request.staff, "billing", "view"):
        pays = Payment.objects.filter(bill__client=client, bill__status=Bill.STATUS_ACTIVE)
        out["collected_today"] = str(pays.filter(paid_on=on).aggregate(s=Sum("amount"))["s"] or R.ZERO)
        out["collected_this_month"] = str(pays.filter(paid_on__gte=on.replace(day=1)).aggregate(s=Sum("amount"))["s"] or R.ZERO)
        dues = R.ZERO
        for b in Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE).annotate(p=Sum("payments__amount")):
            dues += R.remaining(b.total, b.p or 0)
        out["pending_dues"] = str(dues)
    return out


# --- removing a member added twice / by mistake ---------------------------------------------------

def member_records(m):
    """What's linked to a member. A member with none of these can be deleted
    for good; otherwise archive or merge, so history is never lost."""
    from gym_ops.models import ConsentForm, DietPlan, ProgressEntry, ReferralReward
    return {k: v for k, v in {
        "bills": Bill.objects.filter(member=m).count(),
        "memberships": Membership.objects.filter(member=m).count(),
        "check_ins": CheckIn.objects.filter(member=m).count(),
        "diet_plans": DietPlan.objects.filter(member=m).count(),
        "progress": ProgressEntry.objects.filter(member=m).count(),
        "consent_forms": ConsentForm.objects.filter(member=m).count(),
        "referral_rewards": ReferralReward.objects.filter(referrer=m).count(),
    }.items() if v}


def delete_member(request, m):
    recs = member_records(m)
    if recs:
        raise ApiError(409, "member_has_records", "This member has bills or other records, so they can't be deleted. "
                       "Archive them, or merge them into the other entry.", records=recs)
    label = f"{m.name} ({m.code})"
    m.delete()
    log(request, "member_deleted", label)


MERGE_FILL = ["email", "date_of_birth", "age", "address", "emergency_contact", "biometric_id", "notes"]


def merge_members(request, dup, keep):
    """Moves everything from `dup` (the duplicate) to `keep`, fills blanks on
    `keep` from `dup`, then deletes `dup`. Receipts keep their numbers."""
    from gym_auto.models import Reminder
    from gym_ops.models import ConsentForm, DietPlan, Enquiry, ProgressEntry, ReferralReward
    if dup.pk == keep.pk:
        raise ApiError(400, "same_member", "Pick a different member to keep.")
    if keep.client_id != dup.client_id:
        raise ApiError(404, "not_found", "Member not found.")
    moved = {}
    with transaction.atomic():
        days = set(CheckIn.objects.filter(member=keep).values_list("local_date", flat=True))
        clash = CheckIn.objects.filter(member=dup, local_date__in=days)
        clash.delete()                                   # both checked in that day: keep one
        for name, qs, field in (("bills", Bill.objects.filter(member=dup), "member"),
                                ("memberships", Membership.objects.filter(member=dup), "member"),
                                ("check_ins", CheckIn.objects.filter(member=dup), "member"),
                                ("diet_plans", DietPlan.objects.filter(member=dup), "member"),
                                ("progress", ProgressEntry.objects.filter(member=dup), "member"),
                                ("consent_forms", ConsentForm.objects.filter(member=dup), "member"),
                                ("referral_rewards", ReferralReward.objects.filter(referrer=dup), "referrer")):
            n = qs.update(**{field: keep})
            if n:
                moved[name] = n
        Enquiry.objects.filter(referred_by=dup).update(referred_by=keep)
        Enquiry.objects.filter(converted_member=dup).update(converted_member=keep)
        Reminder.objects.filter(member=dup).delete()      # the next daily run makes fresh ones
        for f in MERGE_FILL:
            if not getattr(keep, f) and getattr(dup, f):
                setattr(keep, f, getattr(dup, f))
        fp_dup, fp_keep = getattr(dup, "fitness", None), getattr(keep, "fitness", None)
        label = f"{dup.name} ({dup.code})"
        if fp_dup is not None and fp_keep is None:
            fp_dup.member = keep
            fp_dup.save()
        dup.delete()                                      # frees the phone / fingerprint ID first
        keep.version += 1
        keep.save()
    log(request, "member_merged", f"{label} → {keep.name} ({keep.code})", moved=moved)
    return keep, moved


# --- dashboard charts (30 Oct 2026) -----------------------------------------------------------

def _month_key(d):
    return f"{d.year}-{d.month:02d}"


def _months_back(on, n):
    """The last n months as "YYYY-MM", oldest first, ending with on's month."""
    y, m, out = on.year, on.month, []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def dashboard_insights(request):
    """Everything the dashboard charts need, in one call, worked out in the
    gym's time zone. Money and enquiry figures only for roles that may see
    them (same rule as the reports)."""
    from django.db.models import Count
    client, user, on = request.client, request.staff, today_for(request.client)
    tz = gym_tz(client)
    members = scoped_members(user).filter(is_archived=False).prefetch_related("memberships__freezes", "memberships__plan")
    out = {"date": on.isoformat()}

    # member status: one state per member, as on the member list
    status = {"active": 0, "expiring": 0, "frozen": 0, "upcoming": 0, "expired": 0, "no_plan": 0}
    plan_mix, birthdays = {}, []
    for m in members:
        st = membership_state(m, on, memberships=list(m.memberships.all()))
        key = st["state"]
        if key == "active" and st["days_left"] is not None and st["days_left"] <= 7:
            key = "expiring"
        status[key] += 1
        if st["state"] in ("active", "frozen") and st["membership"]:
            name = st["membership"].plan.name
            plan_mix[name] = plan_mix.get(name, 0) + 1
        dob = m.date_of_birth
        if dob and (dob.month, dob.day) == (on.month, on.day):
            birthdays.append({"id": str(m.id), "name": m.name, "code": m.code, "phone": m.phone,
                              "age": on.year - dob.year})
    out["member_status"] = status
    top = sorted(plan_mix.items(), key=lambda kv: -kv[1])
    if len(top) > 5:
        top = top[:5] + [("Other plans", sum(v for _, v in top[5:]))]
    out["plan_mix"] = [{"plan": k, "members": v} for k, v in top]
    out["birthdays_today"] = birthdays[:10]

    # check-ins, last 14 days
    since = on - timedelta(days=13)
    counts = dict(CheckIn.objects.filter(client=client, local_date__gte=since, local_date__lte=on, member__in=members)
                  .values_list("local_date").annotate(n=Count("id")).values_list("local_date", "n"))
    out["checkins_14d"] = [{"date": (since + timedelta(days=i)).isoformat(),
                            "count": counts.get(since + timedelta(days=i), 0)} for i in range(14)]

    # new members vs renewals, last 6 months (by bill date in the gym's time zone)
    months6 = _months_back(on, 6)
    jr = {k: {"month": k, "new": 0, "renewal": 0} for k in months6}
    first = date(int(months6[0][:4]), int(months6[0][5:]), 1)
    for kind, created in (Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE, member__in=members,
                                              created_at__date__gte=first - timedelta(days=1))
                          .values_list("kind", "created_at")):
        k = _month_key(timezone.localtime(created, tz).date())
        if k in jr:
            jr[k]["new" if kind == Bill.KIND_NEW else "renewal"] += 1
    out["joins_6m"] = list(jr.values())

    if has_perm(user, "billing", "view"):
        pays = Payment.objects.filter(bill__client=client, bill__status=Bill.STATUS_ACTIVE)
        d30 = on - timedelta(days=29)
        daily = dict(pays.filter(paid_on__gte=d30, paid_on__lte=on).values_list("paid_on").annotate(s=Sum("amount"))
                     .values_list("paid_on", "s"))
        out["collections_30d"] = [{"date": (d30 + timedelta(days=i)).isoformat(),
                                   "amount": str(R.money(daily.get(d30 + timedelta(days=i)) or 0))} for i in range(30)]
        months12 = _months_back(on, 12)
        m_first = date(int(months12[0][:4]), int(months12[0][5:]), 1)
        monthly = {k: R.ZERO for k in months12}
        for paid_on, amt in pays.filter(paid_on__gte=m_first, paid_on__lte=on).values_list("paid_on", "amount"):
            monthly[_month_key(paid_on)] += amt
        out["collections_12m"] = [{"month": k, "amount": str(R.money(v))} for k, v in monthly.items()]
        month_start = on.replace(day=1)
        modes = (pays.filter(paid_on__gte=month_start, paid_on__lte=on).values("mode").annotate(s=Sum("amount"))
                 .order_by("-s"))
        out["modes_this_month"] = [{"mode": x["mode"], "amount": str(R.money(x["s"]))} for x in modes]
        # same days last month, for "vs last month"
        prev_end = month_start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        same_day = min(on.day, prev_end.day)
        out["collected_this_month"] = str(R.money(pays.filter(paid_on__gte=month_start, paid_on__lte=on)
                                          .aggregate(s=Sum("amount"))["s"] or 0))
        out["collected_last_month_same_days"] = str(R.money(pays.filter(paid_on__gte=prev_start,
                                                                paid_on__lte=prev_start.replace(day=same_day))
                                                    .aggregate(s=Sum("amount"))["s"] or 0))
        dues, due_members = R.ZERO, set()
        for b in Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE).annotate(p=Sum("payments__amount")):
            r = R.remaining(b.total, b.p or 0)
            if r > 0:
                dues += r
                due_members.add(b.member_id)
        out["dues"] = {"amount": str(R.money(dues)), "members": len(due_members)}
        if has_perm(user, "expenses", "view"):
            from gym_ops.models import Expense
            exp = (Expense.objects.filter(client=client, is_deleted=False, date__gte=month_start, date__lte=on)
                   .aggregate(s=Sum("amount"))["s"] or R.ZERO)
            out["expenses_this_month"] = str(R.money(exp))

    if has_perm(user, "enquiries", "view"):
        from gym_ops.models import Enquiry
        month_start = on.replace(day=1)
        qs = Enquiry.objects.filter(client=client, is_archived=False, enquiry_date__gte=month_start, enquiry_date__lte=on)
        by = dict(qs.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
        out["enquiries_this_month"] = {"total": sum(by.values()), "by_status": by,
                                       "open_all": Enquiry.objects.filter(client=client, is_archived=False,
                                                                          status__in=Enquiry.OPEN).count()}
    return out
