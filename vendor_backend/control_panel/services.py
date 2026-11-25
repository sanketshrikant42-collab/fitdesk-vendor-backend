"""Read-only summaries for the Crestline control panel (/control/).

Everything here reads the tables the server already has (licensing,
gym_accounts, gym_core, cloud_backup, db_backup, gym_auto). Nothing in
this module writes; the views do the few writes, each with an AuditLog row.
"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Max, Q
from django.utils import timezone

from cloud_backup.models import CloudBackup
from gym_accounts.models import OwnerSetupCode, StaffDevice, StaffUser
from licensing.models import AuditLog, Client, Device, License, SupportQuery

IST = ZoneInfo("Asia/Kolkata")
SOON_DAYS = 30            # "ending soon" on the gyms list and Today
TODO_DAYS = 14            # licences this close to their end go on Today's to-do list
BACKUP_WARN_DAYS = 2      # no verified cloud backup for longer than this = warning


def today():
    return timezone.localdate(timezone=IST)


def wa_digits(phone):
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "91" + d if len(d) == 10 else d


def _max(*values):
    vals = [v for v in values if v]
    return max(vals) if vals else None


def gym_rows(q="", filt=""):
    """One summary dict per gym, newest first, for the Gyms list and Today."""
    t = today()
    clients = (Client.objects.all()
               .annotate(staff_n=Count("staff_users", filter=Q(staff_users__status=StaffUser.STATUS_ACTIVE), distinct=True),
                         members_n=Count("members", distinct=True),
                         devices_n=Count("licenses__devices", filter=Q(licenses__devices__status=Device.STATUS_ACTIVE), distinct=True),
                         open_support=Count("queries", filter=Q(queries__status=SupportQuery.STATUS_OPEN), distinct=True),
                         lic_seen=Max("licenses__last_check_in"),
                         dev_seen=Max("licenses__devices__last_seen_at"),
                         app_seen=Max("staff_devices__last_seen_at"),
                         last_backup=Max("cloud_backups__completed_at",
                                         filter=Q(cloud_backups__status=CloudBackup.STATUS_AVAILABLE))))
    if q:
        q = q.strip()
        clients = clients.filter(Q(gym_name__icontains=q) | Q(owner_name__icontains=q) | Q(phone__icontains=q)
                                 | Q(city__icontains=q) | Q(licenses__key__icontains=q)).distinct()
    rows = []
    for c in clients:
        lic = c.current_license
        st = lic.effective_status if lic else "no_licence"
        days = (lic.expiry_date - t).days if lic else None
        new_app = c.staff_n > 0
        old_app = c.devices_n > 0 or bool(c.lic_seen)
        r = {"c": c, "lic": lic, "status": st, "days_left": days,
             "app": "Both" if new_app and old_app else "New app" if new_app else "v1.2.0" if old_app else "Not set up",
             "new_app": new_app, "old_app": old_app,
             "members": c.members_n if new_app else None, "open_support": c.open_support,
             "last_seen": _max(c.lic_seen, c.dev_seen, c.app_seen), "last_backup": c.last_backup,
             "is_test": "test" in (c.gym_name + " " + c.notes).lower(),
             "wa": wa_digits(c.phone)}
        r["soon"] = st == "active" and days is not None and days <= SOON_DAYS
        r["badge"] = ("Test" if r["is_test"] and st == "active" else
                      {"active": "Ending soon" if r["soon"] else "Active", "expired": "Expired", "suspended": "Suspended",
                       "revoked": "Revoked", "no_licence": "No licence"}[st])
        r["badge_cls"] = {"Active": "ok", "Test": "info", "Ending soon": "warn"}.get(r["badge"], "bad")
        rows.append(r)
    if filt == "soon":
        rows = [r for r in rows if r["soon"]]
    elif filt == "problem":
        rows = [r for r in rows if r["status"] != "active"]
    elif filt == "old":
        rows = [r for r in rows if r["old_app"]]
    elif filt == "new":
        rows = [r for r in rows if r["new_app"]]
    return rows


def backup_late(row, now=None):
    """A gym on the desktop app whose last verified cloud backup is older
    than BACKUP_WARN_DAYS (or that has never sent one)."""
    if not row["old_app"] or row["status"] != "active" or row["is_test"]:
        return False
    now = now or timezone.now()
    return row["last_backup"] is None or now - row["last_backup"] > timedelta(days=BACKUP_WARN_DAYS)


def today_summary():
    rows = gym_rows()
    real = [r for r in rows if not r["is_test"]]
    active = [r for r in real if r["status"] == "active"]
    todo = []
    for r in rows:
        if r["is_test"]:
            continue
        if r["status"] == "active" and r["days_left"] is not None and r["days_left"] <= TODO_DAYS:
            todo.append({"kind": "renew", "row": r, "sort": r["days_left"]})
        elif r["status"] == "expired" and r["days_left"] is not None and r["days_left"] >= -30:
            todo.append({"kind": "expired", "row": r, "sort": r["days_left"]})
        if backup_late(r):
            todo.append({"kind": "backup", "row": r, "sort": 50})
    for sq in SupportQuery.objects.filter(status=SupportQuery.STATUS_OPEN).select_related("client")[:20]:
        hours = int((timezone.now() - sq.created_at).total_seconds() // 3600)
        todo.append({"kind": "support", "q": sq, "hours": hours, "sort": 20 - min(hours, 19)})
    for d in StaffDevice.objects.filter(status=StaffDevice.STATUS_PENDING).select_related("client", "registered_by")[:20]:
        todo.append({"kind": "device", "d": d, "sort": 30})
    todo.sort(key=lambda x: x["sort"])
    from db_backup.models import ServerBackup
    from gym_auto.models import DailyRun
    last_run = DailyRun.objects.order_by("-ran_at").first()
    last_srv = ServerBackup.objects.order_by("-started_at").first()
    return {
        "gyms_active": len(active),
        "gyms_new_app": sum(1 for r in active if r["new_app"]),
        "gyms_old_app": sum(1 for r in active if r["old_app"] and not r["new_app"]),
        "ending_soon": sum(1 for r in active if r["soon"]),
        "open_support": SupportQuery.objects.filter(status=SupportQuery.STATUS_OPEN).count(),
        "support_late": SupportQuery.objects.filter(status=SupportQuery.STATUS_OPEN,
                                                   created_at__lt=timezone.now() - timedelta(hours=24)).count(),
        "backup_warn": sum(1 for r in rows if backup_late(r)),
        "todo": todo,
        "last_run": last_run, "last_srv": last_srv,
        "activity": activity(AuditLog.objects.select_related("client").order_by("-created_at")[:8]),
    }


def gym_detail(client):
    lic = client.current_license
    t = today()
    return {
        "lic": lic,
        "days_left": (lic.expiry_date - t).days if lic else None,
        "devices": list(lic.devices.order_by("-last_seen_at")) if lic else [],
        "users": list(client.staff_users.order_by("role", "name")),
        "app_devices": list(client.staff_devices.exclude(status=StaffDevice.STATUS_REMOVED).select_related("registered_by").order_by("-last_seen_at")),
        "setup_codes": list(OwnerSetupCode.objects.filter(client=client).order_by("-created_at")[:3]),
        "backups": list(CloudBackup.objects.filter(client=client).order_by("-created_at")[:3]),
        "support": list(client.queries.order_by("-created_at")[:5]),
        "activity": activity(AuditLog.objects.filter(client=client).order_by("-created_at")[:10]),
        "features": lic.resolved_features if lic else [],
    }


def renew_to(lic, months):
    """New end date: early renewal never loses remaining days."""
    start = max(lic.expiry_date, today())
    m = start.month - 1 + months
    y, m = start.year + m // 12, m % 12 + 1
    import calendar
    d = min(start.day, calendar.monthrange(y, m)[1])
    return date(y, m, d)


ACTION_LABELS = {
    "gym_created": "Gym added",
    "licence_renewed": "Licence renewed",
    "licence_status_changed": "Licence status changed",
    "licence_settings_changed": "Plan / PC limit changed",
    "gym_details_changed": "Gym details edited",
    "owner_setup_code_issued": "New owner setup code",
    "device_activated": "PC allowed",
    "device_deactivated": "PC blocked",
    "plan_changed": "Plan changed",
    "feature_override_changed": "Feature changed",
}


def activity(entries):
    out = []
    for a in entries:
        m = a.metadata or {}
        label = ACTION_LABELS.get(a.action, a.action.replace("_", " ").capitalize())
        extra = ""
        if a.action == "licence_renewed" and m.get("new_expiry"):
            extra = f"{m.get('months')} months, to {date.fromisoformat(m['new_expiry']):%d %b %Y}"
        elif a.action == "licence_status_changed":
            extra = f"now {m.get('new')}"
        elif a.action == "gym_created" and m.get("plan"):
            extra = f"{m.get('plan')} · {m.get('months')} months"
        elif m.get("device"):
            extra = m["device"]
        out.append({"label": label, "extra": extra, "gym": a.client.gym_name if a.client_id else "",
                    "client_id": a.client_id, "at": a.created_at, "by_panel": m.get("by") == "control_panel"})
    return out


# --- money (Phase 2) -------------------------------------------------------------------------

def fy_start(d=None):
    d = d or today()
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def fy_label(d):
    s = fy_start(d)
    return f"{s.year}-{str(s.year + 1)[2:]}"


def next_invoice_no(paid_on):
    from .models import VendorPayment
    prefix = f"CT/{fy_label(paid_on)}/"
    nums = [int(n.rsplit("/", 1)[1]) for n in VendorPayment.objects.filter(invoice_no__startswith=prefix)
            .values_list("invoice_no", flat=True) if n.rsplit("/", 1)[1].isdigit()]
    return f"{prefix}{(max(nums) if nums else 0) + 1:04d}"


def record_payment(client, amount, mode, paid_on, months=0, reference="", note="", by=""):
    """Saves a gym's payment to Crestline and, when months > 0, extends the
    licence (from its end date, or from today if it has ended)."""
    from django.db import IntegrityError, transaction
    from .models import VendorPayment
    lic = client.current_license
    for attempt in range(3):
        try:
            with transaction.atomic():
                p = VendorPayment(client=client, license=lic, paid_on=paid_on, amount=amount, mode=mode,
                                  reference=reference[:60], note=note[:200], months=months, created_by=by[:150],
                                  plan_name=lic.plan.name if lic and lic.plan_id else "",
                                  invoice_no=next_invoice_no(paid_on))
                if lic and months:
                    old = lic.expiry_date
                    p.period_from = max(old, today())
                    lic.expiry_date = renew_to(lic, months)
                    p.period_to = lic.expiry_date
                    lic.save(update_fields=["expiry_date"])
                    AuditLog.record("licence_renewed", license=lic, by="control_panel", months=months,
                                    old_expiry=old.isoformat(), new_expiry=lic.expiry_date.isoformat(), payment=p.invoice_no)
                p.save()
                AuditLog.record("payment_recorded", license=lic, client=client, by="control_panel",
                                invoice=p.invoice_no, amount=str(amount), mode=mode)
                return p
        except IntegrityError:
            if attempt == 2:
                raise
    return None


def _months_back(n):
    t = today()
    out = []
    y, m = t.year, t.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def money_summary():
    from django.db.models import Sum
    from .models import GymBilling, LAUNCH_OFFER_SEATS, VendorPayment
    t = today()
    pays = VendorPayment.objects.all()
    month_sum = pays.filter(paid_on__year=t.year, paid_on__month=t.month).aggregate(s=Sum("amount"))["s"] or 0
    month_n = pays.filter(paid_on__year=t.year, paid_on__month=t.month).count()
    fy_sum = pays.filter(paid_on__gte=fy_start(t)).aggregate(s=Sum("amount"))["s"] or 0
    prices = {b.client_id: b for b in GymBilling.objects.all()}
    last_rem = {}
    for a in AuditLog.objects.filter(action="renewal_reminder_sent").order_by("created_at"):
        last_rem[a.client_id] = a.created_at
    renewals, due = [], 0
    for r in gym_rows():
        if r["is_test"] or r["status"] in ("suspended", "revoked", "no_licence") or r["days_left"] is None:
            continue
        if -60 <= r["days_left"] <= 45:
            b = prices.get(r["c"].id)
            r["price"] = b.yearly_price if b else None
            r["reminded"] = last_rem.get(r["c"].id)
            renewals.append(r)
            if r["days_left"] <= SOON_DAYS and r["price"]:
                due += r["price"]
    renewals.sort(key=lambda r: r["days_left"])
    by_month = []
    for y, m in _months_back(12):
        s = pays.filter(paid_on__year=y, paid_on__month=m).aggregate(s=Sum("amount"))["s"] or 0
        by_month.append({"label": date(y, m, 1).strftime("%b %y"), "amount": s})
    top = max([x["amount"] for x in by_month] + [1])
    for x in by_month:
        x["pct"] = int(x["amount"] * 100 / top)
    return {"month_sum": month_sum, "month_n": month_n, "fy_sum": fy_sum, "fy": fy_label(t),
            "due": due, "due_n": sum(1 for r in renewals if 0 <= r["days_left"] <= SOON_DAYS or r["days_left"] < 0),
            "launch_used": GymBilling.objects.filter(launch_offer=True).count(), "launch_total": LAUNCH_OFFER_SEATS,
            "renewals": renewals, "payments": list(pays.select_related("client")[:60]), "by_month": by_month}


def renewal_text(client, lic, price=None):
    ends = lic.expiry_date
    t = today()
    when = (f"ends on {ends:%d %b %Y}" if ends >= t else f"ended on {ends:%d %b %Y}")
    lines = [f"Hello {client.owner_name},", "",
             f"Your FitDesk Pro licence for *{client.gym_name}* {when}."]
    if price:
        lines.append(f"Renewal for 1 year: *{format_rupees(price)}*.")
    lines += ["", "Please renew so your gym software keeps working without a break. Reply here or call 84840 74959.",
              "", "– Crestline Technologies"]
    return "\n".join(lines)


def format_rupees(v):
    from .invoice import rupees
    return rupees(v)


# --- support (Phase 3) -------------------------------------------------------------------------

def support_lists(tab):
    from .models import SupportMeta
    qs = SupportQuery.objects.select_related("client").order_by("-created_at")
    metas = {m.query_id: m for m in SupportMeta.objects.all()}
    rows = []
    for q in qs[:300]:
        m = metas.get(q.id)
        cb = bool(m and m.call_back)
        bucket = "done" if q.status == SupportQuery.STATUS_RESOLVED else ("call" if cb else "open")
        rows.append({"q": q, "m": m, "bucket": bucket, "hours": int((timezone.now() - q.created_at).total_seconds() // 3600)})
    counts = {k: sum(1 for r in rows if r["bucket"] == k) for k in ("open", "call", "done")}
    return [r for r in rows if r["bucket"] == tab], counts


# --- health (Phase 3) --------------------------------------------------------------------------

def health_summary():
    from db_backup.models import ServerBackup
    from gym_auto.mailer import ready as email_ready
    from gym_auto.models import DailyRun, EmailSettings
    from licensing.models import WhatsAppConfiguration
    now = timezone.now()
    last_run = DailyRun.objects.order_by("-ran_at").first()
    last_srv = ServerBackup.objects.order_by("-started_at").first()
    last_ok_srv = ServerBackup.objects.filter(status=ServerBackup.STATUS_OK).order_by("-started_at").first()
    rows = gym_rows()
    wa = {w.client_id: w for w in WhatsAppConfiguration.objects.all()}
    em = {e.client_id: e for e in EmailSettings.objects.all()}
    latest_backup = {}
    for b in CloudBackup.objects.order_by("created_at"):
        latest_backup[b.client_id] = b
    for r in rows:
        w = wa.get(r["c"].id)
        r["wa"] = "Automatic (Meta)" if w and w.enabled and w.connection_status == "CONNECTED" else (
            "Meta error" if w and w.connection_status == "ERROR" else "Links")
        e = em.get(r["c"].id)
        try:
            r["email"] = "Set up" if e and email_ready(e) else "Not set"
        except Exception:                      # noqa: BLE001 - never break the page over one gym's settings
            r["email"] = "Not set"
        r["backup_obj"] = latest_backup.get(r["c"].id)
        r["late"] = backup_late(r, now)
    week = now - timedelta(days=7)
    problems = []
    for b in CloudBackup.objects.filter(created_at__gte=week, status__in=[CloudBackup.STATUS_FAILED, CloudBackup.STATUS_CORRUPT]).select_related("client"):
        problems.append({"at": b.created_at, "text": f"{b.client.gym_name}: cloud backup {b.get_status_display().lower()}"})
    for s in ServerBackup.objects.filter(started_at__gte=week, status=ServerBackup.STATUS_FAILED):
        problems.append({"at": s.started_at, "text": f"Server backup failed{(': ' + s.error[:120]) if getattr(s, 'error', '') else ''}"})
    run_days = set(DailyRun.objects.filter(ran_at__gte=week).values_list("date", flat=True))
    first = DailyRun.objects.order_by("date").values_list("date", flat=True).first()
    for i in range(1, 7):
        d = today() - timedelta(days=i)
        if first and d > first and d not in run_days:          # only days after the job first ran
            problems.append({"at": timezone.make_aware(timezone.datetime(d.year, d.month, d.day, 6, 0), IST),
                             "text": f"Nightly job didn't run on {d:%d %b}"})
    problems.sort(key=lambda p: p["at"], reverse=True)
    fresh = timedelta(hours=26)
    return {
        "job_ok": bool(last_run and now - last_run.ran_at < fresh), "last_run": last_run,
        "srv_ok": bool(last_srv and last_srv.status == ServerBackup.STATUS_OK and now - last_srv.started_at < fresh),
        "last_srv": last_srv, "last_ok_srv": last_ok_srv,
        "late_n": sum(1 for r in rows if r["late"]), "rows": rows, "problems": problems,
    }


def warnings_count():
    """For the menu badge on Health & backups."""
    h = health_summary()
    return h["late_n"] + (0 if h["job_ok"] else 1) + (0 if h["srv_ok"] else 1)
