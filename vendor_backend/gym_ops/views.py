"""/api/v1/ endpoints for Phase 3B."""

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse

from gym_accounts.api import ApiError, endpoint, json_body, ok, paginate, staff_required
from gym_accounts.permissions import SCOPE_OWN, has_perm, scope_for
from gym_core import rules as R
from gym_core import services as C
from gym_core.models import Member

from . import diet_nutrition as N
from . import diet_starter_plans as SP
from . import services as S
from .models import (ConsentForm, DietPlan, DietTemplate, DietShare, Enquiry, Expense, Food, PayrollSettlement,
                     ProgressEntry, Staff, StaffAttendance)


def need(user, module, action, msg=None):
    if not has_perm(user, module, action):
        raise ApiError(403, "permission_denied", msg or "Your role can't do this.", module=module, action=action)


# =============================================================================
# STAFF, ATTENDANCE, PAYROLL
# =============================================================================

@endpoint("GET", "POST")
@staff_required("staff", "view")
def staff_list(request):
    if request.method == "POST":
        need(request.staff, "staff", "add")
        s = S.save_staff(request, json_body(request))
        return ok({"staff": S.staff_json(s, S.can_see_salaries(request.staff))}, status=201)
    qs = Staff.objects.filter(client=request.client).select_related("trainer", "staff_user")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(designation__icontains=q))
    sal = S.can_see_salaries(request.staff)
    return ok({"staff": [S.staff_json(s, sal) for s in qs], "salaries_visible": sal})


@endpoint("GET", "POST")
@staff_required("staff", "view")
def staff_detail(request, staff_id):
    s = C.get_in_gym(Staff, request.client, staff_id, "Staff")
    if request.method == "POST":
        data = json_body(request)
        salary_only = set(data) <= {"monthly_salary", "salary_basis"} and S.can_manage_salaries(request.staff)
        if not salary_only:      # someone given "can see & pay" may change just the salary
            need(request.staff, "staff", "edit")
        s = S.save_staff(request, data, s)
    sal = S.can_see_salaries(request.staff)
    out = S.staff_json(s, sal)
    month = S.month_param(request.GET.get("month"), request.client)
    out["attendance"] = S.attendance_month(s, month)
    if sal:
        out["payroll"] = S.calculate_payroll(request, s, month)
    if sal:
        out["advances"] = [{"id": a.id, "date": a.date.isoformat(), "amount": str(a.amount), "mode": a.mode,
                            "reference_no": a.reference_no, "remarks": a.remarks} for a in s.advances.order_by("-date")[:24]]
        out["settlements"] = [{"month": p.month, "total": str(p.total), "salary": str(p.salary_amount),
                               "pt_commission": str(p.pt_commission), "payment_date": p.payment_date.isoformat(),
                               "mode": p.mode} for p in s.settlements.all()[:24]]
    return ok({"staff": out})


@endpoint("GET", "POST")
@staff_required("staff", "view")
def staff_attendance(request):
    """GET ?date= → the daily grid. POST {rows:[…]} saves many at once.
    Marking needs attendance.edit (owner, manager)."""
    if request.method == "POST":
        need(request.staff, "attendance", "edit", "Only the owner or manager can mark staff attendance.")
        return ok({"saved": S.mark_attendance(request, json_body(request).get("rows"))})
    on = C.parse_date(request.GET.get("date"), "date", C.today_for(request.client))
    rows = {a.staff_id: a for a in StaffAttendance.objects.filter(staff__client=request.client, date=on)}
    staff = Staff.objects.filter(client=request.client, status="active")
    counts = {s: 0 for s in StaffAttendance.STATUSES}
    out = []
    for s in staff:
        a = rows.get(s.id)
        if a:
            counts[a.status] += 1
        out.append({"staff_id": s.id, "name": s.name, "code": s.code, "designation": s.designation,
                    "status": a.status if a else ("Weekly Off" if on.weekday() == 6 else None),
                    "inferred": a is None and on.weekday() == 6,
                    "check_in": a.check_in if a else "", "check_out": a.check_out if a else "", "remarks": a.remarks if a else ""})
    return ok({"date": on.isoformat(), "rows": out, "counts": counts})


def _salary_staff(request, staff_id):
    if not S.can_see_salaries(request.staff):
        raise ApiError(403, "permission_denied", "Only the owner (or someone the owner allows) can see payroll.",
                       module="salaries", action="view")
    return C.get_in_gym(Staff, request.client, staff_id, "Staff")


def _pay_staff(request, staff_id):
    if not S.can_manage_salaries(request.staff):
        raise ApiError(403, "permission_denied", "Only the owner (or someone the owner allows) can pay salaries.",
                       module="salaries", action="edit")
    return C.get_in_gym(Staff, request.client, staff_id, "Staff")


@endpoint("GET")
@staff_required("staff", "view")
def payroll_calc(request, staff_id):
    s = _salary_staff(request, staff_id)
    month = S.month_param(request.GET.get("month"), request.client)
    return ok({"payroll": S.calculate_payroll(request, s, month, C.dec(request.GET.get("deduction"), "deduction", 0))})


@endpoint("GET")
@staff_required("staff", "view")
def payroll_month(request):
    """Everyone's payroll for a month (owner's payroll screen)."""
    if not S.can_see_salaries(request.staff):
        raise ApiError(403, "permission_denied", "Only the owner can see payroll.", module="staff", action="edit")
    month = S.month_param(request.GET.get("month"), request.client)
    return ok({"month": month, "rows": [S.calculate_payroll(request, s, month) for s in
                                         Staff.objects.filter(client=request.client, status="active")]})


@endpoint("POST")
@staff_required("staff", "view")
def payroll_pay(request, staff_id):
    s = _pay_staff(request, staff_id)
    data = json_body(request)
    st = S.pay_payroll(request, s, S.month_param(data.get("month"), request.client), data)
    return ok({"settlement": {"month": st.month, "total": str(st.total), "salary": str(st.salary_amount),
                              "pt_commission": str(st.pt_commission), "payment_date": st.payment_date.isoformat()}})


@endpoint("POST")
@staff_required("staff", "view")
def salary_advance(request, staff_id):
    s = _pay_staff(request, staff_id)
    a = S.add_advance(request, s, json_body(request))
    return ok({"advance": {"id": a.id, "amount": str(a.amount), "date": a.date.isoformat()}}, status=201)


# =============================================================================
# EXPENSES
# =============================================================================

@endpoint("GET", "POST")
@staff_required("expenses", "view")
def expenses(request):
    if request.method == "POST":
        need(request.staff, "expenses", "add")
        return ok({"expense": S.expense_json(S.save_expense(request, json_body(request)))}, status=201)
    qs = Expense.objects.filter(client=request.client, is_deleted=False).select_related("created_by")
    g = request.GET
    if g.get("from"):
        qs = qs.filter(date__gte=C.parse_date(g["from"], "from"))
    if g.get("to"):
        qs = qs.filter(date__lte=C.parse_date(g["to"], "to"))
    for f, field in (("category", "category"), ("mode", "mode"), ("status", "status")):
        if g.get(f):
            qs = qs.filter(**{field: g[f]})
    if g.get("q"):
        qs = qs.filter(Q(title__icontains=g["q"]) | Q(paid_to__icontains=g["q"]) | Q(reference_no__icontains=g["q"]))
    from django.db.models import Sum
    total = qs.aggregate(s=Sum("amount"))["s"] or 0
    paid = qs.filter(status="Paid").aggregate(s=Sum("amount"))["s"] or 0
    items, page = paginate(request, qs)
    return ok({"expenses": [S.expense_json(e) for e in items], "pagination": page,
               "totals": {"total": str(R.money(total)), "paid": str(R.money(paid)), "pending": str(R.money(total - paid)), "count": qs.count()},
               "categories": Expense.CATEGORIES, "modes": Expense.MODES})


@endpoint("POST")
@staff_required("expenses", "edit")
def expense_update(request, expense_id):
    e = C.get_in_gym(Expense, request.client, expense_id, "Expense")
    return ok({"expense": S.expense_json(S.save_expense(request, json_body(request), e))})


@endpoint("POST")
@staff_required("expenses", "delete")
def expense_delete(request, expense_id):
    e = C.get_in_gym(Expense, request.client, expense_id, "Expense")
    e.is_deleted = True
    e.save(update_fields=["is_deleted"])
    C.log(request, "expense_deleted", e.title, amount=str(e.amount))
    return ok()


# =============================================================================
# ENQUIRIES
# =============================================================================

def _enq_qs(request):
    return Enquiry.objects.filter(client=request.client).select_related("interested_plan", "assigned_to", "referred_by",
                                                                       "converted_member")


@endpoint("GET", "POST")
@staff_required("enquiries", "view")
def enquiries(request):
    if request.method == "POST":
        need(request.staff, "enquiries", "add")
        e, matches = S.save_enquiry(request, json_body(request))
        return ok({"enquiry": S.enquiry_json(e, True), "existing_members_with_phone": matches}, status=201)
    qs = _enq_qs(request).filter(is_archived=request.GET.get("archived") == "1")
    g, on = request.GET, C.today_for(request.client)
    view = g.get("view") or "open"
    if view == "open":
        qs = qs.filter(status__in=Enquiry.OPEN)
    elif view == "due_today":
        qs = qs.filter(status__in=Enquiry.OPEN, next_follow_up_date=on)
    elif view == "overdue":
        qs = qs.filter(status__in=Enquiry.OPEN, next_follow_up_date__lt=on)
    elif view == "mine":
        qs = qs.filter(status__in=Enquiry.OPEN, assigned_to=request.staff)
    elif view in ("Converted", "Lost"):
        qs = qs.filter(status=view)
    if g.get("source"):
        qs = qs.filter(source=g["source"])
    if g.get("q"):
        qs = qs.filter(Q(name__icontains=g["q"]) | Q(phone__contains=g["q"]) | Q(code__icontains=g["q"]))
    items, page = paginate(request, qs)
    return ok({"enquiries": [S.enquiry_json(e) for e in items], "pagination": page,
               "stats": S.enquiry_stats(request, Enquiry.objects.filter(client=request.client)),
               "choices": {"statuses": Enquiry.STATUSES, "sources": Enquiry.SOURCES, "priorities": Enquiry.PRIORITIES,
                           "followup_modes": ["Call", "WhatsApp", "Visit", "SMS", "Email", "Other"]}})


@endpoint("GET", "POST")
@staff_required("enquiries", "view")
def enquiry_detail(request, enquiry_id):
    e = C.get_in_gym(Enquiry, request.client, enquiry_id, "Enquiry")
    if request.method == "POST":
        need(request.staff, "enquiries", "edit")
        e, _ = S.save_enquiry(request, json_body(request), e)
    return ok({"enquiry": S.enquiry_json(e, True)})


@endpoint("POST")
@staff_required("enquiries", "edit")
def enquiry_action(request, enquiry_id, action):
    e = C.get_in_gym(Enquiry, request.client, enquiry_id, "Enquiry")
    data = json_body(request)
    extra = {}
    if action == "followup":
        S.add_followup(request, e, data)
    elif action == "lost":
        S.mark_lost(request, e, data)
    elif action == "reopen":
        S.reopen(request, e, data)
    elif action == "convert":
        e, m, reward = S.convert(request, e, data)
        extra = {"member": C.member_json(m), "referral_reward": {"days": reward.days, "status": reward.status} if reward else None}
    elif action == "archive":
        need(request.staff, "enquiries", "delete")
        e.is_archived = bool(data.get("archived", True))
        e.save(update_fields=["is_archived", "updated_at"])
    else:
        raise ApiError(404, "not_found", "Unknown action.")
    e.refresh_from_db()
    return ok({"enquiry": S.enquiry_json(e, True), **extra})


@endpoint("GET")
@staff_required("members", "view")
def member_referrals(request, member_id):
    m = C.get_in_gym(Member, request.client, member_id, "Member")
    C.require_member_in_scope(request.staff, m)
    rows = m.referrals.all()
    return ok({"referrals": [{"name": e.name, "status": e.status, "date": e.enquiry_date.isoformat(),
                              "reward": {"days": e.reward.days, "status": e.reward.status} if hasattr(e, "reward") else None}
                             for e in rows]})


# =============================================================================
# DIET
# =============================================================================

def _diet_member(request, member_id):
    m = C.get_in_gym(Member, request.client, member_id, "Member")
    C.require_member_in_scope(request.staff, m, "diet")
    return m


def _diet_plan(request, plan_id):
    p = C.get_in_gym(DietPlan, request.client, plan_id, "Diet plan")
    C.require_member_in_scope(request.staff, p.member, "diet")
    return p


@endpoint("GET", "POST")
@staff_required("diet", "view")
def diet_foods(request):
    if request.method == "POST":
        if not S.can_manage_library(request.staff):
            raise ApiError(403, "permission_denied", "Only the owner or manager can edit the food library.", module="diet", action="edit")
        d = json_body(request)
        f = Food(client=request.client) if not d.get("id") else C.get_in_gym(Food, request.client, d["id"], "Food")
        f.name = C.text(d, "name", 120, required=True)
        for k in ("serving_qty", "kcal", "protein_g", "carbs_g", "fat_g"):
            if k in d:
                v = d[k]
                try:
                    v = float(v) if v not in (None, "") else None
                    assert v is None or 0 <= v <= 5000
                except (TypeError, ValueError, AssertionError):
                    raise ApiError(400, f"bad_{k}", f"{k} must be 0 to 5000.")
                setattr(f, k, v)
        for k, n in (("serving_unit", 20), ("allergens", 60), ("source_note", 200)):
            if k in d:
                setattr(f, k, C.text(d, k, n))
        if "is_active" in d:
            f.is_active = bool(d["is_active"])
        warn = N.macro_check(f.kcal, f.protein_g, f.carbs_g, f.fat_g)
        try:
            f.save()
        except Exception:
            raise ApiError(409, "food_exists", "A food with this name already exists.")
        return ok({"food": f.as_dict(), "warning": warn}, status=201)
    qs = Food.objects.filter(client=request.client)
    if request.GET.get("q"):
        qs = qs.filter(name__icontains=request.GET["q"])
    if request.GET.get("show", "active") == "active":
        qs = qs.filter(is_active=True)
    foods = [{**f.as_dict(), "builtin": False} for f in qs]
    if request.GET.get("builtin") == "1":
        # ?builtin=1 adds the built-in list (read-only); a gym food with the same name replaces it.
        from gym_ops.food_db import builtin_foods, norm
        own = {norm(f["name"]) for f in foods}
        q = (request.GET.get("q") or "").lower()
        foods += [f for f in builtin_foods() if norm(f["name"]) not in own and (not q or q in f["name"].lower())]
    return ok({"foods": foods, "allergen_tags": SP.ALLERGENS})


@endpoint("GET")
@staff_required("diet", "view")
def diet_starters(request):
    plans = SP.list_starter_plans(request.GET.get("goal"), request.GET.get("diet_type"))
    return ok({"starters": [{**{k: v for k, v in p.items() if k != "meals"}, "summary": SP.summary_line(p),
                             "meals": [{"name": m["name"], "time": m["time"],
                                        "items": [{"food": i["food"], "portion": i["portion"], "allergens": list(i["allergens"])}
                                                  for i in m["items"]]} for m in p["meals"]]} for p in plans]})


@endpoint("GET", "POST")
@staff_required("diet", "view")
def diet_templates(request):
    if request.method == "POST":
        if not S.can_manage_library(request.staff):
            raise ApiError(403, "permission_denied", "Only the owner or manager can edit templates.", module="diet", action="edit")
        d = json_body(request)
        t = DietTemplate(client=request.client, created_by=request.staff) if not d.get("id") else C.get_in_gym(DietTemplate, request.client, d["id"], "Template")
        if d.get("from_starter"):
            src = SP.get_starter_plan(d["from_starter"])
            if not src:
                raise ApiError(404, "not_found", "Starter plan not found.")
            t.name, t.goal, t.diet_type, t.weeks = d.get("name") or src["name"], src["goal"], src["diet_type"], src["weeks"]
            t.instructions, t.source_starter_key = src["instructions"], src["key"]
            t.meals = [{"name": m["name"], "time": m["time"], "items": [{"food_name": i["food"], "portion": i["portion"],
                                                                          "allergens": ",".join(i["allergens"])} for i in m["items"]]}
                       for m in src["meals"]]
        else:
            t.name = C.text(d, "name", 120, required=True)
            t.goal, t.diet_type = C.text(d, "goal", 30), C.text(d, "diet_type", 30)
            t.weeks = int(d["weeks"]) if str(d.get("weeks") or "").isdigit() else None
            t.instructions = C.text(d, "instructions", 4000)
            t.meals = S.clean_meals(d.get("meals") or [])
        if "is_archived" in d:
            t.is_archived = bool(d["is_archived"])
        t.save()
        return ok({"template": {"id": t.id, "name": t.name}}, status=201)
    qs = DietTemplate.objects.filter(client=request.client, is_archived=False)
    return ok({"templates": [{"id": t.id, "name": t.name, "goal": t.goal, "diet_type": t.diet_type, "weeks": t.weeks,
                              "meals": t.meals, "instructions": t.instructions} for t in qs]})


@endpoint("POST")
@staff_required("diet", "view")
def diet_calculate(request):
    """Live nutrition while building a plan (nothing saved)."""
    meals = S.clean_meals(json_body(request).get("meals") or [])
    by_id, by_name = S.food_index(request.client)
    return ok({"nutrition": N.calculate(meals, by_id, by_name)})


@endpoint("GET", "POST")
@staff_required("diet", "view")
def diet_plans(request):
    if request.method == "POST":
        need(request.staff, "diet", "add")
        d = json_body(request)
        m = _diet_member(request, d.get("member_id"))
        with transaction.atomic():
            if d.get("starter_key") or d.get("template_id"):
                p = S.assign_source(request, m, d)
            else:
                p = S.create_diet_plan(request, m, d, d.get("meals") or [], status=d.get("status") or "Draft")
            # replace_active: the new plan takes over from the member's current one(s)
            replaced = S.replace_active_plans(request, m, p) if d.get("replace_active") and p.status == "Active" else 0
        return ok({"plan": S.diet_plan_json(p), "replaced": replaced}, status=201)
    members = C.scoped_members(request.staff, "diet")
    qs = DietPlan.objects.filter(client=request.client, member__in=members).select_related("member", "trainer")
    g, on = request.GET, C.today_for(request.client)
    if g.get("member_id"):
        qs = qs.filter(member_id=g["member_id"])
    view = g.get("view")
    if view in DietPlan.STATUSES:
        qs = qs.filter(status=view)
    elif view == "review_due":
        qs = qs.filter(status="Active", review_date__lte=on)
    elif view != "all" and not g.get("member_id"):
        qs = qs.exclude(status="Archived")      # removed plans only show under "Removed"
    if g.get("q"):
        qs = qs.filter(Q(member__name__icontains=g["q"]) | Q(member__code__icontains=g["q"]) | Q(name__icontains=g["q"]))
    items, page = paginate(request, qs)
    return ok({"plans": [S.diet_plan_json(p, with_nutrition=False) for p in items], "pagination": page,
               "counts": {"active": qs.filter(status="Active").count(),
                          "review_due": qs.filter(status="Active", review_date__lte=on).count(),
                          "templates": DietTemplate.objects.filter(client=request.client, is_archived=False).count()}})


@endpoint("GET", "POST")
@staff_required("diet", "view")
def diet_plan_detail(request, plan_id):
    p = _diet_plan(request, plan_id)
    if request.method == "POST":
        need(request.staff, "diet", "edit")
        d = json_body(request)
        replace = bool(d.pop("replace_active", False))
        with transaction.atomic():
            if "status" in d and len(d) == 1:
                S.set_plan_status(request, p, d["status"])
            elif p.status == "Draft":
                S.update_draft(request, p, d, d.get("meals") or [])
                if d.get("status"):
                    S.set_plan_status(request, p, d["status"])
            else:
                p = S.revise_plan(request, p, d, d.get("meals") or [])
            p.refresh_from_db()
            if replace and p.status == "Active":
                S.replace_active_plans(request, p.member, p)
    v = request.GET.get("version")
    return ok({"plan": S.diet_plan_json(p, int(v) if v and v.isdigit() else None)})


@endpoint("POST")
@staff_required("diet", "view")
def diet_plan_share(request, plan_id):
    p = _diet_plan(request, plan_id)
    pdf = None
    try:                                     # PDF link for the member (Phase 3C)
        from gym_auto.views import diet_member_link
        url, name, local = diet_member_link(request, p)
        pdf = {"url": url, "filename": name, "local_only": local}
    except ImportError:
        pass
    with_pdf = json_body(request).get("include_pdf", True) is not False and pdf is not None
    msg = S.diet_share_text(p, pdf["url"] if with_pdf else None)
    DietShare.objects.create(plan=p, version_no=p.current_version, recipient=p.member.phone, by_user=request.staff)
    from gym_core import rules as R
    return ok({"whatsapp": {"text": msg, "link": R.whatsapp_link(p.member.phone, msg)}, "pdf": pdf})


# =============================================================================
# PROGRESS, CONSENT
# =============================================================================

def _progress_member(request, member_id, write=False):
    m = C.get_in_gym(Member, request.client, member_id, "Member")
    C.require_member_in_scope(request.staff, m)
    if write and not (has_perm(request.staff, "members", "edit") or scope_for(request.staff, "members") == SCOPE_OWN):
        raise ApiError(403, "permission_denied", "Your role can't record progress.", module="members", action="edit")
    return m


@endpoint("GET", "POST")
@staff_required("members", "view")
def member_progress(request, member_id):
    if request.method == "POST":
        m = _progress_member(request, member_id, write=True)
        if request.content_type and request.content_type.startswith("multipart/"):
            data, photo = request.POST.dict(), request.FILES.get("photo")
        else:
            data, photo = json_body(request), None
        e = S.add_progress(request, m, data, photo)
        return ok({"entry": S.progress_json(e)}, status=201)
    m = _progress_member(request, member_id)
    entries = list(m.progress.select_related("recorded_by"))
    fit = getattr(m, "fitness", None)
    return ok({"entries": [S.progress_json(e) for e in entries],
               "weight_series": [{"date": e.date.isoformat(), "weight_kg": str(e.weight_kg)} for e in entries if e.weight_kg is not None],
               "target_weight_kg": str(fit.target_weight_kg) if fit and fit.target_weight_kg else None})


@endpoint("GET")
@staff_required("members", "view")
def progress_photo(request, entry_id):
    e = ProgressEntry.objects.filter(member__client=request.client, pk=entry_id).select_related("member").first()
    if e is None or not e.photo:
        raise ApiError(404, "not_found", "Photo not found.")
    C.require_member_in_scope(request.staff, e.member)
    return FileResponse(e.photo.open("rb"))


@endpoint("GET", "POST")
@staff_required("members", "view")
def member_consent(request, member_id):
    m = C.get_in_gym(Member, request.client, member_id, "Member")
    C.require_member_in_scope(request.staff, m)
    if request.method == "POST":
        need(request.staff, "members", "edit")
        c = S.sign_consent(request, m, json_body(request))
        return ok({"consent": {"id": c.id, "signed_at": c.signed_at.isoformat(), "signed_name": c.signed_name}}, status=201)
    from gym_core.models import GymSettings
    st = GymSettings.for_client(request.client)
    return ok({"text": st.consent_text or S.DEFAULT_CONSENT,
               "signed": [{"id": c.id, "signed_at": c.signed_at.isoformat(), "signed_name": c.signed_name,
                           "health_answers": c.health_answers, "witnessed_by": c.witnessed_by.name if c.witnessed_by_id else None,
                           "signature_png": c.signature_png} for c in m.consents.select_related("witnessed_by")]})


# =============================================================================
# SUPPORT
# =============================================================================

@endpoint("GET", "POST")
@staff_required("support", "view")
def support(request):
    from licensing.models import SupportQuery
    if request.method == "POST":
        need(request.staff, "support", "add")
        q = S.raise_ticket(request, json_body(request))
        return ok({"ticket": {"id": q.id, "subject": q.subject, "status": q.status}}, status=201)
    qs = SupportQuery.objects.filter(client=request.client)
    if request.staff.role != "owner":
        qs = qs.filter(message__contains=f"— {request.staff.name} ({request.staff.role}) · {request.staff.phone}")
    return ok({"tickets": [{"id": q.id, "subject": q.subject, "status": q.status, "created_at": q.created_at.isoformat(),
                            "resolved_at": q.resolved_at.isoformat() if q.resolved_at else None} for q in qs[:50]]})


def test_console(request):
    # Old Phase 3B test page: now part of the single test app.
    from django.shortcuts import redirect
    return redirect("/api/v1/test-console/app/#staff")
