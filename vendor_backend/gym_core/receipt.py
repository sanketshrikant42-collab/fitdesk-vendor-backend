"""One description of a receipt, used by both the on-screen / printable
HTML receipt and the downloadable PDF, so the two always look the same.

Also handles the gym logo shown at the top of every receipt.
"""
import base64
from datetime import date
from io import BytesIO

from django.utils import timezone

from gym_accounts.api import ApiError

from . import rules as R

LOGO_MAX_UPLOAD = 2 * 1024 * 1024        # 2 MB as uploaded
LOGO_MAX_SIDE = 600                      # stored at most 600 px on the longest side


def money(x):
    return f"₹ {R.money(x):,.2f}"


def nice_date(s):
    if not s:
        return ""
    return date.fromisoformat(str(s)[:10]).strftime("%d %b %Y").lstrip("0")


def _pct(x):
    v = R.money(x)
    return f"{v.normalize():f}"


def receipt_view(b, s):
    """b = services.bill_json(bill); s = GymSettings. Returns everything the
    receipt shows, already formatted."""
    inclusive = b.get("gst_inclusive")
    rows = [("Plan", b["plan"]["name"] + (f" · Trainer {b['trainer']['name']}" if b.get("trainer") else ""), ""),
            ("Valid", f"{nice_date(b['start_date'])} → {nice_date(b['end_date'])}", ""),
            ("Fee (incl. GST)" if inclusive else "Fee", money(b["fee"]), "")]
    if R.money(b["discount"]) > 0:
        rows.append(("Discount", "− " + money(b["discount"]), ""))
    if b["gst_applied"]:
        half = R.money(R.money(b["gst_percent"]) / 2)
        split = f"CGST {_pct(half)}% + SGST {_pct(half)}%"
        if inclusive:
            rows.append(("Price before GST", money(R.money(b["total"]) - R.money(b["gst_amount"])), "muted"))
            rows.append((f"GST {_pct(b['gst_percent'])}% included ({split})", money(b["gst_amount"]), "muted"))
        else:
            rows.append((f"GST {_pct(b['gst_percent'])}% ({split})", money(b["gst_amount"]), ""))
    rows.append(("Total", money(b["total"]), "total"))
    for p in b["payments"]:
        label = f"Paid {nice_date(p['paid_on'])} · {p['mode']}" + (f" · Ref {p['reference_no']}" if p["reference_no"] else "")
        rows.append((label, money(p["amount"]), "muted"))
    rows.append(("Balance due", money(b["remaining"]), "bold"))
    contact = " · ".join(x for x in (s.phone, s.email) if x)
    return {
        "gym": {"name": s.gym_name, "tagline": s.tagline, "address": [l for l in (s.address or "").splitlines() if l.strip()],
                "contact": contact, "gstin": s.gstin},
        "logo": logo_data_uri(s),
        "receipt_no": b["receipt_no"], "date": nice_date(b["created_at"]),
        "status": b["payment_status"], "status_ok": b["payment_status"] == "Paid",
        "cancelled": (b["cancelled_reason"] or "Cancelled") if b["status"] == "cancelled" else "",
        "member": {"name": b["member"]["name"], "sub": f"{b['member']['code']} · {b['member']['phone']}"},
        "covers": ", ".join(c["name"] for c in b["covers"]) if len(b["covers"]) > 1 else "",
        "rows": [{"label": l, "value": v, "style": st} for l, v, st in rows],
        "upi": (b.get("upi") or {}).get("upi_id") if R.money(b["remaining"]) > 0 else "",
        "footer": s.receipt_footer or "", "terms": s.terms or "",
    }


# --- logo -----------------------------------------------------------------------------------

def logo_bytes(s):
    return bytes(s.logo) if s.logo else None


def logo_data_uri(s):
    data = logo_bytes(s)
    return "data:image/png;base64," + base64.b64encode(data).decode() if data else ""


def save_logo(s, upload):
    """Checks the upload is a real image, shrinks it, keeps transparency,
    and stores it as PNG."""
    from PIL import Image, UnidentifiedImageError
    if upload is None:
        raise ApiError(400, "logo_required", "Choose an image file for the logo.")
    if upload.size > LOGO_MAX_UPLOAD:
        raise ApiError(400, "logo_too_big", "The logo must be 2 MB or smaller.")
    try:
        img = Image.open(upload)
        img.verify()
        upload.seek(0)
        img = Image.open(upload)
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ApiError(400, "bad_logo", "That file isn't an image. Use PNG, JPG or WEBP.")
    if img.format not in ("PNG", "JPEG", "WEBP"):
        raise ApiError(400, "bad_logo", "Use a PNG, JPG or WEBP image.")
    img = img.convert("RGBA")
    img.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    s.logo, s.logo_updated_at = out.getvalue(), timezone.now()
    s.save(update_fields=["logo", "logo_updated_at", "updated_at"])
    return s


def remove_logo(s):
    s.logo, s.logo_updated_at = None, None
    s.save(update_fields=["logo", "logo_updated_at", "updated_at"])
    return s
