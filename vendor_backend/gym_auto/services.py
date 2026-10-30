"""Phase 3C: the morning job (build the day's "to send" list), the owner's
summary, and actions on list items.

The job is safe to run any number of times: each reminder has a key
(e.g. "renewal:<membership>:7"), so a second run adds only what's new.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from gym_accounts.api import ApiError
from gym_accounts.models import GymProfile, StaffUser
from gym_accounts.permissions import has_perm
from gym_core import rules as R
from gym_core import services as C
from gym_core.models import Bill, CheckIn, Freeze, GymSettings, Member, Membership, Payment

from . import texts as TX
from .models import DailyRun, Reminder, ReminderSettings


# --- helpers -----------------------------------------------------------------------------------

def gym_tz(client):
    return ZoneInfo(GymProfile.for_client(client).time_zone or "Asia/Kolkata")


def local_date(dt, tz):
    return dt.astimezone(tz).date()


def fmt_date(d):
    return d.strftime("%d %b %Y").lstrip("0") if d else ""


def first_name(name):
    return (name or "").strip().split(" ")[0] or name


def owner_phone(client, rs):
    if rs.owner_summary_phone:
        return rs.owner_summary_phone
    owner = StaffUser.objects.filter(client=client, role="owner").order_by("id").first()
    return owner.phone if owner else ""


def renewal_amount(plan, gs):
    """What renewing the same plan would cost today (plan discount, plus GST
    if the gym adds GST by default). Shown in the reminder."""
    disc = R.money(plan.fee * plan.discount_percent / 100)
    return R.bill_totals(plan.fee, disc, gs.apply_gst_by_default, gs.gst_percent)["total"]


def _ctx(gs, member, **kw):
    ctx = {"name": first_name(member.name) if member else "", "gym": gs.gym_name, "upi_id": gs.upi_id or ""}
    ctx.update(kw)
    return ctx


def _add(client, created, kind, key, on, member, phone, text, amount=None, upi="", days=None, meta=None):
    try:
        with transaction.atomic():
            obj, made = Reminder.objects.get_or_create(
                client=client, key=key,
                defaults={"kind": kind, "for_date": on, "member": member, "phone": phone or "", "text": text,
                          "amount": amount, "upi_link": upi or "", "days": days, "meta": meta or {}})
    except IntegrityError:          # another device ran the job at the same moment
        return
    if made:
        created[kind] += 1


# --- the morning job ---------------------------------------------------------------------------

def visits_tracked_since(client):
    """A gym brought over from FitDesk v1.2.0 has no visit history (v1.2.0
    didn't record member check-ins), so "days not seen" counts from the
    day of the import, not from when each membership started."""
    from django.apps import apps
    if not apps.is_installed("gym_import"):
        return None
    from gym_import.models import ImportRun
    run = ImportRun.objects.filter(client=client, ok=True).order_by("created_at").first()
    return timezone.localtime(run.created_at, ZoneInfo(GymProfile.for_client(client).time_zone or "Asia/Kolkata")).date() if run else None


def generate(client, on=None):
    """Build the "to send" list for `on` (default: today in the gym's time
    zone). Returns {kind: number created}."""
    on = on or C.today_for(client)
    gs = GymSettings.for_client(client)
    rs = ReminderSettings.for_client(client)
    created = defaultdict(int)
    members = Member.objects.filter(client=client, is_archived=False)
    payee = gs.upi_payee_name or gs.gym_name

    active_ms = Membership.objects.filter(client=client, status=Membership.STATUS_ACTIVE, member__is_archived=False)
    latest = {r["member"]: r["end"] for r in active_ms.values("member").annotate(end=Max("end_date"))}
    frozen_today = set(Freeze.objects.filter(membership__client=client, cancelled_at__isnull=True,
                                             start_date__lte=on, end_date__gte=on).values_list("membership__member", flat=True))

    # 1) Renewal (before / on the last day) and expired (after it) ---------------------------
    if rs.renewal_on:
        offsets = rs.offsets()
        wanted = {}
        for mid, end in latest.items():
            off = (end - on).days
            if off in offsets and mid not in frozen_today:
                wanted[mid] = (end, off)
        if wanted:
            ms_by_member = {}
            for m in active_ms.filter(member_id__in=wanted).select_related("plan", "member").order_by("end_date"):
                ms_by_member[m.member_id] = m           # keeps the one with the latest end date
            for mid, (end, off) in wanted.items():
                m = ms_by_member[mid]
                amount = renewal_amount(m.plan, gs)
                amt_txt = R.rupees(amount)
                upi = R.upi_link(gs.upi_id, payee, amount, f"Renewal {m.member.code}")
                common = {"plan": m.plan.name, "end_date": fmt_date(end), "amount": amt_txt,
                          "pay_line": TX.pay_line(amt_txt, gs.upi_id, upi)}
                if off >= 0:
                    text = R.render_template(rs.tpl_renewal or TX.RENEWAL,
                                             _ctx(gs, m.member, days=off, when=TX.when_text(off), **common))
                    _add(client, created, Reminder.KIND_RENEWAL, f"renewal:{m.id}:{off}", on, m.member, m.member.phone,
                         text, amount, upi, off, {"plan": m.plan.name, "end_date": end.isoformat()})
                else:
                    text = R.render_template(rs.tpl_expired or TX.EXPIRED,
                                             _ctx(gs, m.member, days=-off, when="", **common))
                    _add(client, created, Reminder.KIND_EXPIRED, f"expired:{m.id}:{-off}", on, m.member, m.member.phone,
                         text, amount, upi, -off, {"plan": m.plan.name, "end_date": end.isoformat()})

    # 2) Dues ------------------------------------------------------------------------------------
    if rs.dues_on:
        dues = defaultdict(Decimal)
        for b in Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE, member__is_archived=False).annotate(p=Sum("payments__amount")):
            left = R.remaining(b.total, b.p or 0)
            if left > 0:
                dues[b.member_id] += left
        if dues:
            since = on - timedelta(days=max(rs.dues_every_days, 1) - 1)
            recent = set(Reminder.objects.filter(client=client, kind=Reminder.KIND_DUES, for_date__gte=since,
                                                 for_date__lte=on).values_list("member_id", flat=True))
            for m in members.filter(pk__in=[k for k in dues if k not in recent]):
                amount = R.money(dues[m.id])
                amt_txt = R.rupees(amount)
                upi = R.upi_link(gs.upi_id, payee, amount, f"Dues {m.code}")
                text = R.render_template(rs.tpl_dues or TX.DUES, _ctx(gs, m, amount=amt_txt, plan="", end_date="",
                                                                       days="", when="", pay_line=TX.pay_line(amt_txt, gs.upi_id, upi)))
                _add(client, created, Reminder.KIND_DUES, f"dues:{m.id}:{on.isoformat()}", on, m, m.phone, text, amount, upi)

    # 3) Birthdays -------------------------------------------------------------------------------
    if rs.birthday_on:
        q = Q(date_of_birth__month=on.month, date_of_birth__day=on.day)
        if on.month == 2 and on.day == 28 and not _leap(on.year):
            q |= Q(date_of_birth__month=2, date_of_birth__day=29)          # 29 Feb birthdays in other years
        for m in members.filter(q):
            text = R.render_template(rs.tpl_birthday or TX.BIRTHDAY, _ctx(gs, m, plan="", end_date="", days="", when="",
                                                                           amount="", pay_line=""))
            _add(client, created, Reminder.KIND_BIRTHDAY, f"birthday:{m.id}:{on.year}", on, m, m.phone, text)

    # 4) Inactive members (call list) ------------------------------------------------------------
    if rs.inactive_on and rs.inactive_after_days > 0:
        n = rs.inactive_after_days
        current = {}
        for m in active_ms.filter(start_date__lte=on, end_date__gte=on).select_related("member", "plan"):
            if m.member_id in frozen_today:
                continue
            prev = current.get(m.member_id)
            if prev is None or m.start_date < prev.start_date:
                current[m.member_id] = m                # earliest running start: when they could first have come
        if current:
            last = dict(CheckIn.objects.filter(client=client, member_id__in=current).values("member")
                        .annotate(d=Max("local_date")).values_list("member", "d"))
            since = on - timedelta(days=n - 1)
            recent = set(Reminder.objects.filter(client=client, kind=Reminder.KIND_INACTIVE, for_date__gte=since,
                                                 for_date__lte=on).values_list("member_id", flat=True))
            tracked = visits_tracked_since(client)
            for mid, m in current.items():
                base = max(filter(None, [last.get(mid), m.start_date, tracked]))
                away = (on - base).days
                if away >= n and mid not in recent:
                    end = latest.get(mid, m.end_date)
                    text = R.render_template(rs.tpl_inactive or TX.INACTIVE,
                                             _ctx(gs, m.member, days=away, plan=m.plan.name, end_date=fmt_date(end),
                                                  when="", amount="", pay_line=""))
                    _add(client, created, Reminder.KIND_INACTIVE, f"inactive:{mid}:{on.isoformat()}", on, m.member,
                         m.member.phone, text, None, "", away,
                         {"last_visit": last[mid].isoformat() if last.get(mid) else None, "plan": m.plan.name,
                          "end_date": end.isoformat()})

    # 5) Owner's summary of yesterday --------------------------------------------------------------
    if rs.owner_summary_on:
        day = on - timedelta(days=1)
        s = summary(client, day)
        _add(client, created, Reminder.KIND_SUMMARY, f"summary:{day.isoformat()}", on, None, owner_phone(client, rs),
             s["text"], None, "", None, {"summary_date": day.isoformat()})

    with transaction.atomic():
        run, _ = DailyRun.objects.get_or_create(client=client, date=on)
        total = dict(run.created or {})
        for k, v in created.items():
            total[k] = total.get(k, 0) + v
        run.created = total
        run.save()
    return dict(created)


def ensure_today(client):
    """The list builds itself the first time anyone opens it each day, so
    it works even before the scheduled job is set up on Render."""
    on = C.today_for(client)
    if not DailyRun.objects.filter(client=client, date=on).exists():
        generate(client, on)
    return on


def _leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


# --- owner's summary ---------------------------------------------------------------------------

def summary(client, day):
    """Numbers for one day, plus a WhatsApp-ready text."""
    gs = GymSettings.for_client(client)
    tz = gym_tz(client)
    pays = Payment.objects.filter(bill__client=client, bill__status=Bill.STATUS_ACTIVE)
    today_pays = pays.filter(paid_on=day)
    by_mode = {r["mode"]: R.money(r["s"]) for r in today_pays.values("mode").annotate(s=Sum("amount")).order_by("-s")}
    collected = R.money(today_pays.aggregate(s=Sum("amount"))["s"] or 0)
    month = R.money(pays.filter(paid_on__gte=day.replace(day=1), paid_on__lte=day).aggregate(s=Sum("amount"))["s"] or 0)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    bills = Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE, created_at__gte=start, created_at__lt=end)
    new_bills, renewals = bills.filter(kind=Bill.KIND_NEW).count(), bills.filter(kind=Bill.KIND_RENEWAL).count()
    new_members = Member.objects.filter(client=client, created_at__gte=start, created_at__lt=end).count()
    checkins = CheckIn.objects.filter(client=client, local_date=day).count()
    active_ms = Membership.objects.filter(client=client, status=Membership.STATUS_ACTIVE, member__is_archived=False)
    latest = active_ms.values("member").annotate(end=Max("end_date"))
    expiring = sum(1 for r in latest if day < r["end"] <= day + timedelta(days=7))
    expired_today = sum(1 for r in latest if r["end"] == day)
    dues = R.ZERO
    for b in Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE).annotate(p=Sum("payments__amount")):
        dues += R.remaining(b.total, b.p or 0)
    out = {"date": day.isoformat(), "collected": str(collected), "by_mode": {k: str(v) for k, v in by_mode.items()},
           "month_to_date": str(month), "new_joins": new_bills, "renewals": renewals, "new_members": new_members,
           "checkins": checkins, "expiring_7_days": expiring, "ended_today": expired_today, "pending_dues": str(R.money(dues))}
    try:                                      # Phase 3B numbers (expenses, enquiries)
        from gym_ops.models import Enquiry, Expense
        out["expenses"] = str(R.money(Expense.objects.filter(client=client, is_deleted=False, date=day)
                                      .aggregate(s=Sum("amount"))["s"] or 0))
        out["enquiries_new"] = Enquiry.objects.filter(client=client, enquiry_date=day).count()
        out["enquiries_joined"] = Enquiry.objects.filter(client=client, status="Converted", converted_at__gte=start,
                                                         converted_at__lt=end).count()
    except Exception:
        out.update({"expenses": "0.00", "enquiries_new": 0, "enquiries_joined": 0})
    modes = " · ".join(f"{k} {R.rupees(v)}" for k, v in by_mode.items())
    lines = [f"*{gs.gym_name} · daily summary*", day.strftime("%A, %d %b %Y"), "",
             f"💰 Collected: *{R.rupees(collected)}*" + (f"\n    ({modes})" if modes else ""),
             f"📅 This month so far: {R.rupees(month)}",
             f"🧾 Bills: {new_bills} new join{'s' if new_bills != 1 else ''}, {renewals} renewal{'s' if renewals != 1 else ''}",
             f"👥 New members added: {new_members}",
             f"✅ Check-ins: {checkins}",
             f"💸 Expenses: {R.rupees(out['expenses'])}",
             f"📞 Enquiries: {out['enquiries_new']} new, {out['enquiries_joined']} joined",
             f"⏳ Ending in the next 7 days: {expiring}" + (f" (ended today: {expired_today})" if expired_today else ""),
             f"❗ Pending dues: {R.rupees(dues)}",
             "", "_FitDesk Pro_"]
    out["text"] = "\n".join(lines)
    return out


# --- list items ----------------------------------------------------------------------------------

KIND_LABEL = {"renewal": "Renewal", "expired": "Expired", "dues": "Dues", "birthday": "Birthday",
              "inactive": "Not visiting", "owner_summary": "Owner summary"}


def can_see_summary(user):
    return has_perm(user, "reports", "view") and has_perm(user, "billing", "view")


def reminder_json(r):
    m = r.member
    return {"id": r.id, "kind": r.kind, "kind_label": KIND_LABEL.get(r.kind, r.kind), "for_date": r.for_date.isoformat(),
            "member": {"id": str(m.id), "name": m.name, "code": m.code, "phone": m.phone, "email": m.email} if m else None,
            "phone": r.phone, "text": r.text, "amount": str(r.amount) if r.amount is not None else None,
            "upi_link": r.upi_link, "days": r.days, "meta": r.meta, "status": r.status, "channel": r.channel,
            "whatsapp_link": R.whatsapp_link(r.phone, r.text) if r.phone else "",
            "sent_at": r.sent_at.isoformat() if r.sent_at else None, "sent_by": r.sent_by.name if r.sent_by_id else None,
            "call_outcome": r.call_outcome, "notes": r.notes}


def act(request, r, action, data):
    if action == "sent":
        r.status, r.sent_at, r.sent_by, r.sent_device = Reminder.SENT, timezone.now(), request.staff, request.device
    elif action == "skip":
        r.status = Reminder.SKIPPED
        r.notes = str(data.get("notes") or r.notes)[:300]
    elif action == "reopen":
        r.status, r.sent_at, r.sent_by, r.call_outcome = Reminder.PENDING, None, None, ""
    elif action == "call":
        outcome = str(data.get("outcome") or "")
        if outcome not in Reminder.CALL_OUTCOMES:
            raise ApiError(400, "bad_outcome", f"Outcome must be one of: {', '.join(Reminder.CALL_OUTCOMES)}.")
        r.status, r.call_outcome = Reminder.DONE, outcome
        r.notes = str(data.get("notes") or "")[:300]
        r.sent_at, r.sent_by, r.sent_device = timezone.now(), request.staff, request.device
    else:
        raise ApiError(404, "not_found", "Unknown action.")
    r.save()
    C.log(request, f"reminder_{action}", f"{KIND_LABEL.get(r.kind, r.kind)} · {r.member.name if r.member_id else r.phone}")
    return r


# --- settings ------------------------------------------------------------------------------------

SETTINGS_FIELDS = ["renewal_on", "renewal_days", "dues_on", "dues_every_days", "birthday_on", "inactive_on",
                   "inactive_after_days", "owner_summary_on", "owner_summary_phone",
                   "tpl_renewal", "tpl_expired", "tpl_dues", "tpl_birthday", "tpl_inactive"]


def settings_json(rs):
    out = {f: getattr(rs, f) for f in SETTINGS_FIELDS}
    out["defaults"] = TX.DEFAULTS
    out["placeholders"] = TX.PLACEHOLDERS
    out["owner_summary_phone_effective"] = owner_phone(rs.client, rs)
    return out


def update_settings(request, data):
    rs = ReminderSettings.for_client(request.client)
    for f in ("renewal_on", "dues_on", "birthday_on", "inactive_on", "owner_summary_on"):
        if f in data:
            setattr(rs, f, bool(data[f]))
    if "renewal_days" in data:
        parts = [p for p in str(data["renewal_days"] or "").replace(" ", "").split(",") if p]
        try:
            nums = sorted({int(p) for p in parts}, reverse=True)
        except ValueError:
            raise ApiError(400, "bad_renewal_days", "Renewal days must be numbers separated by commas, e.g. 7,3,1,0,-3.")
        if any(n > 60 or n < -60 for n in nums):
            raise ApiError(400, "bad_renewal_days", "Use days between -60 and 60.")
        rs.renewal_days = ",".join(str(n) for n in nums)
    for f, lo, hi in (("dues_every_days", 1, 60), ("inactive_after_days", 1, 90)):
        if f in data:
            try:
                v = int(data[f])
            except (TypeError, ValueError):
                raise ApiError(400, f"bad_{f}", f"{f.replace('_', ' ').capitalize()} must be a number.")
            setattr(rs, f, max(lo, min(hi, v)))
    if "owner_summary_phone" in data:
        rs.owner_summary_phone = C.clean_phone(data["owner_summary_phone"]) if data["owner_summary_phone"] else ""
    for f in ("tpl_renewal", "tpl_expired", "tpl_dues", "tpl_birthday", "tpl_inactive"):
        if f in data:
            v = str(data[f] or "").strip()
            setattr(rs, f, "" if v == TX.DEFAULTS[f[4:]].strip() else v[:2000])
    rs.save()
    C.log(request, "reminder_settings_changed", "Reminders")
    return rs
