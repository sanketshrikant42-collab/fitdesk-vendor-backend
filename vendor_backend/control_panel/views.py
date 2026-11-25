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
PAY_MODES = ["UPI", "Cash", "Bank transfer", "Cheque"]
INVOICE_SALT = "crestline-invoice"
INVOICE_LINK_DAYS = 90


def _money(v):
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(str(v or "").replace(",", "").replace("₹", "").strip())
    except InvalidOperation:
        return None
    return d.quantize(Decimal("0.01")) if d > 0 else None


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

def nav_badges():
    from licensing.models import SupportQuery
    rows = S.gym_rows()
    try:
        hw = S.warnings_count()
    except Exception:                      # noqa: BLE001 - a badge must never break a page
        hw = 0
    return {"ending_soon": sum(1 for r in rows if r["soon"] and not r["is_test"]),
            "open_support": SupportQuery.objects.filter(status=SupportQuery.STATUS_OPEN).count(),
            "health_warn": hw}


@owner_required
def today_view(request):
    s = S.today_summary()
    s["money"] = S.money_summary()
    return render(request, "control_panel/today.html", {"s": s, "nav": "today", "badges": nav_badges(),
                                                         "hour": timezone.localtime(timezone=S.IST).hour})


@owner_required
def gyms_view(request, client_id=None):
    q, filt = request.GET.get("q", ""), request.GET.get("f", "")
    rows = S.gym_rows(q, filt)
    ctx = {"rows": rows, "q": q, "f": filt, "nav": "gyms", "badges": nav_badges(),
           "renew_months": RENEW_MONTHS, "plans": Plan.objects.filter(status=Plan.STATUS_ACTIVE)}
    if client_id is not None:
        c = get_object_or_404(Client, pk=client_id)
        from .models import GymBilling
        ctx.update({"sel": c, "d": S.gym_detail(c), "reveal": _pop_reveal(request),
                    "billing": GymBilling.objects.filter(client=c).first(),
                    "payments": list(c.vendor_payments.all()[:10])})
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
    from .models import GymBilling
    b = GymBilling.for_client(c)
    b.yearly_price = _money(request.POST.get("yearly_price"))
    b.launch_offer = request.POST.get("launch_offer") == "on"
    b.gstin = request.POST.get("gstin", "").strip().upper()[:20]
    b.save()
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
    ctx = {"nav": "add", "badges": nav_badges(), "draft": draft, "err": "",
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
            from .models import GymBilling
            price = _money(p.get("price"))
            GymBilling.objects.create(client=c, yearly_price=price, launch_offer=p.get("launch_offer") == "on")
            _audit("gym_created", c, lic, request, plan=plan.plan_id, months=months, max_devices=maxd,
                   price=str(price or ""))
            if p.get("paid_now") == "on" and price:
                pay = S.record_payment(c, price, p.get("mode") if p.get("mode") in PAY_MODES else "UPI", S.today(),
                                       months=0, reference=p.get("reference", ""), by=request.user.username,
                                       note="New licence")
                pay.period_from, pay.period_to, pay.months = S.today(), lic.expiry_date, months
                pay.save(update_fields=["period_from", "period_to", "months"])
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
        "nav": "add", "badges": nav_badges(), "step": "3", "c": c, "lic": lic, "code": code,
        "wa": wa_link(c.phone, handover_text(c, lic.key, code)), "text": handover_text(c, lic.key, code),
        "app_address": app_address()})


# --- renewals & money (Phase 2) ----------------------------------------------------------------

def invoice_link(request, p):
    """Short public link (/i/<code>, 90 days) the owner can open on WhatsApp."""
    from django.core import signing
    from gym_core.models import ShortLink
    from gym_core.services import public_base
    from .invoice import invoice_filename
    token = signing.dumps({"p": p.id}, salt=INVOICE_SALT)
    link = ShortLink.make("i", p.client, f"pay:{p.id}", token, invoice_filename(p))
    return f"{public_base(request)}/i/{link.code}"


def invoice_text(p, link):
    return "\n".join([f"Hello {p.client.owner_name},", "",
                      f"Thank you! We've received {S.format_rupees(p.amount)} for FitDesk Pro ({p.client.gym_name})."
                      + (f" Your licence is now valid until {p.period_to:%d %b %Y}." if p.period_to else ""),
                      "", f"Invoice {p.invoice_no}: {link}", "", "– Crestline Technologies"])


@owner_required
def money_view(request):
    from .models import GymBilling, VendorPayment
    if request.method == "POST":
        c = get_object_or_404(Client, pk=request.POST.get("client") or 0)
        amount = _money(request.POST.get("amount"))
        mode = request.POST.get("mode")
        try:
            months = int(request.POST.get("months", "0"))
        except ValueError:
            months = -1
        from datetime import date as _d
        try:
            paid_on = _d.fromisoformat(request.POST.get("paid_on") or S.today().isoformat())
        except ValueError:
            paid_on = None
        if not amount or mode not in PAY_MODES or months not in [0] + RENEW_MONTHS or not paid_on or paid_on > S.today():
            messages.error(request, "Check the amount, mode, date (not in the future) and how long to extend.")
            return redirect(f"/control/money/?gym={c.id}#pay")
        if months and c.current_license is None:
            messages.error(request, "This gym has no licence to extend.")
            return redirect(f"/control/money/?gym={c.id}#pay")
        p = S.record_payment(c, amount, mode, paid_on, months=months, reference=request.POST.get("reference", "").strip(),
                             note=request.POST.get("note", "").strip(), by=request.user.username)
        msg = f"Saved {S.format_rupees(amount)} from {c.gym_name} · invoice {p.invoice_no}"
        if p.period_to:
            msg += f" · licence now valid until {p.period_to:%d %b %Y}"
        messages.success(request, msg + ".")
        if request.POST.get("send") == "on":
            _reveal(request, what="invoice", wa=wa_link(c.phone, invoice_text(p, invoice_link(request, p))),
                    owner=c.owner_name, invoice=p.invoice_no)
        return redirect("/control/money/")
    m = S.money_summary()
    gyms = [r for r in S.gym_rows() if r["lic"]]
    pre = request.GET.get("gym")
    pre_c = Client.objects.filter(pk=pre).first() if pre and pre.isdigit() else None
    pre_price = getattr(GymBilling.objects.filter(client=pre_c).first(), "yearly_price", None) if pre_c else None
    return render(request, "control_panel/money.html", {
        "nav": "money", "badges": nav_badges(), "m": m, "gyms": gyms, "pre": pre_c, "pre_price": pre_price,
        "modes": PAY_MODES, "renew_months": RENEW_MONTHS, "today": S.today(), "reveal": _pop_reveal(request)})


@owner_required
@require_POST
def money_remind(request, client_id):
    """Logs the reminder, then opens WhatsApp with the message ready."""
    from django.http import HttpResponseRedirect
    from .models import GymBilling
    c, lic = _gym_and_licence(client_id)
    b = GymBilling.objects.filter(client=c).first()
    _audit("renewal_reminder_sent", c, lic, request, ends=lic.expiry_date.isoformat())
    return HttpResponseRedirect(wa_link(c.phone, S.renewal_text(c, lic, b.yearly_price if b else None)))


@owner_required
def payment_invoice(request, payment_id):
    from django.http import HttpResponse
    from .invoice import invoice_filename, invoice_pdf
    from .models import GymBilling, VendorPayment
    p = get_object_or_404(VendorPayment.objects.select_related("client"), pk=payment_id)
    resp = HttpResponse(invoice_pdf(p, GymBilling.objects.filter(client=p.client).first()), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{invoice_filename(p)}"'
    return resp


def invoice_shared(request, token, name=""):
    """The invoice link sent to the owner on WhatsApp (no sign-in)."""
    from django.core import signing
    from django.http import HttpResponse, JsonResponse
    from .invoice import invoice_filename, invoice_pdf
    from .models import GymBilling, VendorPayment
    try:
        data = signing.loads(token, salt=INVOICE_SALT, max_age=INVOICE_LINK_DAYS * 86400)
    except signing.BadSignature:
        return JsonResponse({"ok": False, "message": "This invoice link has expired. Ask Crestline to send it again."}, status=404)
    p = VendorPayment.objects.select_related("client").filter(pk=data.get("p")).first()
    if p is None:
        return JsonResponse({"ok": False, "message": "Invoice not found."}, status=404)
    resp = HttpResponse(invoice_pdf(p, GymBilling.objects.filter(client=p.client).first()), content_type="application/pdf")
    resp["Content-Disposition"] = f'{"inline" if request.GET.get("download") != "1" else "attachment"}; filename="{invoice_filename(p)}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


def invoice_short(request, code):
    from gym_auto.views import _short
    return _short(request, code, "i", invoice_shared)


# --- support (Phase 3) -------------------------------------------------------------------------

@owner_required
def support_view(request, query_id=None):
    from licensing.models import SupportQuery
    tab = request.GET.get("tab", "open")
    if tab not in ("open", "call", "done"):
        tab = "open"
    rows, counts = S.support_lists(tab)
    ctx = {"nav": "support", "badges": nav_badges(), "rows": rows, "counts": counts, "tab": tab}
    if query_id is not None:
        q = get_object_or_404(SupportQuery.objects.select_related("client"), pk=query_id)
        from .models import SupportMeta
        ctx.update({"sel": q, "meta": SupportMeta.objects.filter(query=q).first(),
                    "wa": wa_link(q.client.phone, f"Hello {q.client.owner_name}, this is Crestline about your question “{q.subject}”.")})
    elif rows:
        return redirect(f"/control/support/{rows[0]['q'].id}/?tab={tab}")
    return render(request, "control_panel/support.html", ctx)


@owner_required
@require_POST
def support_action(request, query_id):
    from licensing.models import SupportQuery
    from .models import SupportMeta
    q = get_object_or_404(SupportQuery.objects.select_related("client"), pk=query_id)
    meta = SupportMeta.for_query(q)
    act = request.POST.get("act")
    tab = request.POST.get("tab", "open")
    if act == "reply":
        text = request.POST.get("text", "").strip()
        if not text:
            messages.error(request, "Write a reply first.")
            return redirect(f"/control/support/{q.id}/?tab={tab}")
        stamp = timezone.localtime(timezone=S.IST).strftime("%d %b %Y, %I:%M %p")
        q.admin_notes = (q.admin_notes.rstrip() + "\n\n" if q.admin_notes.strip() else "") + f"[{stamp}] Crestline: {text}"
        q.save(update_fields=["admin_notes"])
        meta.last_reply_at = timezone.now()
        if request.POST.get("close") == "on":
            q.mark_resolved()
            meta.call_back = False
        meta.save()
        _audit("support_replied", q.client, None, request, query=q.id, closed=q.status == SupportQuery.STATUS_RESOLVED)
        messages.success(request, "Reply saved. The gym sees it in FitDesk's Support screen."
                         + (" Question closed." if q.status == SupportQuery.STATUS_RESOLVED else ""))
    elif act == "note":
        meta.private_note = request.POST.get("note", "").strip()
        meta.save(update_fields=["private_note"])
        messages.success(request, "Private note saved.")
    elif act == "callback":
        meta.call_back = not meta.call_back
        meta.save(update_fields=["call_back"])
        messages.success(request, "Moved to Call back." if meta.call_back else "Removed from Call back.")
    elif act == "close":
        q.mark_resolved()
        meta.call_back = False
        meta.save(update_fields=["call_back"])
        _audit("support_closed", q.client, None, request, query=q.id)
        messages.success(request, "Question closed.")
    elif act == "reopen":
        q.status, q.resolved_at = SupportQuery.STATUS_OPEN, None
        q.save(update_fields=["status", "resolved_at"])
        messages.success(request, "Question open again.")
    else:
        raise Http404
    new_tab = "done" if q.status == SupportQuery.STATUS_RESOLVED else ("call" if meta.call_back else "open")
    return redirect(f"/control/support/{q.id}/?tab={new_tab}")


# --- health (Phase 3) --------------------------------------------------------------------------

@owner_required
def health_view(request):
    return render(request, "control_panel/health.html", {"nav": "health", "badges": nav_badges(), "h": S.health_summary()})


@owner_required
@require_POST
def health_tool(request):
    import io
    from django.core.management import call_command
    tool = request.POST.get("tool")
    out = io.StringIO()
    try:
        if tool == "backup":
            call_command("backup_now", reason="manual", stdout=out)
            messages.success(request, "Server backup made and checked.")
        elif tool == "reminders":
            call_command("run_daily_jobs", stdout=out)
            messages.success(request, "Today's reminder lists made for every gym (already-made items aren't repeated).")
        else:
            raise Http404
        _audit("server_tool_run", None, None, request, tool=tool)
    except Http404:
        raise
    except Exception as e:                  # noqa: BLE001 - show the reason instead of a server error page
        messages.error(request, f"That didn't work: {str(e)[:200]}")
    return redirect("/control/health/")
