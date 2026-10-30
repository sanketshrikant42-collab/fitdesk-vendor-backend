"""Pure calculation rules, ported from FitDesk Pro v1.2.0
(ui/membership/membership_transaction.py). No database access here, so
every rule is tested on its own against v1.2.0's own examples.

Differences from v1.2.0 are marked FIX and listed in README_PHASE3A.md.
"""

import calendar
import re
import urllib.parse
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

STATUS_PENDING, STATUS_PARTIAL, STATUS_PAID = "Pending", "Partially Paid", "Paid"


def money(x):
    """Round to paise, half-up (what people expect on a receipt)."""
    return Decimal(str(x if x is not None else 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --- dates -------------------------------------------------------------------

_MONTHS_BY_DAYS = {30: 1, 31: 1, 90: 3, 91: 3, 180: 6, 181: 6, 365: 12, 366: 12}


def plan_months(duration_days, plan_name=""):
    """v1.2.0 plan_months(): standard durations follow calendar months."""
    d = int(duration_days or 0)
    if d in _MONTHS_BY_DAYS:
        return _MONTHS_BY_DAYS[d]
    name = str(plan_name or "").lower()
    if "monthly" in name or "month" in name:
        return 1
    if "quarter" in name or "3 month" in name:
        return 3
    if "half" in name or "6 month" in name:
        return 6
    if "year" in name or "12 month" in name:
        return 12
    return None


def add_calendar_months(d, months):
    """v1.2.0 add_calendar_months(): clamps 31 Jan + 1 month to 28/29 Feb."""
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def expiry_date(start, duration_days, plan_name=""):
    """v1.2.0 calculate_expiry(): Monthly from 21/08 ends 21/09."""
    months = plan_months(duration_days, plan_name)
    if months is not None:
        return add_calendar_months(start, months)
    return start + timedelta(days=int(duration_days or 1))


def renewal_start(previous_end):
    """v1.2.0 renew_membership(): the new period starts the day after the
    previous one ends."""
    return previous_end + timedelta(days=1)


# --- money ---------------------------------------------------------------------

def bill_totals(fee, discount, gst_applied, gst_percent):
    """v1.2.0 calculate_remaining(): GST is added ON TOP of (fee - discount)."""
    fee, discount = money(fee), money(discount)
    if fee < 0 or discount < 0:
        raise ValueError("Fee and discount can't be negative.")
    if discount > fee:
        raise ValueError("Discount can't be more than the fee.")
    net = fee - discount
    gst = money(net * Decimal(str(gst_percent)) / HUNDRED) if gst_applied else ZERO
    return {"net": net, "gst_amount": gst, "total": net + gst}


def bill_totals_inclusive(fee, discount, gst_percent):
    """For plans whose price already includes GST: the member pays
    fee - discount, and the GST inside it is taken out:
    taxable = paid x 100 / (100 + GST%), GST = paid - taxable."""
    fee, discount = money(fee), money(discount)
    if fee < 0 or discount < 0:
        raise ValueError("Fee and discount can't be negative.")
    if discount > fee:
        raise ValueError("Discount can't be more than the fee.")
    total = fee - discount
    net = money(total * HUNDRED / (HUNDRED + Decimal(str(gst_percent))))
    return {"net": net, "gst_amount": total - net, "total": total}


def payment_status(total, paid):
    """FIX: v1.2.0 compared the amount paid with the fee WITHOUT GST, so a
    bill with GST could show "Paid" while GST was still owed. Here the
    status follows what is actually still due."""
    total, paid = money(total), money(paid)
    if paid <= 0:
        return STATUS_PENDING
    if paid >= total:
        return STATUS_PAID
    return STATUS_PARTIAL


def remaining(total, paid):
    """v1.2.0: remaining = total - paid, never below zero."""
    return max(money(total) - money(paid), ZERO)


def trainer_gym_share(amount, is_pt, share_percent, net, total, includes_gst=True):
    """v1.2.0 calculate_trainer_gym_share(): for PT plans the trainer gets
    share% of the amount received in this payment; the gym gets the rest.
    v1.2.0 applies the % to the amount INCLUDING GST (includes_gst=True,
    the default). With includes_gst=False only the pre-GST part counts."""
    amount = money(amount)
    if not is_pt:
        return ZERO, amount
    base = amount
    if not includes_gst and money(total) > 0:
        base = money(amount * money(net) / money(total))
    trainer = money(base * Decimal(str(share_percent or 0)) / HUNDRED)
    return trainer, amount - trainer


# --- codes ----------------------------------------------------------------------

def code_prefix(gym_name):
    """Same as v1.2.0 derive_employee_code_prefix(): "P&P Fitness" -> "PPF",
    "Momentum" -> "MOM". FIX: v1.2.0 hard-codes "PPF" for member codes in
    every gym; here each gym gets its own prefix."""
    fragments = [f for f in re.split(r"[^A-Za-z0-9]+", str(gym_name or "")) if f]
    letters = "".join(f[0] for f in fragments).upper()
    if len(letters) >= 2:
        return letters[:4]
    word = fragments[0] if fragments else ""
    return "".join(ch for ch in word.upper() if ch.isalpha())[:3] or "GYM"


def member_code(prefix, n):
    return f"{prefix}{n:06d}"           # v1.2.0: PPF000001



def staff_code(prefix, n):
    """Staff codes look different from member codes but carry the gym's
    prefix (decided 28 Oct 2026): PPF-S001. Members stay PPF000001."""
    return f"{prefix}-S{n:03d}"


def trainer_code(prefix, n):
    """Trainer records: PPF-T001 (v1.2.0 used TRN-001 for every gym)."""
    return f"{prefix}-T{n:03d}"

def receipt_no(prefix, n):
    return f"{prefix}{n:05d}"           # v1.2.0: RCPT00001


# --- messages and links -----------------------------------------------------------

DEFAULT_WHATSAPP_RECEIPT = (
    "🎉 *Welcome to {gym_name}!*\n\n"
    "Hi {member_name},\n\n"
    "Thank you for choosing *{gym_name}*. Your membership payment has been successfully received.\n\n"
    "🧾 Receipt No: {receipt_no}\n"
    "🏋️ Plan: {plan_name}\n"
    "📅 Valid From: {joining_date}\n"
    "📅 Valid Until: {expiry_date}\n"
    "💰 Amount Paid: {amount_paid}\n\n"
    "Thank you for choosing {gym_name}. We wish you a great fitness journey! 💪\n\n"
    "Regards,\n*{gym_name}*\n\n"
    "_Powered by FitDesk Pro 🚀_\n"
    "_Smart Gym Management Solution_"
)                                       # v1.2.0 DEFAULT_WHATSAPP_TEMPLATE


def rupees(x):
    return f"₹ {money(x):,.2f}"         # v1.2.0 money()


def render_template(template, context):
    out = template or ""
    for k, v in context.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def whatsapp_link(phone, text):
    """Tap-to-send link: opens the gym's own WhatsApp with the message
    filled in. Used until the gym's Meta account is verified."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"https://wa.me/{digits}?text={urllib.parse.quote(text)}"


def upi_link(upi_id, payee_name, amount, note):
    """Standard UPI payment link (works in GPay, PhonePe, Paytm, BHIM).
    Shown as a QR at the counter and inside reminder messages."""
    if not upi_id:
        return ""
    params = {"pa": upi_id, "pn": payee_name or "", "am": f"{money(amount):.2f}", "cu": "INR", "tn": note[:50]}
    return "upi://pay?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
