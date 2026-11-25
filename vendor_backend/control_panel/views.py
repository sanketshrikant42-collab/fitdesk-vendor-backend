"""Crestline control panel: the vendor's own screens at /control/.

Only a Django superuser can sign in. Every change made here is written to
licensing.AuditLog with by="control_panel" so it shows in Recent activity
and in each gym's history. The gym apps and the desktop licence check are
not touched by anything in this module.
"""
import os
import urllib.parse
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from gym_accounts.models import GymProfile, OwnerSetupCode
from licensing.models import AuditLog, Client, Device, License, Plan

from . import services as S

LOGIN_TRIES = 5            # wrong passwords per address ...
LOGIN_WINDOW = 15 * 60     # ... within this many seconds, then wait
RENEW_MONTHS = [1, 3, 6, 12, 24]


def owner_required(view):
    @wraps(view)
    def inner(request, *a, **kw):
        u = request.user
        if not (u.is_authenticated and u.is_active and u.is_superuser):
            return redirect(f"/control/login/?next={urllib.parse.quote(request.path)}")
        timezone.activate(S.IST)            # every date and time on these pages in India time
        try:
            return view(request, *a, **kw)
        finally:
            timezone.deactivate()
    return inner


def _ip(request):
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", ""))


def app_address():
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/") or "https://fitdesk-vendor-backend.onrender.com"
    return base + "/app/"


def wa_link(phone, text):
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={urllib.parse.quote(text)}"


def handover_text(client, key, code=None):
    lines = [f"Hello {client.owner_name},", "",
             f"Welcome to FitDesk Pro! Here are the details for *{client.gym_name}*:", "",
             f"Licence key: *{key}*"]
    if code:
        lines += [f"Owner setup code: *{code}* (one time, valid 7 days)", "",
                  f"1. Open {app_address()}",
                  "2. Tap \"Set up your gym\"",
                  "3. Enter the licence key, the setup code, your mobile number and a new password."]
    lines += ["", "Need help? Call 84840 74959 · www.crestlinetechnologies.in", "– Crestline Technologies"]
    return "\n".join(lines)


def _reveal(request, **data):
    """Show a secret (setup code) exactly once, on the next page."""
    request.session["cp_reveal"] = data


def _pop_reveal(request):
    return request.session.pop("cp_reveal", None)


def _audit(action, client=None, license=None, request=None, **meta):
    AuditLog.record(action, license=license, client=client, by="control_panel",
                    user=getattr(getattr(request, "user", None), "username", ""), **meta)


# --- sign in ---------------------------------------------------------------------------------

def login_view(request):
    nxt = request.GET.get("next") or request.POST.get("next") or "/control/"
    if not nxt.startswith("/control/"):
        nxt = "/control/"
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect(nxt)
    err = ""
    if request.method == "POST":
        k = "cp-login:" + _ip(request)
        tries = cache.get(k, 0)
        if tries >= LOGIN_TRIES:
            err = "Too many wrong tries. Wait 15 minutes and try again."
        else:
            u = authenticate(request, username=request.POST.get("username", "").strip(),
                             password=request.POST.get("password", ""))
            if u is not None and u.is_active and u.is_superuser:
                cache.delete(k)
                login(request, u)
                request.session.set_expiry(12 * 3600)
                return redirect(nxt)
            cache.set(k, tries + 1, LOGIN_WINDOW)
            err = "Username or password is wrong."
    return render(request, "control_panel/login.html", {"err": err, "next": nxt})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("/control/login/")


# --- pages -----------------------------------------------------------------------------------

def _nav(request):
    t = S.today_summary()
    return t


@owner_required
def today_view(request):
    s = S.today_summary()
    return render(request, "control_panel/today.html", {"s": s, "nav": "today", "badges": s,
                                                         "hour": timezone.localtime(timezone=S.IST).hour})


@owner_required
def gyms_view(request, client_id=None):
    q, filt = request.GET.get("q", ""), request.GET.get("f", "")
    rows = S.gym_rows(q, filt)
    ctx = {"rows": rows, "q": q, "f": filt, "nav": "gyms", "badges": S.today_summary(),
           "renew_months": RENEW_MONTHS, "plans": Plan.objects.filter(status=Plan.STATUS_ACTIVE)}
    if client_id is not None:
        c = get_object_or_404(Client, pk=client_id)
        ctx.update({"sel": c, "d": S.gym_detail(c), "reveal": _pop_reveal(request)})
        if ctx["d"]["lic"]:
            ctx["wa_key"] = wa_link(c.phone, handover_text(c, ctx["d"]["lic"].key, (ctx["reveal"] or {}).get("code")))
    return render(request, "control_panel/gyms.html", ctx)


def _gym_and_licence(client_id):
    c = get_object_or_404(Client, pk=client_id)
    lic = c.current_license
    if lic is None:
        raise Http404("This gym has no licence.")
    return c, lic


@owner_required
@require_POST
def gym_renew(request, client_id):
    c, lic = _gym_and_licence(client_id)
    try:
        months = int(request.POST.get("months", "0"))
    except ValueError:
        months = 0
    if months not in RENEW_MONTHS:
        messages.error(request, "Pick how long to renew for.")
        return redirect(f"/control/gyms/{c.id}/")
    old = lic.expiry_date
    lic.expiry_date = S.renew_to(lic, months)
    lic.save(update_fields=["expiry_date"])
    _audit("licence_renewed", c, lic, request, months=months, old_expiry=old.isoformat(), new_expiry=lic.expiry_date.isoformat())
    messages.success(request, f"Renewed for {months} month{'s' if months > 1 else ''}: valid until {lic.expiry_date:%d %b %Y}.")
    return redirect(f"/control/gyms/{c.id}/")


@owner_required
@require_POST
def gym_setup_code(request, client_id):
    c = get_object_or_404(Client, pk=client_id)
    _, code = OwnerSetupCode.issue(c, created_by=f"control_panel:{request.user.username}")
    _audit("owner_setup_code_issued", c, c.current_license, request)
    _reveal(request, code=code, what="setup")
    messages.success(request, "New owner setup code made. It's shown below once; it works for 7 days.")
    return redirect(f"/control/gyms/{c.id}/")


@owner_required
@require_POST
def gym_status(request, client_id):
    c, lic = _gym_and_licence(client_id)
    to = request.POST.get("to")
    if to not in (License.STATUS_ACTIVE, License.STATUS_SUSPENDED):
        raise Http404
    if request.POST.get("confirm") != c.gym_name:
        messages.error(request, "Type the gym's name exactly to confirm.")
        return redirect(f"/control/gyms/{c.id}/")
    old = lic.status
    lic.status = to
    lic.save(update_fields=["status"])
    _audit("licence_status_changed", c, lic, request, old=old, new=to)
    messages.success(request, "Licence suspended: the gym's apps are locked." if to == License.STATUS_SUSPENDED
                     else "Licence active again: the gym's apps work.")
    return redirect(f"/control/gyms/{c.id}/")


@owner_required
@require_POST
def gym_edit(request, client_id):
    c = get_object_or_404(Client, pk=client_id)
    p = request.POST
    name, owner, phone = p.get("gym_name", "").strip(), p.get("owner_name", "").strip(), p.get("phone", "").strip()
    if not (name and owner and phone):
        messages.error(request, "Gym name, owner and mobile are needed.")
        return redirect(f"/control/gyms/{c.id}/")
    before = {f: getattr(c, f) for f in ("gym_name", "owner_name", "phone", "email", "city", "notes")}
    c.gym_name, c.owner_name, c.phone = name[:200], owner[:150], phone[:20]
    c.email, c.city, c.notes = p.get("email", "").strip()[:254], p.get("city", "").strip()[:100], p.get("notes", "").strip()
    c.save()
    changed = [f for f, v in before.items() if getattr(c, f) != v]
    if changed:
        _audit("gym_details_changed", c, c.current_license, request, fields=changed)
    messages.success(request, "Gym details saved.")
    return redirect(f"/control/gyms/{c.id}/")


@owner_required
@require_POST
def gym_licence(request, client_id):
    """Plan and how many desktop (v1.2.0) PCs may use the licence."""
    c, lic = _gym_and_licence(client_id)
    try:
        maxd = max(1, min(20, int(request.POST.get("max_devices", lic.max_devices))))
    except ValueError:
        maxd = lic.max_devices
    plan = Plan.objects.filter(pk=request.POST.get("plan") or 0, status=Plan.STATUS_ACTIVE).first()
    old = {"max_devices": lic.max_devices, "plan": lic.plan.plan_id if lic.plan_id else None}
    lic.max_devices = maxd
    if plan:
        lic.plan = plan
    lic.save(update_fields=["max_devices", "plan"])
    _audit("licence_settings_changed", c, lic, request, old=old,
           new={"max_devices": lic.max_devices, "plan": lic.plan.plan_id if lic.plan_id else None})
    messages.success(request, "Licence settings saved.")
    return redirect(f"/control/gyms/{c.id}/")


@owner_required
@require_POST
def device_status(request, client_id, device_id):
    c, lic = _gym_and_licence(client_id)
    d = get_object_or_404(Device, pk=device_id, license__client=c)
    to = request.POST.get("to")
    if to not in (Device.STATUS_ACTIVE, Device.STATUS_BLOCKED):
        raise Http404
    d.status = to
    d.save(update_fields=["status"])
    _audit("device_activated" if to == Device.STATUS_ACTIVE else "device_deactivated", c, d.license, request,
           device=d.device_name or d.fingerprint[:12])
    messages.success(request, f"PC “{d.device_name or 'unnamed'}” {'allowed' if to == Device.STATUS_ACTIVE else 'blocked'}.")
    return redirect(f"/control/gyms/{c.id}/")


# --- add a gym -------------------------------------------------------------------------------

@owner_required
def add_gym(request):
    step = request.GET.get("step", "1")
    draft = request.session.get("cp_new_gym", {})
    ctx = {"nav": "add", "badges": S.today_summary(), "draft": draft, "err": "",
           "plans": Plan.objects.filter(status=Plan.STATUS_ACTIVE), "renew_months": RENEW_MONTHS}
    if request.method == "POST" and request.POST.get("step") == "1":
        p = request.POST
        draft = {k: p.get(k, "").strip() for k in ("gym_name", "owner_name", "phone", "city", "email", "notes")}
        request.session["cp_new_gym"] = draft
        ctx["draft"] = draft
        if not (draft["gym_name"] and draft["owner_name"] and len("".join(ch for ch in draft["phone"] if ch.isdigit())) >= 10):
            ctx["err"] = "Gym name, owner's name and a 10-digit mobile are needed."
            return render(request, "control_panel/add_gym.html", {**ctx, "step": "1"})
        return redirect("/control/gyms/new/?step=2")
    if request.method == "POST" and request.POST.get("step") == "2":
        if not draft:
            return redirect("/control/gyms/new/")
        p = request.POST
        try:
            months = int(p.get("months", "12"))
            maxd = max(1, min(20, int(p.get("max_devices", "1"))))
        except ValueError:
            months, maxd = 0, 1
        plan = Plan.objects.filter(pk=p.get("plan") or 0, status=Plan.STATUS_ACTIVE).first()
        if months not in RENEW_MONTHS or plan is None:
            ctx["err"] = "Pick a plan and how long the licence runs."
            return render(request, "control_panel/add_gym.html", {**ctx, "step": "2"})
        with transaction.atomic():
            c = Client.objects.create(gym_name=draft["gym_name"][:200], owner_name=draft["owner_name"][:150],
                                      phone=draft["phone"][:20], email=draft.get("email", "")[:254],
                                      city=draft.get("city", "")[:100], notes=draft.get("notes", ""))
            lic = License(client=c, plan=plan, max_devices=maxd, expiry_date=S.today())
            lic.expiry_date = S.renew_to(lic, months)
            lic.save()
            GymProfile.for_client(c)
            _, code = OwnerSetupCode.issue(c, created_by=f"control_panel:{request.user.username}")
            _audit("gym_created", c, lic, request, plan=plan.plan_id, months=months, max_devices=maxd,
                   price=p.get("price", ""))
        request.session.pop("cp_new_gym", None)
        _reveal(request, code=code, what="new")
        return redirect(f"/control/gyms/new/done/{c.id}/")
    if step == "2" and not draft:
        return redirect("/control/gyms/new/")
    return render(request, "control_panel/add_gym.html", {**ctx, "step": step if step in ("1", "2") else "1"})


@owner_required
def add_gym_done(request, client_id):
    c = get_object_or_404(Client, pk=client_id)
    lic = c.current_license
    rev = _pop_reveal(request)
    code = (rev or {}).get("code")
    return render(request, "control_panel/add_gym.html", {
        "nav": "add", "badges": S.today_summary(), "step": "3", "c": c, "lic": lic, "code": code,
        "wa": wa_link(c.phone, handover_text(c, lic.key, code)), "text": handover_text(c, lic.key, code),
        "app_address": app_address()})
