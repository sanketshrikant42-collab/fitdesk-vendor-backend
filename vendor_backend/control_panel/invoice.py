"""Crestline's invoice for a gym's FitDesk Pro payment (A4 PDF).

Seller details come from environment variables so they can change
without a code change: CRESTLINE_ADDRESS, CRESTLINE_GSTIN (optional),
CRESTLINE_PHONE, CRESTLINE_EMAIL, CRESTLINE_WEBSITE.
"""
import io
import os
from decimal import Decimal

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from gym_auto.pdf import BOLD, REG, SEMI, _register

INK, MUTED, LINE, ACCENT = HexColor("#1D1B33"), HexColor("#5F5C78"), HexColor("#E2E0EE"), HexColor("#6D5DF6")


def seller():
    return {
        "name": "Crestline Technologies Private Limited",
        "address": os.environ.get("CRESTLINE_ADDRESS", ""),
        "gstin": os.environ.get("CRESTLINE_GSTIN", ""),
        "phone": os.environ.get("CRESTLINE_PHONE", "84840 74959"),
        "email": os.environ.get("CRESTLINE_EMAIL", ""),
        "website": os.environ.get("CRESTLINE_WEBSITE", "www.crestlinetechnologies.in"),
    }


def rupees(v):
    v = Decimal(v).quantize(Decimal("0.01"))
    whole, frac = f"{v:.2f}".split(".")
    neg = whole.startswith("-")
    whole = whole.lstrip("-")
    if len(whole) > 3:                                   # Indian grouping: 1,23,456
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{'-' if neg else ''}₹{whole}" + ("" if frac == "00" else f".{frac}")


def invoice_filename(p):
    safe = "".join(ch if ch.isalnum() or ch in " -" else " " for ch in p.client.gym_name).strip()
    return f"Invoice {p.invoice_no.replace('/', '-')} - {safe}.pdf"


def invoice_pdf(p, billing=None):
    _register()
    s = seller()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x0, x1 = 56, W - 56
    c.setTitle(f"Invoice {p.invoice_no}")

    c.setFillColor(ACCENT)
    c.roundRect(x0, H - 92, 34, 34, 8, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(BOLD, 16)
    c.drawCentredString(x0 + 17, H - 81, "C")
    c.setFillColor(INK)
    c.setFont(BOLD, 14)
    c.drawString(x0 + 46, H - 70, s["name"])
    c.setFont(REG, 9)
    c.setFillColor(MUTED)
    y = H - 84
    for line in [s["address"], " · ".join(x for x in (s["phone"], s["email"], s["website"]) if x),
                 f"GSTIN {s['gstin']}" if s["gstin"] else ""]:
        if line:
            c.drawString(x0 + 46, y, line)
            y -= 12

    c.setFillColor(INK)
    c.setFont(BOLD, 22)
    c.drawRightString(x1, H - 72, "INVOICE")
    c.setFont(REG, 10)
    c.setFillColor(MUTED)
    c.drawRightString(x1, H - 88, f"No. {p.invoice_no}")
    c.drawRightString(x1, H - 101, f"Date {p.paid_on:%d %b %Y}")

    y = min(y, H - 112) - 18
    c.setStrokeColor(LINE)
    c.line(x0, y, x1, y)
    y -= 24
    c.setFont(SEMI, 9)
    c.setFillColor(MUTED)
    c.drawString(x0, y, "BILL TO")
    y -= 16
    cl = p.client
    c.setFont(BOLD, 12)
    c.setFillColor(INK)
    c.drawString(x0, y, cl.gym_name)
    c.setFont(REG, 10)
    for line in [cl.owner_name, " · ".join(x for x in (cl.phone, cl.email) if x), cl.city,
                 f"GSTIN {billing.gstin}" if billing and billing.gstin else ""]:
        if line:
            y -= 14
            c.drawString(x0, y, line)

    y -= 34
    c.setFillColor(HexColor("#F5F4FB"))
    c.rect(x0, y - 6, x1 - x0, 24, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont(SEMI, 9)
    c.drawString(x0 + 10, y + 2, "DESCRIPTION")
    c.drawRightString(x1 - 10, y + 2, "AMOUNT")
    y -= 30
    c.setFillColor(INK)
    c.setFont(SEMI, 11)
    title = "FitDesk Pro gym management software – licence"
    if p.months:
        title += f" ({p.months} month{'s' if p.months > 1 else ''})"
    c.drawString(x0 + 10, y, title)
    c.drawRightString(x1 - 10, y, rupees(p.amount))
    c.setFont(REG, 9.5)
    c.setFillColor(MUTED)
    details = []
    if p.plan_name:
        details.append(f"Plan: {p.plan_name}")
    if p.period_from and p.period_to:
        details.append(f"Valid {p.period_from:%d %b %Y} to {p.period_to:%d %b %Y}")
    if p.note:
        details.append(p.note)
    for d in details:
        y -= 14
        c.drawString(x0 + 10, y, d)
    y -= 18
    c.setStrokeColor(LINE)
    c.line(x0, y, x1, y)
    y -= 22
    c.setFillColor(INK)
    c.setFont(BOLD, 13)
    c.drawString(x1 - 220, y, "Total")
    c.drawRightString(x1 - 10, y, rupees(p.amount))
    y -= 20
    c.setFont(SEMI, 10)
    c.setFillColor(HexColor("#157F3D"))
    paid = f"Paid in full · {p.mode}" + (f" · ref {p.reference}" if p.reference else "") + f" · {p.paid_on:%d %b %Y}"
    c.drawRightString(x1 - 10, y, paid)

    c.setFillColor(MUTED)
    c.setFont(REG, 9)
    c.drawString(x0, 70, "Thank you for choosing FitDesk Pro.")
    c.drawString(x0, 57, "This is a computer-generated invoice and needs no signature.")
    c.showPage()
    c.save()
    return buf.getvalue()
