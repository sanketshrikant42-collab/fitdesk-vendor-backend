"""/api/v1/ endpoints for Phase 3A. Sign-in, device and licence checks
and role permissions come from gym_accounts.staff_required."""

from django.db.models import Q

from gym_accounts.api import ApiError, endpoint, json_body, ok, paginate, staff_required
from gym_accounts.permissions import has_perm

from . import services as S
from .models import Bill, CheckIn, Freeze, GymSettings, Member, Membership, Plan, Trainer


# --- settings -----------------------------------------------------------------------------

@endpoint("GET")
@staff_required()
def gym_logo(request):
    """The gym's logo as PNG (404 if none). Any signed-in staff member."""
    from django.http import HttpResponse
    from .receipt import logo_bytes
    data = logo_bytes(GymSettings.for_client(request.client))
    if not data:
        raise ApiError(404, "no_logo", "No logo uploaded yet.")
    resp = HttpResponse(data, content_type="image/png")
    resp["Cache-Control"] = "private, max-age=300"
    return resp


@endpoint("POST")
@staff_required("settings", "edit")
def gym_logo_upload(request):
    """multipart/form-data with "logo" (PNG / JPG / WEBP, up to 2 MB)."""
    from .receipt import save_logo
    s = save_logo(GymSettings.for_client(request.client), request.FILES.get("logo"))
    S.log(request, "logo_uploaded", s.gym_name)
    return ok({"settings": S.settings_json(s)})


@endpoint("POST")
@staff_required("settings", "edit")
def gym_logo_remove(request):
    from .receipt import remove_logo
    s = remove_logo(GymSettings.for_client(request.client))
    S.log(request, "logo_removed", s.gym_name)
    return ok({"settings": S.settings_json(s)})


@endpoint("GET", "POST")
@staff_required()
def gym_settings(request):
    if request.method == "POST":
        if not has_perm(request.staff, "settings", "edit"):
            raise ApiError(403, "permission_denied", "Only the owner can change gym settings.", module="settings", action="edit")
        return ok({"settings": S.settings_json(S.update_settings(request, json_body(request)))})
    return ok({"settings": S.settings_json(GymSettings.for_client(request.client))})


@endpoint("GET")
@staff_required("dashboard", "view")
def dashboard(request):
    return ok({"dashboard": S.dashboard(request)})


@endpoint("GET")
@staff_required("dashboard", "view")
def dashboard_insights(request):
    """Chart data for the dashboard (money/enquiry parts follow the role)."""
    return ok({"insights": S.dashboard_insights(request)})


# --- plans --------------------------------------------------------------------------------

@endpoint("GET", "POST")
@staff_required()
def plans(request):
    if request.method == "POST":
        if not has_perm(request.staff, "plans", "add"):
            raise ApiError(403, "permission_denied", "Your role can't add plans.", module="plans", action="add")
        return ok({"plan": S.plan_json(S.save_plan(request, json_body(request)))}, status=201)
    # Every signed-in role that can bill or view plans needs the list.
    if not (has_perm(request.staff, "plans", "view") or has_perm(request.staff, "billing", "view")):
        raise ApiError(403, "permission_denied", "Your role can't see plans.", module="plans", action="view")
    qs = Plan.objects.filter(client=request.client)
    if request.GET.get("status") != "all":
        qs = qs.filter(status=Plan.STATUS_ACTIVE)
    return ok({"plans": [S.plan_json(p) for p in qs],
               "gst_percent": str(GymSettings.for_client(request.client).gst_percent)})


@endpoint("POST")
@staff_required("plans", "edit")
def plan_update(request, plan_id):
    p = S.get_in_gym(Plan, request.client, plan_id, "Plan")
    return ok({"plan": S.plan_json(S.save_plan(request, json_body(request), p))})


@endpoint("POST")
@staff_required("plans", "delete")
def plan_archive(request, plan_id):
    p = S.get_in_gym(Plan, request.client, plan_id, "Plan")
    p.status = Plan.STATUS_ARCHIVED if json_body(request).get("archived", True) else Plan.STATUS_ACTIVE
    p.save(update_fields=["status"])
    S.log(request, "plan_archived" if p.status == Plan.STATUS_ARCHIVED else "plan_restored", p.name)
    return ok({"plan": S.plan_json(p)})


# --- trainers ------------------------------------------------------------------------------

@endpoint("GET", "POST")
@staff_required()
def trainers(request):
    if request.method == "POST":
        if not has_perm(request.staff, "trainers", "add"):
            raise ApiError(403, "permission_denied", "Your role can't add trainers.", module="trainers", action="add")
        return ok({"trainer": S.trainer_json(S.save_trainer(request, json_body(request)))}, status=201)
    if not (has_perm(request.staff, "trainers", "view") or has_perm(request.staff, "billing", "add")):
        raise ApiError(403, "permission_denied", "Your role can't see trainers.", module="trainers", action="view")
    qs = Trainer.objects.filter(client=request.client).select_related("staff_user")
    if request.GET.get("status") in ("active", "inactive"):
        qs = qs.filter(status=request.GET["status"])
    return ok({"trainers": [S.trainer_json(t) for t in qs]})


@endpoint("GET")
@staff_required("trainers", "view")
def trainer_detail(request, trainer_id):
    """Trainer profile. A trainer login (scope "own") can open only their own."""
    from gym_accounts.permissions import SCOPE_OWN, scope_for
    t = S.get_in_gym(Trainer, request.client, trainer_id, "Trainer")
    if scope_for(request.staff, "trainers") == SCOPE_OWN:
        mine = S.trainer_for(request.staff)
        if mine is None or mine.pk != t.pk:
            raise ApiError(404, "not_found", "Trainer not found.")
    return ok({"trainer": S.trainer_profile(request, t)})


@endpoint("POST")
@staff_required("trainers", "delete")
def trainer_remove(request, trainer_id):
    """Removes an unused trainer; one with history is marked inactive instead."""
    t = S.get_in_gym(Trainer, request.client, trainer_id, "Trainer")
    return ok({"result": S.remove_trainer(request, t)})


@endpoint("POST")
@staff_required("trainers", "edit")
def trainer_update(request, trainer_id):
    t = S.get_in_gym(Trainer, request.client, trainer_id, "Trainer")
    return ok({"trainer": S.trainer_json(S.save_trainer(request, json_body(request), t))})


# --- members ---------------------------------------------------------------------------------

@endpoint("GET", "POST")
@staff_required()
def members(request):
    if request.method == "POST":
        if not has_perm(request.staff, "members", "add"):
            raise ApiError(403, "permission_denied", "Your role can't add members.", module="members", action="add")
        m = S.save_member(request, json_body(request))
        data = json_body(request)
        if isinstance(data.get("fitness"), dict):
            S.save_fitness(request, m, data["fitness"])
        return ok({"member": S.member_json(m, detail=True)}, status=201)
    if not has_perm(request.staff, "members", "view"):
        raise ApiError(403, "permission_denied", "Your role can't see members.", module="members", action="view")
    qs = S.scoped_members(request.staff)
    qs = qs.filter(is_archived=request.GET.get("archived") == "1")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(phone__contains=q))
    status = request.GET.get("status")
    on = S.today_for(request.client)
    if status in ("active", "expired", "expiring", "dues", "frozen", "no_plan", "upcoming"):
        # Status depends on memberships; filter in Python on the page set (gyms are small).
        rows = [m for m in qs.prefetch_related("memberships__plan", "memberships__trainer", "memberships__freezes")]
        def keep(m):
            st = S.membership_state(m, on)
            if status == "expiring":
                return st["state"] == "active" and st["days_left"] is not None and st["days_left"] <= 7
            if status == "dues":
                return S.dues_for(m) > 0
            return st["state"] == status
        rows = [m for m in rows if keep(m)]
        from django.core.paginator import Paginator
        page = max(1, int(request.GET.get("page", 1) or 1))
        size = min(100, max(1, int(request.GET.get("page_size", 25) or 25)))
        p = Paginator(rows, size)
        items = list(p.page(min(page, p.num_pages or 1)).object_list) if rows else []
        meta = {"page": page, "page_size": size, "total": len(rows), "has_next": page * size < len(rows)}
    else:
        items, meta = paginate(request, qs.prefetch_related("memberships__plan", "memberships__trainer", "memberships__freezes"))
    rows = [S.member_json(m, on=on) for m in items]
    if status == "dues":                       # the list of who owes money shows how much
        for j, m in zip(rows, items):
            j["dues"] = str(S.dues_for(m))
    return ok({"members": rows, "pagination": meta})


def _member(request, member_id, module="members"):
    m = S.get_in_gym(Member, request.client, member_id, "Member")
    S.require_member_in_scope(request.staff, m, module)
    return m


@endpoint("GET")
@staff_required("members", "view")
def member_detail(request, member_id):
    m = _member(request, member_id)
    out = S.member_json(m, detail=True)
    if has_perm(request.staff, "billing", "view"):
        out["bills"] = [S.bill_json(b, with_links=False) for b in m.bills.select_related("plan", "trainer", "member", "created_by").prefetch_related("payments__received_by", "memberships__member")]
    return ok({"member": out})


@endpoint("POST")
@staff_required("members", "edit")
def member_update(request, member_id):
    m = _member(request, member_id)
    return ok({"member": S.member_json(S.save_member(request, json_body(request), m), detail=True)})


@endpoint("POST")
@staff_required("members", "edit")
def member_fitness(request, member_id):
    m = _member(request, member_id)
    S.save_fitness(request, m, json_body(request))
    return ok({"member": S.member_json(m, detail=True)})


@endpoint("POST")
@staff_required("members", "delete")
def member_archive(request, member_id):
    m = _member(request, member_id)
    m.is_archived = bool(json_body(request).get("archived", True))
    m.version += 1
    m.save(update_fields=["is_archived", "version"])
    S.log(request, "member_archived" if m.is_archived else "member_restored", f"{m.name} ({m.code})")
    return ok({"member": S.member_json(m)})


@endpoint("GET")
@staff_required("members", "delete")
def member_records(request, member_id):
    m = _member(request, member_id)
    return ok({"records": S.member_records(m), "same_phone": [
        {"id": str(x.id), "name": x.name, "code": x.code, "phone": x.phone}
        for x in Member.objects.filter(client=request.client, phone__endswith="".join(c for c in m.phone if c.isdigit())[-10:])
        .exclude(pk=m.pk)]})


@endpoint("POST")
@staff_required("members", "delete")
def member_delete(request, member_id):
    """Only for a member with nothing linked (added twice or by mistake)."""
    S.delete_member(request, _member(request, member_id))
    return ok({"deleted": True})


@endpoint("POST")
@staff_required("members", "delete")
def member_merge(request, member_id):
    """Moves this (duplicate) member's bills and history to `into_id`, then deletes it."""
    dup = _member(request, member_id)
    keep = _member(request, json_body(request).get("into_id"))
    keep, moved = S.merge_members(request, dup, keep)
    return ok({"member": S.member_json(keep, detail=True), "moved": moved})


# --- billing -----------------------------------------------------------------------------------

@endpoint("POST")
@staff_required("billing", "view")
def bill_quote(request):
    return ok({"quote": S.quote(request, json_body(request))})


def _bill_qs(request):
    return Bill.objects.filter(client=request.client).select_related("member", "plan", "trainer", "created_by").prefetch_related(
        "payments__received_by", "memberships__member")


@endpoint("GET", "POST")
@staff_required("billing", "view")
def bills(request):
    if request.method == "POST":
        if not has_perm(request.staff, "billing", "add"):
            raise ApiError(403, "permission_denied", "Your role can't create bills.", module="billing", action="add")
        b, created = S.create_bill(request, json_body(request))
        return ok({"bill": S.bill_json(_bill_qs(request).get(pk=b.pk)), "created": created}, status=201 if created else 200)
    qs = _bill_qs(request)
    if request.GET.get("member_id"):
        qs = qs.filter(Q(member_id=request.GET["member_id"]) | Q(memberships__member_id=request.GET["member_id"])).distinct()
    if request.GET.get("from"):
        qs = qs.filter(created_at__date__gte=S.parse_date(request.GET["from"], "from"))
    if request.GET.get("to"):
        qs = qs.filter(created_at__date__lte=S.parse_date(request.GET["to"], "to"))
    if request.GET.get("q"):
        q = request.GET["q"].strip()
        qs = qs.filter(Q(receipt_no__icontains=q) | Q(member__name__icontains=q) | Q(member__phone__contains=q))
    items, page = paginate(request, qs)
    rows = [S.bill_json(b, with_links=False) for b in items]
    if request.GET.get("payment_status"):
        rows = [r for r in rows if r["payment_status"] == request.GET["payment_status"]]
    return ok({"bills": rows, "pagination": page})


@endpoint("GET")
@staff_required("billing", "view")
def bill_detail(request, bill_id):
    b = S.get_in_gym(Bill, request.client, bill_id, "Bill")
    return ok({"bill": S.bill_json(_bill_qs(request).get(pk=b.pk))})


@endpoint("POST")
@staff_required("billing", "add")
def bill_payment(request, bill_id):
    b = S.get_in_gym(Bill, request.client, bill_id, "Bill")
    S.collect_payment(request, b, json_body(request))
    return ok({"bill": S.bill_json(_bill_qs(request).get(pk=b.pk))})


@endpoint("POST")
@staff_required("billing", "delete")
def bill_cancel(request, bill_id):
    b = S.get_in_gym(Bill, request.client, bill_id, "Bill")
    if b.status == Bill.STATUS_CANCELLED:
        raise ApiError(400, "already_cancelled", "This bill is already cancelled.")
    S.cancel_bill(request, b, json_body(request))
    return ok({"bill": S.bill_json(_bill_qs(request).get(pk=b.pk), with_links=False)})


@endpoint("GET")
@staff_required("billing", "view")
def bill_receipt(request, bill_id):
    """Printable receipt (HTML). The apps print it or save it as PDF."""
    from django.shortcuts import render
    b = S.get_in_gym(Bill, request.client, bill_id, "Bill")
    data = S.bill_json(_bill_qs(request).get(pk=b.pk))
    from .receipt import receipt_view
    return render(request, "gym_core/receipt.html", {"r": receipt_view(data, GymSettings.for_client(request.client))})


# --- memberships: freeze, sessions ---------------------------------------------------------------

def _membership(request, membership_id, module="members"):
    ms = S.get_in_gym(Membership, request.client, membership_id, "Membership")
    S.require_member_in_scope(request.staff, ms.member, module)
    return ms


@endpoint("POST")
@staff_required("members", "edit")
def membership_freeze(request, membership_id):
    ms = _membership(request, membership_id)
    S.freeze(request, ms, json_body(request))
    return ok({"member": S.member_json(ms.member, detail=True)})


@endpoint("POST")
@staff_required("members", "edit")
def membership_unfreeze(request, membership_id, freeze_id):
    ms = _membership(request, membership_id)
    f = Freeze.objects.filter(membership=ms, pk=freeze_id).first()
    if f is None:
        raise ApiError(404, "not_found", "Freeze not found.")
    S.unfreeze(request, ms, f, json_body(request))
    return ok({"member": S.member_json(ms.member, detail=True)})


@endpoint("POST")
@staff_required("attendance", "add")
def membership_session(request, membership_id):
    ms = _membership(request, membership_id, "attendance")
    S.log_session(request, ms, json_body(request))
    return ok({"member": S.member_json(ms.member, detail=True)})


# --- check-in ------------------------------------------------------------------------------------

@endpoint("GET")
@staff_required("attendance", "add")
def checkin_lookup(request):
    return ok({"results": S.find_member_for_checkin(request, request.GET.get("q"))})


@endpoint("GET", "POST")
@staff_required("attendance", "view")
def checkins(request):
    if request.method == "POST":
        if not has_perm(request.staff, "attendance", "add"):
            raise ApiError(403, "permission_denied", "Your role can't check members in.", module="attendance", action="add")
        c, created = S.check_in(request, json_body(request))
        if created:
            S.log(request, "member_checked_in", c.member.name, source=c.source)
        return ok({"checkin": S.checkin_json(c), "created": created, "already_checked_in": not created},
                  status=201 if created else 200)
    on = S.parse_date(request.GET.get("date"), "date", S.today_for(request.client))
    members = S.scoped_members(request.staff, "attendance")
    qs = CheckIn.objects.filter(client=request.client, local_date=on, member__in=members).select_related("member", "by_user", "device")
    items, page = paginate(request, qs, default_size=50)
    return ok({"date": on.isoformat(), "total": qs.count(), "checkins": [S.checkin_json(c) for c in items], "pagination": page})


def test_console(request):
    """Old Phase 3A test page: now part of the single test app."""
    from django.shortcuts import redirect
    return redirect("/api/v1/test-console/app/")


def test_app(request):
    """Local test app (gym + operations in one page, light/dark theme).
    Routed only when DEBUG=True."""
    from django.shortcuts import render
    return render(request, "gym_core/app.html")
