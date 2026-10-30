"""Phase 3C reports. Every report returns the same shape so the apps can
show any of them and the Excel export can write any of them:

    {"key", "title", "from", "to", "notes": [..],
     "summary": [{"label", "value", "type"}],
     "tables": [{"name", "columns": [{"key", "label", "type"}], "rows": [{key: value}]}]}

type is one of text, int, money, pct, date. Money is a string ("1500.00").
"""
from collections import defaultdict, OrderedDict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum

from gym_core import rules as R
from gym_core.models import Bill, Membership, Payment, SessionLog, Trainer

from .services import gym_tz

M = lambda x: str(R.money(x or 0))                              # noqa: E731


def col(key, label, typ="text"):
    return {"key": key, "label": label, "type": typ}


def stat(label, value, typ="text"):
    return {"label": label, "value": value, "type": typ}


def _bounds(client, frm, to):
    tz = gym_tz(client)
    return datetime.combine(frm, time.min, tzinfo=tz), datetime.combine(to + timedelta(days=1), time.min, tzinfo=tz), tz


def _months(frm, to):
    out, y, m = [], frm.year, frm.month
    while (y, m) <= (to.year, to.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def _pct(a, b):
    return round(100 * a / b, 1) if b else 0


def _base(key, title, frm, to):
    return {"key": key, "title": title, "from": frm.isoformat(), "to": to.isoformat(), "notes": [], "summary": [], "tables": []}


# --- 1. collections -------------------------------------------------------------------------------

def collections(client, frm, to, group="day"):
    out = _base("collections", "Collections", frm, to)
    pays = Payment.objects.filter(bill__client=client, bill__status=Bill.STATUS_ACTIVE, paid_on__gte=frm, paid_on__lte=to)
    modes = [r["mode"] for r in pays.values("mode").annotate(s=Sum("amount")).order_by("-s")]
    rows = OrderedDict()
    keyf = (lambda d: d.strftime("%Y-%m")) if group == "month" else (lambda d: d.isoformat())
    for p in pays.order_by("paid_on").values("paid_on", "mode", "amount"):
        k = keyf(p["paid_on"])
        r = rows.setdefault(k, {"period": k, "count": 0, "total": Decimal(0), **{f"m_{m}": Decimal(0) for m in modes}})
        r["count"] += 1
        r["total"] += p["amount"]
        r[f"m_{p['mode']}"] += p["amount"]
    for r in rows.values():
        for k in list(r):
            if isinstance(r[k], Decimal):
                r[k] = M(r[k])
    total = pays.aggregate(s=Sum("amount"), n=Count("id"))
    days = (to - frm).days + 1
    out["summary"] = [stat("Collected", M(total["s"]), "money"), stat("Payments", total["n"] or 0, "int"),
                      stat("Average per day", M((total["s"] or 0) / days), "money")]
    out["summary"] += [stat(m, M(pays.filter(mode=m).aggregate(s=Sum("amount"))["s"]), "money") for m in modes]
    out["tables"].append({"name": "By month" if group == "month" else "By day",
                          "columns": [col("period", "Month" if group == "month" else "Date", "text" if group == "month" else "date"),
                                      col("count", "Payments", "int"), col("total", "Total", "money")]
                          + [col(f"m_{m}", m, "money") for m in modes], "rows": list(rows.values())})
    by_plan = (pays.values("bill__plan__name").annotate(s=Sum("amount"), n=Count("bill", distinct=True)).order_by("-s"))
    out["tables"].append({"name": "By plan", "columns": [col("plan", "Plan"), col("bills", "Bills", "int"), col("total", "Collected", "money")],
                          "rows": [{"plan": r["bill__plan__name"], "bills": r["n"], "total": M(r["s"])} for r in by_plan]})
    return out


# --- 2. renewals ----------------------------------------------------------------------------------

def renewals(client, frm, to):
    out = _base("renewals", "Renewals", frm, to)
    ending = (Membership.objects.filter(client=client, status=Membership.STATUS_ACTIVE, member__is_archived=False,
                                        end_date__gte=frm, end_date__lte=to)
              .select_related("member", "plan").order_by("end_date"))
    rows, renewed = [], 0
    for m in ending:
        nxt = (Membership.objects.filter(member=m.member, status=Membership.STATUS_ACTIVE, start_date__gt=m.start_date)
               .select_related("plan").order_by("start_date").first())
        renewed += 1 if nxt else 0
        rows.append({"member": m.member.name, "code": m.member.code, "phone": m.member.phone, "plan": m.plan.name,
                     "ends": m.end_date.isoformat(), "renewed": "Yes" if nxt else "No",
                     "next": f"{nxt.plan.name} till {nxt.end_date.strftime('%d %b %Y')}" if nxt else "",
                     "member_id": str(m.member_id)})
    n = len(rows)
    out["summary"] = [stat("Memberships ending", n, "int"), stat("Renewed", renewed, "int"),
                      stat("Not renewed", n - renewed, "int"), stat("Renewal rate", _pct(renewed, n), "pct")]
    cols = [col("member", "Member"), col("code", "Code"), col("phone", "Mobile"), col("plan", "Plan"),
            col("ends", "Ends", "date"), col("renewed", "Renewed"), col("next", "Renewed to")]
    out["tables"].append({"name": "Not renewed (call list)", "columns": cols[:5], "rows": [r for r in rows if r["renewed"] == "No"]})
    out["tables"].append({"name": "All memberships ending", "columns": cols, "rows": rows})
    return out


# --- 3. trainers (PT) -----------------------------------------------------------------------------

def trainers(client, frm, to):
    out = _base("trainers", "Trainers & PT earnings", frm, to)
    pays = Payment.objects.filter(bill__client=client, bill__status=Bill.STATUS_ACTIVE, bill__is_personal_training=True,
                                  paid_on__gte=frm, paid_on__lte=to)
    money = {r["bill__trainer"]: r for r in pays.values("bill__trainer").annotate(a=Sum("amount"), t=Sum("trainer_share"), g=Sum("gym_share"))}
    sessions = dict(SessionLog.objects.filter(membership__client=client, session_date__gte=frm, session_date__lte=to)
                    .values("trainer").annotate(n=Count("id")).values_list("trainer", "n"))
    from gym_core.services import today_for
    now = today_for(client)
    today = to if to < now else now
    clients = dict(Membership.objects.filter(client=client, status=Membership.STATUS_ACTIVE, trainer__isnull=False,
                                             start_date__lte=today, end_date__gte=today)
                   .values("trainer").annotate(n=Count("member", distinct=True)).values_list("trainer", "n"))
    rows, tot = [], defaultdict(Decimal)
    for t in Trainer.objects.filter(client=client).order_by("name"):
        mo = money.get(t.id, {})
        if t.status != "active" and not mo and not sessions.get(t.id):
            continue
        r = {"trainer": t.name, "code": t.code, "clients": clients.get(t.id, 0), "sessions": sessions.get(t.id, 0),
             "pt_collected": M(mo.get("a")), "trainer_share": M(mo.get("t")), "gym_share": M(mo.get("g"))}
        for k in ("pt_collected", "trainer_share", "gym_share"):
            tot[k] += Decimal(r[k])
        rows.append(r)
    out["summary"] = [stat("PT money collected", M(tot["pt_collected"]), "money"),
                      stat("Trainers' share", M(tot["trainer_share"]), "money"), stat("Gym's share", M(tot["gym_share"]), "money"),
                      stat("PT sessions logged", sum(sessions.values()), "int")]
    out["tables"].append({"name": "By trainer", "columns": [col("trainer", "Trainer"), col("code", "Code"),
                          col("clients", "Current clients", "int"), col("sessions", "Sessions", "int"),
                          col("pt_collected", "PT collected", "money"), col("trainer_share", "Trainer's share", "money"),
                          col("gym_share", "Gym's share", "money")], "rows": rows})
    out["notes"].append("Shares are counted in the month the money was received, as in v1.2.0.")
    return out


# --- 4. income vs expenses --------------------------------------------------------------------------

def profit(client, frm, to):
    from gym_ops.models import Expense
    out = _base("profit", "Income vs expenses", frm, to)
    pays = Payment.objects.filter(bill__client=client, bill__status=Bill.STATUS_ACTIVE, paid_on__gte=frm, paid_on__lte=to)
    exps = Expense.objects.filter(client=client, is_deleted=False, status="Paid", date__gte=frm, date__lte=to)
    inc = defaultdict(Decimal)
    for p in pays.values("paid_on", "amount"):
        inc[p["paid_on"].strftime("%Y-%m")] += p["amount"]
    exp = defaultdict(Decimal)
    for e in exps.values("date", "amount"):
        exp[e["date"].strftime("%Y-%m")] += e["amount"]
    rows = []
    for mo in _months(frm, to):
        rows.append({"month": mo, "income": M(inc[mo]), "expenses": M(exp[mo]), "net": M(inc[mo] - exp[mo])})
    ti, te = sum(inc.values(), Decimal(0)), sum(exp.values(), Decimal(0))
    pending = Expense.objects.filter(client=client, is_deleted=False, status="Pending", date__gte=frm, date__lte=to).aggregate(s=Sum("amount"))["s"]
    out["summary"] = [stat("Income", M(ti), "money"), stat("Expenses paid", M(te), "money"), stat("Net", M(ti - te), "money"),
                      stat("Expenses still pending", M(pending), "money")]
    out["tables"].append({"name": "By month", "columns": [col("month", "Month"), col("income", "Income", "money"),
                          col("expenses", "Expenses", "money"), col("net", "Net", "money")], "rows": rows})
    cats = exps.values("category").annotate(s=Sum("amount"), n=Count("id")).order_by("-s")
    out["tables"].append({"name": "Expenses by category", "columns": [col("category", "Category"), col("count", "Entries", "int"),
                          col("total", "Amount", "money"), col("share", "Share", "pct")],
                          "rows": [{"category": c["category"], "count": c["n"], "total": M(c["s"]), "share": _pct(c["s"], te)} for c in cats]})
    out["notes"].append("Income is money received (payments), not bills raised. Pending expenses are not deducted.")
    return out


# --- 5. enquiries ---------------------------------------------------------------------------------

def enquiries(client, frm, to):
    from gym_ops.models import Enquiry
    out = _base("enquiries", "Enquiries & conversion", frm, to)
    qs = Enquiry.objects.filter(client=client, enquiry_date__gte=frm, enquiry_date__lte=to)
    if hasattr(Enquiry, "is_archived"):
        qs = qs.filter(is_archived=False)
    rows, n, conv = [], 0, 0
    for r in qs.values("source").annotate(n=Count("id"), c=Count("id", filter=Q(status="Converted")),
                                          l=Count("id", filter=Q(status="Lost"))).order_by("-n"):
        rows.append({"source": r["source"], "enquiries": r["n"], "joined": r["c"], "lost": r["l"],
                     "open": r["n"] - r["c"] - r["l"], "rate": _pct(r["c"], r["n"])})
        n, conv = n + r["n"], conv + r["c"]
    lost = list(qs.filter(status="Lost").exclude(lost_reason="").values("lost_reason").annotate(n=Count("id")).order_by("-n")[:10])
    out["summary"] = [stat("Enquiries", n, "int"), stat("Joined", conv, "int"), stat("Conversion", _pct(conv, n), "pct"),
                      stat("Referrals", qs.filter(referred_by__isnull=False).count(), "int")]
    out["tables"].append({"name": "By source", "columns": [col("source", "Source"), col("enquiries", "Enquiries", "int"),
                          col("joined", "Joined", "int"), col("lost", "Lost", "int"), col("open", "Still open", "int"),
                          col("rate", "Conversion", "pct")], "rows": rows})
    out["tables"].append({"name": "Why enquiries were lost", "columns": [col("reason", "Reason"), col("count", "Enquiries", "int")],
                          "rows": [{"reason": r["lost_reason"], "count": r["n"]} for r in lost]})
    return out


# --- 6. GST report + Tally sales register -----------------------------------------------------------

def gst(client, frm, to):
    from gym_core.models import GymSettings
    out = _base("gst", "GST report & sales register", frm, to)
    start, end, tz = _bounds(client, frm, to)
    bills = (Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE, created_at__gte=start, created_at__lt=end)
             .select_related("member", "plan").annotate(p=Sum("payments__amount")).order_by("created_at"))
    gs = GymSettings.for_client(client)
    gst_rows, reg, tot = [], [], defaultdict(Decimal)
    for b in bills:
        d = b.created_at.astimezone(tz).date().isoformat()
        taxable = b.total - b.gst_amount
        half = R.money(b.gst_amount / 2)
        cgst, sgst = half, b.gst_amount - half
        paid = b.p or Decimal(0)
        reg.append({"date": d, "voucher_type": "Sales", "voucher_no": b.receipt_no, "party": b.member.name,
                    "party_code": b.member.code, "ledger": f"Membership fees - {b.plan.name}", "taxable": M(taxable),
                    "gst_rate": float(b.gst_percent) if b.gst_applied else 0, "cgst": M(cgst if b.gst_applied else 0),
                    "sgst": M(sgst if b.gst_applied else 0), "total": M(b.total), "received": M(paid),
                    "balance": M(R.remaining(b.total, paid))})
        tot["sales"] += b.total
        if b.gst_applied and b.gst_amount > 0:
            gst_rows.append({"date": d, "receipt": b.receipt_no, "member": b.member.name, "plan": b.plan.name,
                             "taxable": M(taxable), "rate": float(b.gst_percent), "cgst": M(cgst), "sgst": M(sgst),
                             "gst": M(b.gst_amount), "total": M(b.total)})
            tot["taxable"] += taxable
            tot["cgst"] += cgst
            tot["sgst"] += sgst
        else:
            tot["exempt"] += b.total
    out["summary"] = [stat("Bills", len(reg), "int"), stat("Bills with GST", len(gst_rows), "int"),
                      stat("Taxable value", M(tot["taxable"]), "money"), stat("CGST", M(tot["cgst"]), "money"),
                      stat("SGST", M(tot["sgst"]), "money"), stat("Total GST", M(tot["cgst"] + tot["sgst"]), "money"),
                      stat("Bills without GST", M(tot["exempt"]), "money"), stat("Total billed", M(tot["sales"]), "money")]
    out["tables"].append({"name": "GST invoices", "columns": [col("date", "Date", "date"), col("receipt", "Receipt no."),
                          col("member", "Member"), col("plan", "Plan"), col("taxable", "Taxable value", "money"),
                          col("rate", "GST %", "pct"), col("cgst", "CGST", "money"), col("sgst", "SGST", "money"),
                          col("gst", "Total GST", "money"), col("total", "Invoice total", "money")], "rows": gst_rows})
    out["tables"].append({"name": "Sales register (for Tally)", "columns": [
        col("date", "Date", "date"), col("voucher_type", "Voucher type"), col("voucher_no", "Voucher no."),
        col("party", "Party name"), col("party_code", "Member code"), col("ledger", "Sales ledger"),
        col("taxable", "Taxable value", "money"), col("gst_rate", "GST %", "pct"), col("cgst", "CGST", "money"),
        col("sgst", "SGST", "money"), col("total", "Total", "money"), col("received", "Received", "money"),
        col("balance", "Balance", "money")], "rows": reg})
    out["notes"] += [f"GSTIN: {gs.gstin or 'not set in Settings'}. Bills are counted on the date they were made (invoice date); cancelled bills are left out.",
                     "GST is split equally into CGST and SGST (member and gym in the same state).",
                     "The sales register uses the column names Tally's Excel import expects; check ledger names match your Tally company before importing."]
    return out


REPORTS = {"collections": collections, "renewals": renewals, "trainers": trainers, "profit": profit,
           "enquiries": enquiries, "gst": gst}
