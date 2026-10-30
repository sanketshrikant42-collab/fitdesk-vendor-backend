"""Slows down anyone guessing licence keys (Phase 1, 29 Oct 2026).

Licence keys are FDPX-XXXX-XXXX-XXXX (12 hex digits). Before this, the
public endpoints answered "not recognised" as fast as anyone could ask, so
a script could try keys without limit. Now each network address gets a
limited number of WRONG keys per window; correct keys are never slowed.

Counts live in the database (gym_accounts.AuthAttempt), not in memory, so
they're right across gunicorn workers and restarts (same reasoning as
licensing/ratelimit.py)."""
from datetime import timedelta

from django.utils import timezone

LIMITS = {
    "licence_ip": (20, timedelta(minutes=15)),   # validate-license, activate-device
    "support_ip": (20, timedelta(minutes=15)),   # submit-query, support-queries
}

MESSAGE = "Too many wrong licence keys from this network. Please wait 15 minutes and try again."


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")) or "unknown"


def is_blocked(kind, request):
    from gym_accounts.models import AuthAttempt
    limit, window = LIMITS[kind]
    return AuthAttempt.objects.filter(kind=kind, key=client_ip(request)[:120], success=False,
                                      created_at__gte=timezone.now() - window).count() >= limit


def note_failure(kind, request):
    from gym_accounts.models import AuthAttempt
    AuthAttempt.objects.create(kind=kind, key=client_ip(request)[:120], success=False)
