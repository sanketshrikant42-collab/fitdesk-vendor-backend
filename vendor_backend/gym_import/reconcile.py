"""After an import: compares the v1.2.0 database with what is now in the
new system, record by record and rupee by rupee. Every check is worked
out independently from both sides (the old numbers straight from the
v1.2.0 tables, the new numbers from the new tables), so a mistake in the
importer shows up as a difference here."""
from collections import Counter as Tally
from decimal import Decimal

from django.db.models import Sum

from gym_core import rules as R
from gym_core.models import Bill, Member, Membership, Payment, Plan, Trainer
from gym_core.services import dues_for, membership_state
from gym_ops.models import (DietPlan, DietPlanVersion, DietTemplate, Enquiry, EnquiryFollowUp, Expense,
                            PayrollSettlement, Staff, StaffAttendance, TrainerPayout)

from .importer import money, to_date

NOT_MOVED = {
    "users": "v1.2.0 logins. Everyone gets a new login by invite (owner first).",
    "role_permissions": "Replaced by the new roles and per-person permissions.",
    "audit_log": "Old activity log; stays in v1.2.0.",
    "communication_history": "Record of WhatsApp/email messages prepared in v1.2.0; stays in v1.2.0.",
    "whatsapp_messages": "Old WhatsApp send log; stays in v1.2.0.",
    "notification_states": "v1.2.0 alert states; the new daily list replaces them.",
    "notification_settings": "v1.2.0 alert setting; replaced by Settings → Reminders.",
    "trainer_members": "Trainer ↔ client links. In the new system a member's trainer comes from their PT bill.",
    "trainer_sessions": "PT session log. Not imported yet: tell Claude if this gym uses it.",
    "salary_records": "Imported as part of each payroll settlement (earned, advances, deduction, attendance counts).",
    "salary_payments": "Imported as part of each payroll settlement.",
}


def _sum(rows, key):
    return sum((money(r.get(key)) for r in rows), R.ZERO)


class Check:
    def __init__(self):
        self.rows = []      # (area, item, old, new, ok, note)

    def add(self, area, item, old, new, note="", ok=None):
        if isinstance(old, Decimal) or isinstance(new, Decimal):
            old, new = R.money(old or 0), R.money(new or 0)       # 48500 and 48500.00 are the same amount
        if ok is None:
            ok = str(old) == str(new)
        self.rows.append((area, item, old, new, ok, note))

    @property
    def problems(self):
        return [r for r in self.rows if not r[4]]


def reconcile(src, imp, today):
    c = Check()
    client = imp.client
    src_tx = src.rows("membership_transactions")

    # --- counts -------------------------------------------------------------------------------
    counts = [
        ("Members", "members", Member.objects.filter(client=client).count()),
        ("Plans", "membership_plans", Plan.objects.filter(client=client).count()),
        ("Bills (receipts)", "membership_transactions", Bill.objects.filter(client=client).count()),
        ("Trainers", "trainers", Trainer.objects.filter(client=client).count()),
        ("Staff", "staff", Staff.objects.filter(client=client).count()),
        ("Staff attendance days", "staff_attendance", StaffAttendance.objects.filter(staff__client=client).count()),
        ("Expenses", "expenses", Expense.objects.filter(client=client).count()),
        ("Enquiries", "enquiries", Enquiry.objects.filter(client=client).count()),
        ("Enquiry follow-ups", "enquiry_followups", EnquiryFollowUp.objects.filter(enquiry__client=client).count()),
        ("Diet plans", "diet_plans", DietPlan.objects.filter(client=client).count()),
        ("Diet plan versions", "diet_plan_versions", DietPlanVersion.objects.filter(plan__client=client).count()),
        ("Diet templates", "diet_templates", DietTemplate.objects.filter(client=client).count()),
        ("Trainer payouts", "trainer_payouts", TrainerPayout.objects.filter(client=client).count()),
        ("Payroll settlements", "payroll_settlements", PayrollSettlement.objects.filter(staff__client=client).count()),
    ]
    for label, table, new in counts:
        c.add("Counts", label, src.count(table), new)
    items_old = sum(1 for i in src.rows("diet_meal_items")
                    for m in src.rows("diet_meals") if m["id"] == i.get("meal_id") and m.get("plan_version_id"))
    items_new = sum(len(it["items"]) for v in DietPlanVersion.objects.filter(plan__client=client) for it in v.meals)
    c.add("Counts", "Foods in diet plans", items_old, items_new)

    # --- money --------------------------------------------------------------------------------
    live = [r for r in src_tx if (r.get("status") or "Active").lower() not in ("cancelled", "canceled", "void")]
    bills = Bill.objects.filter(client=client, status=Bill.STATUS_ACTIVE)
    old_billed = sum((money(r["original_fee"]) - money(r["discount"]) + (money(r["gst_amount"]) if r.get("is_gst_applied") else 0)
                      for r in live), R.ZERO)
    c.add("Money", "Total billed (fee − discount + GST)", old_billed, bills.aggregate(s=Sum("total"))["s"] or R.ZERO)
    old_paid = _sum(live, "amount_paid")
    new_paid = Payment.objects.filter(bill__in=bills).aggregate(s=Sum("amount"))["s"] or R.ZERO
    c.add("Money", "Total collected", old_paid, new_paid)
    old_dues = sum((max(money(r["original_fee"]) - money(r["discount"]) + (money(r["gst_amount"]) if r.get("is_gst_applied") else 0)
                        - money(r["amount_paid"]), R.ZERO) for r in live), R.ZERO)
    new_dues = sum((dues_for(m) for m in Member.objects.filter(client=client)), R.ZERO)
    c.add("Money", "Dues still to collect", old_dues, new_dues)
    c.add("Money", "Dues as v1.2.0 shows them (remaining amount)", _sum(live, "remaining_amount"), new_dues,
          note="Differs only if v1.2.0 left GST out of the balance.")
    for mode, amt in sorted(_by(live, "payment_mode", "amount_paid").items()):
        new = Payment.objects.filter(bill__in=bills, mode=mode).aggregate(s=Sum("amount"))["s"] or R.ZERO
        c.add("Money", f"Collected by {mode}", amt, new)
    c.add("Money", "Trainers' share (PT)", _sum(live, "trainer_share"),
          Payment.objects.filter(bill__in=bills).aggregate(s=Sum("trainer_share"))["s"] or R.ZERO)
    c.add("Money", "Gym's share", _sum(live, "gym_share"),
          Payment.objects.filter(bill__in=bills).aggregate(s=Sum("gym_share"))["s"] or R.ZERO)
    c.add("Money", "Expenses", _sum(src.rows("expenses"), "amount"),
          Expense.objects.filter(client=client).aggregate(s=Sum("amount"))["s"] or R.ZERO)
    c.add("Money", "Payroll paid (salary + PT)", _sum(src.rows("payroll_settlements"), "total_amount"),
          PayrollSettlement.objects.filter(staff__client=client).aggregate(s=Sum("total"))["s"] or R.ZERO)
    c.add("Money", "Trainer payouts", _sum(src.rows("trainer_payouts"), "amount"),
          TrainerPayout.objects.filter(client=client).aggregate(s=Sum("amount"))["s"] or R.ZERO)

    # --- every receipt ---------------------------------------------------------------------------
    newb = {b.receipt_no: b for b in Bill.objects.filter(client=client).select_related("member").prefetch_related("payments")}
    for r in src_tx:
        b = newb.get(r["receipt_no"])
        if b is None:
            c.add("Receipts", r["receipt_no"], "present", "missing")
            continue
        paid = sum((p.amount for p in b.payments.all()), R.ZERO)
        old = (f"{money(r['original_fee'])} − {money(r['discount'])} + GST "
               f"{money(r['gst_amount']) if r.get('is_gst_applied') else R.ZERO}; paid {money(r['amount_paid'])}")
        new = f"{b.fee} − {b.discount} + GST {b.gst_amount}; paid {paid}"
        ok = (money(r["original_fee"]) == b.fee and money(r["discount"]) == b.discount and money(r["amount_paid"]) == paid
              and to_date(r.get("expiry_date")) == b.memberships.first().end_date)
        c.add("Receipts", f"{r['receipt_no']} · {b.member.name}", old, new, ok=ok,
              note=f"valid {to_date(r.get('joining_date')):%d %b %Y} → {to_date(r.get('expiry_date')):%d %b %Y}")

    # --- every member: status, end date, dues --------------------------------------------------------
    by_member = {}
    for r in live:
        by_member.setdefault(r["member_id"], []).append(r)
    for r in src.rows("members"):
        m = imp.map["member"].get(r["id"])
        txs = by_member.get(r["id"], [])
        last = max((to_date(t.get("expiry_date")) for t in txs), default=None)
        first = min((to_date(t.get("joining_date")) for t in txs), default=None)
        if not txs:
            old_state = "no_plan"
        elif last and last >= today:
            old_state = "upcoming" if first and first > today and not any(
                to_date(t["joining_date"]) <= today <= to_date(t["expiry_date"]) for t in txs) else "active"
        else:
            old_state = "expired"
        st = membership_state(m, today)
        new_end = st["membership"].end_date if st["membership"] else None
        old_due = sum((max(money(t["original_fee"]) - money(t["discount"]) + (money(t["gst_amount"]) if t.get("is_gst_applied") else 0)
                           - money(t["amount_paid"]), R.ZERO) for t in txs), R.ZERO)
        new_due = dues_for(m)
        old_s = f"{old_state} · ends {last:%d %b %Y}" if last else old_state
        new_s = f"{st['state']} · ends {new_end:%d %b %Y}" if new_end else st["state"]
        c.add("Members", f"{m.code} · {m.name}", old_s + (f" · due ₹{old_due}" if old_due else ""),
              new_s + (f" · due ₹{new_due}" if new_due else ""),
              ok=(old_state == st["state"] and (last == new_end or (not last and not new_end)) and old_due == new_due),
              note=f"v1.2.0 status field: {r.get('status') or '—'}")

    # --- statuses ------------------------------------------------------------------------------------
    old_e = Tally(r.get("status") for r in src.rows("enquiries"))
    new_e = Tally(Enquiry.objects.filter(client=client).values_list("status", flat=True))
    for k in sorted(set(old_e) | set(new_e)):
        c.add("Enquiries", f"Status {k}", old_e.get(k, 0), new_e.get(k, 0))
    old_d = Tally(r.get("status") for r in src.rows("diet_plans"))
    new_d = Tally(DietPlan.objects.filter(client=client).values_list("status", flat=True))
    for k in sorted(set(old_d) | set(new_d)):
        c.add("Diet plans", f"Status {k}", old_d.get(k, 0), new_d.get(k, 0))
    return c


def _by(rows, key, amount_key):
    out = {}
    for r in rows:
        k = r.get(key) or "Cash"
        out[k] = out.get(k, R.ZERO) + money(r.get(amount_key))
    return {k: v for k, v in out.items() if v}


def not_moved(src):
    return [(t, src.count(t), why) for t, why in NOT_MOVED.items() if t in src.tables and src.count(t)]
