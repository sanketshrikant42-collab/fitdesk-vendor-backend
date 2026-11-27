"""Demo rules, applied to every signed-in API call (gym_accounts.api.staff_required).

In the demo gym:
* settings, passwords/PINs, the team, devices, email, support questions,
  data export and licence-style changes are refused with a friendly message;
* WhatsApp links open WhatsApp without a phone number (the demo's numbers
  are made up and could belong to real people).
Everything else works normally and is rebuilt every night.
"""
import contextvars
import re

IN_DEMO = contextvars.ContextVar("fitdesk_in_demo", default=False)

BLOCKED = [re.compile(p) for p in (
    r"^/api/v1/me/(password|pin)/",
    r"^/api/v1/team/",
    r"^/api/v1/gym/settings/",
    r"^/api/v1/email/",
    r"^/api/v1/reminders/settings/",
    r"^/api/v1/support/",
    r"^/api/v1/bills/[^/]+/email/",
    r"^/api/v1/diet/plans/\d+/email/",
    r"^/api/v1/reminders/\d+/email/",
    r"^/api/v1/members/[^/]+/(delete|merge)/",
)]
BLOCKED_EVEN_GET = [re.compile(r"^/api/v1/data-export/")]
MESSAGE = "This is the FitDesk demo, so this part is switched off. Everything else you can try freely."


def is_demo(client_or_id):
    """One small indexed lookup per request (no cache, so a nightly rebuild
    is recognised at once and nothing can go stale)."""
    from .models import DemoGym
    cid = getattr(client_or_id, "id", client_or_id)
    return DemoGym.objects.filter(client_id=cid).exists()


def guard(request, client, call):
    """Runs the view; for the demo gym checks the rules first."""
    if not is_demo(client):
        return call()
    from gym_accounts.api import ApiError
    path = request.path
    if any(p.match(path) for p in BLOCKED_EVEN_GET) or (
            request.method not in ("GET", "HEAD") and any(p.match(path) for p in BLOCKED)):
        raise ApiError(403, "demo_blocked", MESSAGE)
    token = IN_DEMO.set(True)
    try:
        return call()
    finally:
        IN_DEMO.reset(token)
