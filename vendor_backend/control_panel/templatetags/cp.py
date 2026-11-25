from django import template

register = template.Library()


@register.filter
def inr(v):
    """₹1,25,000 (Indian grouping, paise only when not whole)."""
    if v in (None, ""):
        return "—"
    from control_panel.invoice import rupees
    try:
        return rupees(v)
    except Exception:                      # noqa: BLE001
        return v
