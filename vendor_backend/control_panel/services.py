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
