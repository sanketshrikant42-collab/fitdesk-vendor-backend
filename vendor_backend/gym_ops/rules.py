"""Payroll rule, ported from FitDesk Pro v1.2.0 PayrollDialog.calculate()
(ui/staff/staff.py). Pure function so it can be tested on its own."""

import calendar
from datetime import date


def month_bounds(month):
    """'2026-10' -> (2026, 10, days_in_month)."""
    y, m = (int(x) for x in month.split("-"))
    return y, m, calendar.monthrange(y, m)[1]


def payroll(monthly_salary, basis, month, today, joining_date, statuses, advance=0, deduction=0,
            pt_earned=0, pt_paid=0, holidays_paid=False):
    """statuses: {date: "Present"|"Half Day"|"Absent"|"Paid Leave"|"Weekly Off"|"Holiday"}.

    v1.2.0 rules:
    - daily rate = salary / days in month ("Calendar Days") or / 30 ("Fixed 30 Days");
    - paid days = Present + half of Half Day + Paid Leave + Weekly Off
      + every elapsed Sunday (since joining) with nothing marked;
    - earned rounded to whole rupees; net = earned - advance - deduction (min 0);
    - PT commission still unpaid for the month is added on top.
    holidays_paid=False matches v1.2.0, which leaves "Holiday" out of paid
    days even though its attendance screen says holidays aren't deducted.
    """
    y, m, days = month_bounds(month)
    salary = float(monthly_salary or 0)
    first = date(y, m, 1)
    this_month = date(today.year, today.month, 1)
    if first == this_month:
        elapsed = today.day
    elif first < this_month:
        elapsed = days
    else:
        elapsed = 0

    start_day = 1
    if joining_date:
        jfirst = date(joining_date.year, joining_date.month, 1)
        if jfirst == first:
            start_day = joining_date.day
        elif jfirst > first:
            start_day = elapsed + 1

    counts = {s: 0 for s in ("Present", "Half Day", "Paid Leave", "Absent", "Holiday", "Weekly Off")}
    marked = set()
    for d, s in statuses.items():
        if d.year == y and d.month == m:
            if s in counts:
                counts[s] += 1
            marked.add(d)

    inferred_sundays = sum(1 for day in range(start_day, elapsed + 1)
                           if date(y, m, day).weekday() == 6 and date(y, m, day) not in marked)
    # Working days so far that nobody marked: they earn nothing, so the
    # payroll screen warns about them before salary is paid.
    not_marked = sum(1 for day in range(start_day, elapsed + 1)
                     if date(y, m, day) not in marked and date(y, m, day).weekday() != 6)

    divisor = 30 if basis == "Fixed 30 Days" else days
    units = (counts["Present"] + counts["Half Day"] * 0.5 + counts["Paid Leave"]
             + counts["Weekly Off"] + inferred_sundays + (counts["Holiday"] if holidays_paid else 0))
    earned = round(salary / divisor * units) if divisor else 0           # whole rupees, as v1.2.0
    net = max(round(earned - float(advance or 0) - float(deduction or 0)), 0)
    pt_pending = max(float(pt_earned or 0) - float(pt_paid or 0), 0)
    total = round(net + pt_pending)
    return {"month": month, "days_in_month": days, "elapsed_days": elapsed, "divisor": divisor,
            "daily_rate": round(salary / divisor, 2) if divisor else 0, "counts": counts,
            "weekly_off_inferred": inferred_sundays, "not_marked": not_marked, "paid_days": units, "holidays_paid": holidays_paid,
            "earned": earned, "advance": float(advance or 0), "deduction": float(deduction or 0), "net": net,
            "pt_earned": round(float(pt_earned or 0), 2), "pt_paid": round(float(pt_paid or 0), 2),
            "pt_pending": round(pt_pending, 2), "total": total}
