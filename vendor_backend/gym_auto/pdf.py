"""Receipt PDF that looks the same as the on-screen receipt
(gym_core/templates/gym_core/receipt.html). Both are built from
gym_core.receipt.receipt_view, so the content is identical; this file only
draws it: same layout, colours and font (Inter, with the ₹ sign), plus the
gym's logo. Pure Python (reportlab), so it works on any server."""
import os
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from gym_core.receipt import logo_bytes, receipt_view

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
REG, SEMI, BOLD = "Inter", "Inter-SemiBold", "Inter-Bold"
_fonts_ready = False

INK, MUTED, LINE, ACCENT, ACCENT_D = HexColor("#172033"), HexColor("#667085"), HexColor("#F0F2F5"), HexColor("#6C5CE7"), HexColor("#5545D0")
OK_BG, OK_T, DUE_BG, DUE_T = HexColor("#E8F8EF"), HexColor("#0B7A43"), HexColor("#FFF4E5"), HexColor("#B54708")
CAN_BG, CAN_T = HexColor("#FFF0EE"), HexColor("#C4320A")

K = 0.8                       # px in the HTML receipt → points in the PDF
PAGE_W, PAGE_H = A4
W = 564 * K                   # the receipt card's inner width
X0 = (PAGE_W - W) / 2
X1 = X0 + W
TOP, BOTTOM = 48, 48


def _register():
    global _fonts_ready
    if not _fonts_ready:
        for name, f in ((REG, "Inter-Regular.ttf"), (SEMI, "Inter-SemiBold.ttf"), (BOLD, "Inter-Bold.ttf")):
            pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, f)))
        _fonts_ready = True


class Pen:
    """Writes top-down; starts a new page when the current one is full."""

    def __init__(self, c):
        self.c, self.y = c, PAGE_H - TOP

    def need(self, h):
        if self.y - h < BOTTOM:
            self.c.showPage()
            self.y = PAGE_H - TOP

    def text(self, x, s, font, size, color, align="left"):
        c = self.c
        c.setFont(font, size)
        c.setFillColor(color)
        if align == "right":
            c.drawRightString(x, self.y, s)
        elif align == "center":
            c.drawCentredString(x, self.y, s)
        else:
            c.drawString(x, self.y, s)


def _chip(c, x_right, y, text, ok):
    size = 11 * K
    w = pdfmetrics.stringWidth(text, BOLD, size) + 20 * K
    h = size + 6 * K
    c.setFillColor(OK_BG if ok else DUE_BG)
    c.roundRect(x_right - w, y - 3.5 * K, w, h, h / 2, stroke=0, fill=1)
    c.setFillColor(OK_T if ok else DUE_T)
    c.setFont(BOLD, size)
    c.drawCentredString(x_right - w / 2, y, text)


def draw_header(c, p, s, gym, label, number, date_text, chip=None):
    """The top of every FitDesk document (receipt, diet plan): logo and gym
    details on the left; label, number, date and an optional status chip on
    the right; then the purple line. gym = receipt_view(...)["gym"]."""
    muted, h1 = 12 * K, 22 * K
    top = p.y
    tx = X0
    logo_h = 0
    data = logo_bytes(s)
    if data:
        img = ImageReader(BytesIO(data))
        iw, ih = img.getSize()
        scale = min(140 * K / iw, 64 * K / ih)
        lw, lh = iw * scale, ih * scale
        c.drawImage(img, X0, top - lh, lw, lh, mask="auto")
        tx, logo_h = X0 + lw + 14 * K, lh
    right_w = 150 * K
    left_w = X1 - right_w - tx
    p.y = top - h1 * 0.85
    for i, line in enumerate(simpleSplit(gym["name"], BOLD, h1, left_w) or [""]):
        if i:
            p.y -= h1 * 1.15
        p.text(tx, line, BOLD, h1, INK)
    p.y -= h1 * 0.35
    lines = ([gym["tagline"]] if gym["tagline"] else []) + gym["address"] + \
            ([gym["contact"]] if gym["contact"] else []) + ([f"GSTIN: {gym['gstin']}"] if gym.get("gstin") else [])
    for line in lines:
        for part in simpleSplit(line, REG, muted, left_w):
            p.y -= muted * 1.3
            p.text(tx, part, REG, muted, MUTED)
    left_bottom = min(p.y - muted * 0.4, top - logo_h)

    p.y = top - muted
    p.text(X1, label, REG, muted, MUTED, "right")
    p.y -= 16 * K * 1.3
    p.text(X1, number, BOLD, 16 * K, ACCENT_D, "right")
    p.y -= muted * 1.35
    p.text(X1, date_text, REG, muted, MUTED, "right")
    right_bottom = p.y - 4 * K
    if chip:
        p.y -= 6 * K + 11 * K * 1.4
        _chip(c, X1, p.y, chip[0], chip[1])
        right_bottom = p.y - 6 * K

    p.y = min(left_bottom, right_bottom) - 14 * K
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2 * K)
    c.line(X0, p.y, X1, p.y)


def gym_header(s):
    """Gym details as shown in every document header."""
    return {"name": s.gym_name, "tagline": s.tagline, "address": [l for l in (s.address or "").splitlines() if l.strip()],
            "contact": " · ".join(x for x in (s.phone, s.email) if x), "gstin": s.gstin}


def receipt_pdf(b, s):
    """b = gym_core.services.bill_json(bill, with_links=True or False); s = GymSettings."""
    _register()
    r = receipt_view(b, s)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Receipt {r['receipt_no']}")
    c.setAuthor(s.gym_name)
    p = Pen(c)
    muted, body, h1 = 12 * K, 13 * K, 22 * K

    # cancelled banner
    if r["cancelled"]:
        bh = 30 * K
        c.setFillColor(CAN_BG)
        c.roundRect(X0, p.y - bh, W, bh, 8 * K, stroke=0, fill=1)
        p.y -= bh / 2 + body * 0.35
        p.text(X0 + 12 * K, f"CANCELLED · {r['cancelled']}", BOLD, body, CAN_T)
        p.y -= bh / 2 + 12 * K - body * 0.35

    # header: logo + gym details (left), receipt number, date, status (right)
    draw_header(c, p, s, r["gym"], "Receipt", r["receipt_no"], r["date"], (r["status"], r["status_ok"]))
    p.y -= 16 * K + body

    # member
    name, nsize = r["member"]["name"], 16 * K
    p.y -= nsize - body
    p.text(X0, name, BOLD, nsize, INK)
    nx = X0 + pdfmetrics.stringWidth(name, BOLD, nsize) + 4
    p.text(nx, f"· {r['member']['sub']}", REG, muted, MUTED)
    if r["covers"]:
        for part in simpleSplit(f"Covers: {r['covers']}", REG, muted, W):
            p.y -= muted * 1.4
            p.text(X0, part, REG, muted, MUTED)
    p.y -= 10 * K

    # rows (same as the HTML table)
    pad = 6 * K
    for row in r["rows"]:
        style = row["style"]
        size = muted if style == "muted" else body
        font = BOLD if style in ("total", "bold") else REG
        color = MUTED if style == "muted" else INK
        vw = pdfmetrics.stringWidth(row["value"], font, size)
        parts = simpleSplit(row["label"], font, size, W - vw - 12 * K) or [""]
        h = pad * 2 + size * 1.25 * len(parts)
        p.need(h)
        if style == "total":
            c.setStrokeColor(INK)
            c.setLineWidth(2 * K)
            c.line(X0, p.y, X1, p.y)
        p.y -= pad + size
        p.text(X1, row["value"], font, size, color, "right")
        for i, part in enumerate(parts):
            if i:
                p.y -= size * 1.25
            p.text(X0, part, font, size, color)
        p.y -= pad + size * 0.25
        if style != "total":
            c.setStrokeColor(LINE)
            c.setLineWidth(1 * K)
            c.line(X0, p.y, X1, p.y)

    if r["upi"]:
        p.need(muted * 3)
        p.y -= 10 * K + muted
        lead = "Pay the balance by UPI to "
        p.text(X0, lead, REG, muted, MUTED)
        p.text(X0 + pdfmetrics.stringWidth(lead, REG, muted), r["upi"], BOLD, muted, MUTED)

    foot = "\n\n".join(x for x in (r["footer"], r["terms"]) if x)
    if foot:
        p.y -= 18 * K
        fs = 11 * K
        for para in foot.split("\n"):
            for part in (simpleSplit(para, REG, fs, W) if para.strip() else [""]):
                p.need(fs * 1.5)
                p.y -= fs * 1.45
                p.text(X0, part, REG, fs, MUTED)

    p.need(muted * 3)
    p.y -= 14 * K + muted
    p.text(PAGE_W / 2, "Powered by FitDesk Pro", REG, muted, MUTED, "center")
    c.showPage()
    c.save()
    return buf.getvalue()
