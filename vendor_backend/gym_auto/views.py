"""/api/v1/ endpoints for Phase 3C: the daily "to send" list, owner's
summary, reports + Excel export, receipt PDF."""
from datetime import timedelta

from django.http import HttpResponse

from gym_accounts.api import ApiError, endpoint, json_body, ok, staff_required
from gym_accounts.permissions import has_perm
from gym_core import services as C
from gym_core.models import Bill, GymSettings

from . import export as X
from . import reports as RP
from . import services as S
from .models import Reminder, ReminderSettings


def need(user, module, action, msg=None):
    if not has_perm(user, module, action):
        raise ApiError(403, "permission_denied", msg or "Your role can't do this.", module=module, action=action)


# --- the daily list -------------------------------------------------------------------------------

def _visible(request, qs):
    return qs if S.can_see_summary(request.staff) else qs.exclude(kind=Reminder.KIND_SUMMARY)


@endpoint("GET")
@staff_required("receipts", "view")
def reminders(request):
    """GET ?date=&kind=&status= → the day's list. Opening today's list runs
    the morning job if it hasn't run yet."""
    today = C.today_for(request.client)
    on = C.parse_date(request.GET.get("date"), "date", today)
    if on == today:
        S.ensure_today(request.client)
    base = _visible(request, Reminder.objects.filter(client=request.client))
    day = base.filter(for_date=on)
    counts = {}
    for k in Reminder.KINDS:
        sub = day.filter(kind=k)
        n = sub.count()
        if n:
            counts[k] = {"total": n, "pending": sub.filter(status=Reminder.PENDING).count()}
    qs = day.select_related("member", "sent_by")
    if request.GET.get("kind") in Reminder.KINDS:
        qs = qs.filter(kind=request.GET["kind"])
    if request.GET.get("status") in Reminder.STATUSES:
        qs = qs.filter(status=request.GET["status"])
    older = base.filter(for_date__lt=on, for_date__gte=on - timedelta(days=14), status=Reminder.PENDING)
    run = request.client.daily_runs.filter(date=on).first()
    return ok({"date": on.isoformat(), "today": today.isoformat(), "ran_at": run.ran_at.isoformat() if run else None,
               "counts": counts, "reminders": [S.reminder_json(r) for r in qs],
               "older_pending": older.count(), "call_outcomes": Reminder.CALL_OUTCOMES})


@endpoint("GET")
@staff_required("receipts", "view")
def reminders_older(request):
    """Items from the last 14 days still not sent."""
    on = C.today_for(request.client)
    qs = (_visible(request, Reminder.objects.filter(client=request.client, for_date__lt=on,
                                                    for_date__gte=on - timedelta(days=14), status=Reminder.PENDING))
          .select_related("member", "sent_by").order_by("-for_date", "kind"))
    return ok({"reminders": [S.reminder_json(r) for r in qs]})


@endpoint("POST")
@staff_required("receipts", "add")
def reminders_run(request):
    """Runs the morning job now (safe to repeat: nothing is made twice)."""
    today = C.today_for(request.client)
    on = C.parse_date(json_body(request).get("date"), "date", today)
    if on > today:
        raise ApiError(400, "future_date", "The list can't be made for a future day.")
    created = S.generate(request.client, on)
    C.log(request, "reminders_run", on.isoformat(), created=created)
    return ok({"date": on.isoformat(), "created": created})


@endpoint("POST")
@staff_required("receipts", "add")
def reminder_action(request, reminder_id, action):
    r = Reminder.objects.filter(client=request.client, pk=reminder_id).select_related("member").first()
    if r is None or (r.kind == Reminder.KIND_SUMMARY and not S.can_see_summary(request.staff)):
        raise ApiError(404, "not_found", "Reminder not found.")
    return ok({"reminder": S.reminder_json(S.act(request, r, action, json_body(request)))})


@endpoint("GET", "POST")
@staff_required("receipts", "view")
def reminder_settings(request):
    if request.method == "POST":
        need(request.staff, "settings", "edit", "Only the owner can change reminder settings.")
        return ok({"settings": S.settings_json(S.update_settings(request, json_body(request)))})
    return ok({"settings": S.settings_json(ReminderSettings.for_client(request.client))})


@endpoint("GET")
@staff_required("reports", "view")
def owner_summary(request):
    """GET ?date= (default today, so far) → numbers + WhatsApp text."""
    need(request.staff, "billing", "view")
    on = C.parse_date(request.GET.get("date"), "date", C.today_for(request.client))
    s = S.summary(request.client, on)
    phone = S.owner_phone(request.client, ReminderSettings.for_client(request.client))
    from gym_core import rules as R
    s["whatsapp_link"] = R.whatsapp_link(phone, s["text"]) if phone else ""
    s["phone"] = phone
    return ok({"summary": s})


# --- reports ------------------------------------------------------------------------------------------

REPORT_PERMS = {"collections": [("billing", "view")], "renewals": [("members", "view")], "trainers": [("salaries", "view")],
                "profit": [("billing", "view"), ("expenses", "view")], "enquiries": [("enquiries", "view")],
                "gst": [("billing", "view")]}


def _report(request, name):
    if name not in RP.REPORTS:
        raise ApiError(404, "not_found", "Unknown report.")
    need(request.staff, "reports", "view")
    for m, a in REPORT_PERMS[name]:
        if not (request.staff.role == "owner" or has_perm(request.staff, m, a)):
            raise ApiError(403, "permission_denied", "Your role can't see this report.", module=m, action=a)
    today = C.today_for(request.client)
    to = C.parse_date(request.GET.get("to"), "to", today)
    frm = C.parse_date(request.GET.get("from"), "from", to.replace(day=1))
    if frm > to:
        raise ApiError(400, "bad_range", "'From' must be on or before 'to'.")
    if (to - frm).days > 731:
        raise ApiError(400, "bad_range", "Pick at most two years.")
    if name == "collections":
        return RP.collections(request.client, frm, to, "month" if request.GET.get("group") == "month" else "day")
    return RP.REPORTS[name](request.client, frm, to)


@endpoint("GET")
@staff_required("reports", "view")
def report(request, name):
    return ok({"report": _report(request, name)})


@endpoint("GET")
@staff_required("reports", "export")
def report_export(request, name):
    rep = _report(request, name)
    gym = GymSettings.for_client(request.client).gym_name
    data = X.workbook(rep, gym)
    C.log(request, "report_exported", rep["title"], frm=rep["from"], to=rep["to"])
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="fitdesk-{name}-{rep["from"]}-to-{rep["to"]}.xlsx"'
    return resp


# --- receipt PDF ---------------------------------------------------------------------------------------

RECEIPT_LINK_SALT = "fitdesk-receipt-pdf"
RECEIPT_LINK_SECONDS = 15 * 60


def receipt_filename(b):
    """"RCPT00003 - Heet Joshi.pdf": receipt number, then the member's name,
    so saved receipts never need renaming."""
    import re
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", f"{b.receipt_no} - {b.member.name}")
    return re.sub(r"\s+", " ", name).strip()[:120] + ".pdf"


def _pdf_response(b, download=False):
    from urllib.parse import quote
    from .pdf import receipt_pdf as build
    b = (Bill.objects.select_related("plan", "trainer", "member", "created_by", "client")
         .prefetch_related("payments__received_by", "memberships__member").get(pk=b.pk))
    data = build(C.bill_json(b), GymSettings.for_client(b.client))
    name = receipt_filename(b)
    ascii_name = name.encode("ascii", "ignore").decode() or f"{b.receipt_no}.pdf"
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = (f'{"attachment" if download else "inline"}; filename="{ascii_name}"; '
                                   f"filename*=UTF-8''{quote(name)}")
    resp["Cache-Control"] = "private, no-store"
    return resp


@endpoint("GET")
@staff_required("billing", "view")
def receipt_pdf(request, bill_id):
    b = C.get_in_gym(Bill, request.client, bill_id, "Bill")
    return _pdf_response(b, download=request.GET.get("download") == "1")


@endpoint("GET")
@staff_required("billing", "view")
def receipt_link(request, bill_id):
    """A link to the PDF that works for 15 minutes without signing in, so it
    opens in a normal browser tab and saves as "RCPT00003 - Name.pdf"."""
    from urllib.parse import quote
    from django.core import signing
    b = C.get_in_gym(Bill, request.client, bill_id, "Bill")
    token = signing.dumps({"b": str(b.id), "c": request.client.id}, salt=RECEIPT_LINK_SALT)
    name = receipt_filename(b)
    url = f"/api/v1/receipts/{token}/{quote(name)}"
    return ok({"url": url, "download_url": url + "?download=1", "filename": name, "expires_in": RECEIPT_LINK_SECONDS})


@endpoint("GET")
def receipt_shared(request, token, name):
    from django.core import signing
    from gym_accounts.api import require_active_licence
    try:
        data = signing.loads(token, salt=RECEIPT_LINK_SALT, max_age=RECEIPT_LINK_SECONDS)
    except signing.BadSignature:          # also covers an expired link
        raise ApiError(404, "link_expired", "This receipt link has expired. Open the receipt again from FitDesk.")
    b = Bill.objects.filter(pk=data.get("b"), client_id=data.get("c")).select_related("client", "member").first()
    if b is None:
        raise ApiError(404, "not_found", "Receipt not found.")
    require_active_licence(b.client)
    return _pdf_response(b, download=request.GET.get("download") == "1")


# --- diet plan PDF -------------------------------------------------------------------------------------

DIET_LINK_SALT = "fitdesk-diet-pdf"
DIET_STAFF_LINK_SECONDS = 15 * 60
DIET_MEMBER_LINK_DAYS = 60          # the link sent to the member on WhatsApp


def diet_filename(plan_json):
    import re
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", f"Diet plan - {plan_json['member']['name']} - v{plan_json['version']['version_no']}")
    return re.sub(r"\s+", " ", name).strip()[:120] + ".pdf"


def public_base(request):
    """Address members' phones use to open links. PUBLIC_BASE_URL wins
    (e.g. https://fitdesk.onrender.com); otherwise this request's address."""
    import os
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    scheme = request.META.get("HTTP_X_FORWARDED_PROTO", request.scheme).split(",")[0].strip()
    return f"{scheme}://{request.get_host()}"


def is_local(base):
    return any(h in base for h in ("://127.", "://localhost", "://0.0.0.0", "://192.168.", "://10."))


def _diet_pdf_response(plan_obj, version_no, client, download=False):
    from urllib.parse import quote
    from gym_ops import services as OS
    from .diet_pdf import diet_plan_pdf
    pj = OS.diet_plan_json(plan_obj, version_no)
    if "version" not in pj:
        raise ApiError(404, "not_found", "That version of the plan was not found.")
    data = diet_plan_pdf(pj, GymSettings.for_client(client))
    name = diet_filename(pj)
    ascii_name = name.encode("ascii", "ignore").decode() or "diet-plan.pdf"
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = (f'{"attachment" if download else "inline"}; filename="{ascii_name}"; '
                                   f"filename*=UTF-8''{quote(name)}")
    resp["Cache-Control"] = "private, no-store"
    return resp, name


def _staff_plan(request, plan_id):
    from gym_ops.models import DietPlan
    p = C.get_in_gym(DietPlan, request.client, plan_id, "Diet plan")
    C.require_member_in_scope(request.staff, p.member, "diet")
    return p


def diet_member_link(request, p):
    """Link for the member (60 days, opens without signing in) to this
    version of the plan, plus its file name."""
    from urllib.parse import quote
    from django.core import signing
    from gym_ops import services as OS
    token = signing.dumps({"p": p.id, "v": p.current_version, "c": p.client_id}, salt=DIET_LINK_SALT)
    name = diet_filename(OS.diet_plan_json(p, with_nutrition=False))
    base = public_base(request)
    return f"{base}/api/v1/d/{token}/{quote(name)}", name, is_local(base)


@endpoint("GET")
@staff_required("diet", "view")
def diet_pdf(request, plan_id):
    p = _staff_plan(request, plan_id)
    v = request.GET.get("version")
    resp, _ = _diet_pdf_response(p, int(v) if v and v.isdigit() else None, request.client, request.GET.get("download") == "1")
    return resp


@endpoint("GET")
@staff_required("diet", "view")
def diet_pdf_link(request, plan_id):
    """For staff screens: a 15-minute link so the PDF opens in a browser tab
    and saves as "Diet plan - Name - v2.pdf"."""
    from urllib.parse import quote
    from django.core import signing
    from gym_ops import services as OS
    p = _staff_plan(request, plan_id)
    token = signing.dumps({"p": p.id, "v": p.current_version, "c": p.client_id, "s": 1}, salt=DIET_LINK_SALT)
    name = diet_filename(OS.diet_plan_json(p, with_nutrition=False))
    url = f"/api/v1/d/{token}/{quote(name)}"
    return ok({"url": url, "download_url": url + "?download=1", "filename": name})


@endpoint("GET")
def diet_pdf_shared(request, token, name):
    from django.core import signing
    from gym_accounts.api import require_active_licence
    from gym_ops.models import DietPlan
    try:
        data = signing.loads(token, salt=DIET_LINK_SALT, max_age=DIET_MEMBER_LINK_DAYS * 86400)
        if data.get("s"):                      # staff links are short-lived
            signing.loads(token, salt=DIET_LINK_SALT, max_age=DIET_STAFF_LINK_SECONDS)
    except signing.BadSignature:
        raise ApiError(404, "link_expired", "This diet plan link has expired. Ask your gym to send it again.")
    p = DietPlan.objects.filter(pk=data.get("p"), client_id=data.get("c")).select_related("client", "member").first()
    if p is None:
        raise ApiError(404, "not_found", "Diet plan not found.")
    require_active_licence(p.client)
    resp, _ = _diet_pdf_response(p, data.get("v"), p.client, request.GET.get("download") == "1")
    return resp


# --- email ---------------------------------------------------------------------------------------

def _email_target(request, member, data):
    """The address to send to: the one typed in the dialog, else the
    member's saved email. Optionally saves a typed address on the member."""
    from gym_core.services import clean_email
    to = clean_email(data.get("to")) if data.get("to") else (member.email if member else "")
    if not to:
        raise ApiError(400, "no_email", f"{member.name if member else 'This person'} has no email saved. Type their email address.")
    if member and data.get("save_to_member") and to != member.email and has_perm(request.staff, "members", "edit"):
        member.email = to
        member.version += 1
        member.save(update_fields=["email", "version", "updated_at"])
    return to


def _sent(to, extra=None):
    return ok({"sent": True, "to": to, **(extra or {})})


@endpoint("GET", "POST")
@staff_required("receipts", "view")
def email_settings(request):
    from . import mailer as ML
    from .models import EmailSettings
    if request.method == "POST":
        need(request.staff, "settings", "edit", "Only the owner can change email settings.")
        return ok({"settings": ML.settings_json(ML.update_settings(request, json_body(request)))})
    return ok({"settings": ML.settings_json(EmailSettings.for_client(request.client))})


@endpoint("POST")
@staff_required("settings", "edit")
def email_test(request):
    from . import mailer as ML
    from .models import EmailSettings
    es = EmailSettings.for_client(request.client)
    gs = GymSettings.for_client(request.client)
    to = json_body(request).get("to") or es.sender_email or gs.email
    to = ML.send(request, to, f"Test email from {gs.gym_name}",
                 f"This is a test email from FitDesk Pro. Email is set up correctly for {gs.gym_name}.",
                 ML.wrap_html(gs, "Email is working", [f"This is a test from FitDesk Pro. Receipts, diet plans and reminders "
                                                         f"will be sent from this address."]), kind="test", es=es)
    ML.mark_verified(es)
    return _sent(to, {"settings": ML.settings_json(es)})


@endpoint("GET")
@staff_required("receipts", "view")
def email_log(request):
    from .models import EmailLog
    qs = EmailLog.objects.filter(client=request.client).select_related("member", "sent_by")
    if request.GET.get("member_id"):
        qs = qs.filter(member_id=request.GET["member_id"])
    return ok({"emails": [{"id": e.id, "kind": e.kind, "ref": e.ref, "to": e.to, "subject": e.subject, "status": e.status,
                           "error": e.error, "member": e.member.name if e.member_id else None,
                           "sent_by": e.sent_by.name if e.sent_by_id else None, "at": e.created_at.isoformat()}
                          for e in qs[:50]]})


@endpoint("POST")
@staff_required("billing", "view")
def receipt_email(request, bill_id):
    from gym_core.receipt import receipt_view
    from . import mailer as ML
    from .pdf import receipt_pdf as build
    b = (Bill.objects.select_related("plan", "trainer", "member", "created_by", "client")
         .prefetch_related("payments__received_by", "memberships__member").filter(client=request.client, pk=bill_id).first())
    if b is None:
        raise ApiError(404, "not_found", "Bill not found.")
    data = json_body(request)
    to = _email_target(request, b.member, data)
    gs = GymSettings.for_client(request.client)
    bj = C.bill_json(b)
    r = receipt_view(bj, gs)
    rows = [(x["label"], x["value"]) for x in r["rows"]]
    first = b.member.name.split()[0]
    text_rows = "\n".join(f"{k}: {v}" for k, v in rows)
    body = (f"Hi {first},\n\nThank you for your payment at {gs.gym_name}. Your receipt {b.receipt_no} is attached.\n\n"
            f"{text_rows}\n\n{gs.gym_name}" + (f"\n{gs.phone}" if gs.phone else ""))
    paras = [f"Hi {first},", f"Thank you for your payment. Your receipt {b.receipt_no} is attached as a PDF."]
    if r["upi"]:
        paras.append(f"Balance due can be paid by UPI to {r['upi']}.")
    pdf = build(bj, gs)
    to = ML.send(request, to, f"Receipt {b.receipt_no} · {gs.gym_name}", body,
                 ML.wrap_html(gs, f"Receipt {b.receipt_no}", paras, rows, gs.receipt_footer or ""),
                 [(receipt_filename(b), pdf, "application/pdf")], kind="receipt", ref=b.receipt_no, member=b.member)
    return _sent(to)


@endpoint("POST")
@staff_required("diet", "view")
def diet_email(request, plan_id):
    from gym_ops import services as OS
    from . import mailer as ML
    from .diet_pdf import diet_plan_pdf
    p = _staff_plan(request, plan_id)
    to = _email_target(request, p.member, json_body(request))
    gs = GymSettings.for_client(request.client)
    pj = OS.diet_plan_json(p)
    d = pj["nutrition"]["day"]
    rows = [("Plan", pj["name"]), ("Version", str(pj["version"]["version_no"]))]
    if d.get("kcal"):
        rows += [("Calories / day", f"{round(d['kcal']):,} kcal"), ("Protein", f"{round(d['protein_g'] or 0)} g"),
                 ("Carbs", f"{round(d['carbs_g'] or 0)} g"), ("Fat", f"{round(d['fat_g'] or 0)} g")]
    first = p.member.name.split()[0]
    text_body = OS.diet_share_text(p).replace("*", "").replace("_", "")
    to = ML.send(request, to, f"Your diet plan · {gs.gym_name}",
                 f"Hi {first},\n\nYour diet plan is attached as a PDF, with calories for every meal.\n\n{text_body}",
                 ML.wrap_html(gs, "Your diet plan", [f"Hi {first},", "Your diet plan is attached as a PDF, with calories, "
                              "protein, carbs and fat for every meal."], rows,
                              "Values are approximate. Tell the gym about any health condition or allergy."),
                 [(diet_filename(pj), diet_plan_pdf(pj, gs), "application/pdf")], kind="diet_plan", ref=p.id, member=p.member)
    return _sent(to)


@endpoint("POST")
@staff_required("receipts", "view")
def reminder_email(request, reminder_id):
    from . import mailer as ML
    r = Reminder.objects.filter(client=request.client, pk=reminder_id).select_related("member").first()
    if r is None or r.member is None:
        raise ApiError(404, "not_found", "Reminder not found.")
    to = _email_target(request, r.member, json_body(request))
    gs = GymSettings.for_client(request.client)
    text_body = r.text.replace("*", "").replace("_", "")
    subjects = {"renewal": "Your membership is ending soon", "expired": "Your membership has ended",
                "dues": "Payment due", "birthday": "Happy birthday!", "inactive": "We miss you at the gym"}
    subject = f"{subjects.get(r.kind, 'Message')} · {gs.gym_name}"
    paras = [p for p in text_body.split("\n") if p.strip()]
    to = ML.send(request, to, subject, text_body, ML.wrap_html(gs, subjects.get(r.kind, "Message"), paras),
                 kind="reminder", ref=r.id, member=r.member)
    S.act(request, r, "sent", {})
    r.channel = "email"
    r.save(update_fields=["channel"])
    return _sent(to, {"reminder": S.reminder_json(r)})
