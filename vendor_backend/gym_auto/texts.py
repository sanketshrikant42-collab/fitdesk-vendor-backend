"""Built-in WhatsApp wording for the daily list. A gym can replace any of
them in Settings → Reminders. Placeholders in braces are filled in by the
server; unknown ones are left as typed."""

PLACEHOLDERS = {
    "name": "member's name",
    "gym": "gym name",
    "plan": "plan name",
    "end_date": "last day of the membership, e.g. 27 Nov 2026",
    "days": "days left / days since",
    "when": "\"in 7 days\", \"tomorrow\", \"today\"",
    "amount": "amount to pay, e.g. ₹ 1,500.00",
    "upi_id": "gym's UPI ID",
    "pay_line": "payment lines (amount + UPI ID + UPI link); empty when there's nothing to pay or no UPI ID",
}

RENEWAL = (
    "Hi {name} 👋\n"
    "Your *{plan}* membership at *{gym}* ends {when} (*{end_date}*).\n"
    "Renew now to keep training without a break 💪"
    "{pay_line}\n"
    "— {gym}"
)

EXPIRED = (
    "Hi {name},\n"
    "your *{plan}* membership at *{gym}* ended on *{end_date}*. We miss you at the gym!\n"
    "Renew today and get back on track 💪"
    "{pay_line}\n"
    "— {gym}"
)

DUES = (
    "Hi {name},\n"
    "a balance of *{amount}* is pending on your membership at *{gym}*."
    "{pay_line}\n"
    "Please ignore this if you've already paid. Thank you!\n"
    "— {gym}"
)

BIRTHDAY = (
    "Happy Birthday {name}! 🎉🎂\n"
    "Wishing you a healthy, strong year ahead. See you at the gym 💪\n"
    "— Team {gym}"
)

INACTIVE = (
    "Hi {name},\n"
    "we haven't seen you at *{gym}* for {days} days. Everything okay?\n"
    "Your membership is active till *{end_date}*. Come back and keep the progress going 💪\n"
    "— {gym}"
)

DEFAULTS = {"renewal": RENEWAL, "expired": EXPIRED, "dues": DUES, "birthday": BIRTHDAY, "inactive": INACTIVE}


def when_text(days):
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def pay_line(amount_text, upi_id, upi_link):
    if not upi_id or not amount_text:
        return ""
    out = f"\n\nPay *{amount_text}* by UPI to *{upi_id}*"
    if upi_link:
        out += f"\n{upi_link}"
    return out
