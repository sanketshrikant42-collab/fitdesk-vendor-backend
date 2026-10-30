"""Diet plan as a PDF, in the same style as the receipt PDF (same header
with the gym's logo, font, colours). Shows the version of the plan the
member was given: every meal and food with its amount and calories,
protein, carbs and fat, the day's totals, and the instructions."""
from datetime import date
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from .pdf import (ACCENT_D, BOLD, INK, K, LINE, MUTED, REG, SEMI, W, X0, X1, Pen, _register, draw_header,
                  gym_header)

PANEL = HexColor("#F6F7FB")
BORDER = HexColor("#E4E7EC")
NUTR = (("kcal", "kcal"), ("protein_g", "Protein g"), ("carbs_g", "Carbs g"), ("fat_g", "Fat g"))


def _d(s):
    return date.fromisoformat(str(s)[:10]).strftime("%d %b %Y").lstrip("0") if s else ""


def _n(v, key):
    if v is None:
        return "—"
    return f"{round(v):,}" if key == "kcal" else f"{round(v, 1):g}"


def _amount(it):
    if it.get("portion"):
        return it["portion"]
    q = it.get("quantity")
    if q in (None, ""):
        return ""
    return f"{float(q):g} {it.get('unit') or ''}".strip()


def diet_plan_pdf(plan, s):
    """plan = gym_ops.services.diet_plan_json(p, version) (with nutrition);
    s = GymSettings."""
    _register()
    v, n = plan["version"], plan["nutrition"]
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Diet plan - {plan['member']['name']}")
    c.setAuthor(s.gym_name)
    p = Pen(c)
    muted, body, small = 12 * K, 13 * K, 11 * K

    gym = gym_header(s)
    gym["gstin"] = ""
    draw_header(c, p, s, gym, "Diet plan", f"Version {v['version_no']}", _d(v["created_at"]))
    p.y -= 16 * K + body

    # member and plan
    name, nsize = plan["member"]["name"], 16 * K
    p.y -= nsize - body
    p.text(X0, name, BOLD, nsize, INK)
    p.text(X0 + pdfmetrics.stringWidth(name, BOLD, nsize) + 4, f"· {plan['member']['code']} · {plan['member']['phone']}",
           REG, muted, MUTED)
    p.y -= 10 * K
    info = [("Plan", plan["name"]), ("Goal", " · ".join(x for x in (plan.get("goal"), plan.get("diet_type")) if x))]
    if plan.get("start_date") or plan.get("end_date"):
        info.append(("Period", f"{_d(plan.get('start_date'))} → {_d(plan.get('end_date'))}".strip(" →")))
    if plan.get("review_date"):
        info.append(("Next review", _d(plan["review_date"])))
    if plan.get("trainer"):
        info.append(("Trainer", plan["trainer"]["name"]))
    for label, value in info:
        if not value:
            continue
        p.y -= body * 1.55
        p.text(X0, label, REG, body, MUTED)
        for i, part in enumerate(simpleSplit(value, REG, body, W - 110 * K) or [""]):
            if i:
                p.y -= body * 1.3
            p.text(X0 + 110 * K, part, REG, body, INK)
    p.y -= 16 * K

    # day totals: four boxes
    d = n["day"]
    gap, bh = 10 * K, 54 * K
    bw = (W - 3 * gap) / 4
    p.need(bh + 10)
    for i, (key, label) in enumerate((("kcal", "Calories / day"), ("protein_g", "Protein"), ("carbs_g", "Carbs"), ("fat_g", "Fat"))):
        x = X0 + i * (bw + gap)
        c.setFillColor(PANEL)
        c.setStrokeColor(BORDER)
        c.setLineWidth(1 * K)
        c.roundRect(x, p.y - bh, bw, bh, 10 * K, stroke=1, fill=1)
        c.setFillColor(MUTED)
        c.setFont(REG, small)
        c.drawString(x + 10 * K, p.y - 18 * K, label)
        c.setFillColor(INK)
        c.setFont(BOLD, 18 * K)
        c.drawString(x + 10 * K, p.y - 42 * K, _n(d[key], key) + ("" if key == "kcal" else " g"))
    p.y -= bh + 8 * K
    if not n["complete"]:
        p.y -= small
        p.text(X0, f"{n['counted']} of {n['items']} foods have values, so the totals are partly counted.", REG, small, MUTED)
    p.y -= 8 * K

    # meals
    cols = [("Food", 196), ("Amount", 132), ("kcal", 56), ("Protein (g)", 62), ("Carbs (g)", 60), ("Fat (g)", 58)]   # = 564 px, the full width
    xs, x = [], X0
    for _, wpx in cols:
        xs.append(x)
        x += wpx * K
    widths = [wpx * K for _, wpx in cols]
    for meal in n["meals"]:
        p.need(70 * K)
        p.y -= 20 * K
        c.setFillColor(PANEL)
        c.roundRect(X0, p.y - 9 * K, W, 26 * K, 7 * K, stroke=0, fill=1)
        p.y += 0.5 * K
        title = meal["name"] + (f"  ·  {meal['time']}" if meal.get("time") else "")
        p.text(X0 + 10 * K, title, BOLD, body, INK)
        if meal["items"]:
            t = meal["totals"]
            p.text(X1 - 10 * K, f"{_n(t['kcal'], 'kcal')} kcal · P {_n(t['protein_g'], 'p')} g · C {_n(t['carbs_g'], 'c')} g · "
                   f"F {_n(t['fat_g'], 'f')} g", REG, small, MUTED, "right")
        p.y -= 24 * K
        for (lab, _), xx, ww in zip(cols, xs, widths):
            if lab in ("Food", "Amount"):
                p.text(xx + 2, lab.upper(), SEMI, 9 * K, MUTED)
            else:
                p.text(xx + ww - 2, lab.upper(), SEMI, 9 * K, MUTED, "right")
        p.y -= 6 * K
        c.setStrokeColor(LINE)
        c.setLineWidth(1 * K)
        c.line(X0, p.y, X1, p.y)
        for it in meal["items"]:
            food = it["food_name"] + (" *" if it.get("manual") else "")
            fl = simpleSplit(food, REG, body, widths[0] - 6) or [""]
            al = simpleSplit(_amount(it), REG, small, widths[1] - 6) or [""]
            lines = max(len(fl), len(al))
            h = 7 * K * 2 + body * 1.25 * lines
            if p.y - h < 48:
                c.showPage()
                p.y = A4[1] - 48
            p.y -= 7 * K + body
            top = p.y
            for k, part in enumerate(fl):
                p.y = top - k * body * 1.25
                p.text(xs[0] + 2, part, REG, body, INK)
            for k, part in enumerate(al):
                p.y = top - k * body * 1.25
                p.text(xs[1] + 2, part, REG, small, MUTED)
            p.y = top
            calc = it.get("calc") or {}
            for (key, _), xx, ww in zip(NUTR, xs[2:], widths[2:]):
                p.text(xx + ww - 2, _n(calc.get(key), key), REG, body, INK, "right")
            p.y = top - body * 1.25 * (lines - 1) - 7 * K - body * 0.25
            c.setStrokeColor(LINE)
            c.line(X0, p.y, X1, p.y)
        p.y -= 6 * K

    if any(it.get("manual") for m in n["meals"] for it in m["items"]):
        p.y -= small * 1.6
        p.text(X0, "* values entered by the gym", REG, small, MUTED)

    if v.get("instructions"):
        p.need(60 * K)
        p.y -= 22 * K
        p.text(X0, "Instructions", BOLD, body, ACCENT_D)
        for para in v["instructions"].split("\n"):
            for part in (simpleSplit(para, REG, body, W) if para.strip() else [""]):
                p.need(body * 1.6)
                p.y -= body * 1.45
                p.text(X0, part, REG, body, INK)

    p.need(muted * 5)
    p.y -= 22 * K
    for line in simpleSplit("Calories and nutrients are approximate and depend on how the food is cooked. Follow your "
                            "trainer's advice, and tell the gym about any health condition or allergy.", REG, small, W):
        p.y -= small * 1.4
        p.text(X0, line, REG, small, MUTED)
    p.y -= 14 * K + muted
    p.text(A4[0] / 2, "Powered by FitDesk Pro", REG, muted, MUTED, "center")
    c.showPage()
    c.save()
    return buf.getvalue()
