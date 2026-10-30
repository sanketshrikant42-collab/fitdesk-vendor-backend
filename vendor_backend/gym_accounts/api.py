"""Small JSON API helpers for the /api/v1/ endpoints: request parsing,
error responses, staff sign-in checks and rate limiting.

Kept as plain Django (no extra framework) to match the existing
licensing/cloud_backup apps and add no new dependency.
"""

import json
from datetime import timedelta
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from licensing.models import License

from .models import AuthAttempt, StaffSession, sha256
from .permissions import has_perm


class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def ok(data=None, status=200):
    body = {"ok": True}
    body.update(data or {})
    return JsonResponse(body, status=status)


def json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "bad_json", "Request body must be valid JSON.")
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "Request body must be a JSON object.")
    return data


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")) or "unknown"


def endpoint(*methods):
    """JSON endpoint: method check, CSRF-exempt (token auth, no cookies),
    ApiError -> {"ok": false, "error": code, "message": ...}."""
    def deco(fn):
        @csrf_exempt
        @wraps(fn)
        def inner(request, *a, **kw):
            if request.method not in methods:
                return JsonResponse({"ok": False, "error": "method_not_allowed", "message": f"Use {', '.join(methods)}."}, status=405)
            try:
                return fn(request, *a, **kw)
            except ApiError as e:
                body = {"ok": False, "error": e.code, "message": e.message}
                body.update(e.extra)
                return JsonResponse(body, status=e.status)
        return inner
    return deco


# --- licence ---------------------------------------------------------------

def licence_for(client):
    """(licence, effective_status). The gym's newest licence decides, the
    same rule Client.current_license already uses."""
    lic = License.objects.filter(client=client).order_by("-issued_at").first()
    return lic, (lic.effective_status if lic else "not_found")


def require_active_licence(client):
    lic, status = licence_for(client)
    if status != License.STATUS_ACTIVE:
        raise ApiError(403, "licence_inactive", f"This gym's FitDesk Pro licence is {status.replace('_', ' ')}.", licence_status=status)
    return lic


# --- sign-in check -----------------------------------------------------------

def staff_required(module=None, action="view", owner_only=False):
    """Every check runs on every call, so a disabled user, a removed
    device, a revoked session or an expired licence is refused at once."""
    def deco(fn):
        @wraps(fn)
        def inner(request, *a, **kw):
            header = request.META.get("HTTP_AUTHORIZATION", "")
            if not header.startswith("Bearer "):
                raise ApiError(401, "not_signed_in", "Sign in first.")
            token = header[7:].strip()
            session = (StaffSession.objects.select_related("user", "user__client", "device")
                       .filter(access_hash=sha256(token)).first())
            if session is None or session.revoked_at is not None:
                raise ApiError(401, "session_ended", "You've been signed out. Please sign in again.")
            now = timezone.now()
            if session.access_expires_at <= now:
                raise ApiError(401, "token_expired", "Session expired; refresh it.")
            user, device = session.user, session.device
            if not user.is_active:
                session.revoke("user_disabled")
                raise ApiError(403, "user_disabled", "Your access to this gym has been turned off by the owner.")
            if device.client_id != user.client_id or not device.is_approved or not device.usable_by(user):
                session.revoke("device_not_approved")
                raise ApiError(403, "device_not_approved", "This device is no longer approved for this gym.")
            request.licence = require_active_licence(user.client)
            if owner_only and not user.is_owner:
                raise ApiError(403, "owner_only", "Only the gym owner can do this.")
            if module and not has_perm(user, module, action):
                raise ApiError(403, "permission_denied", "Your role doesn't allow this.", module=module, action=action)
            if session.last_used_at is None or now - session.last_used_at > timedelta(seconds=60):
                StaffSession.objects.filter(pk=session.pk).update(last_used_at=now)
                type(device).objects.filter(pk=device.pk).update(last_seen_at=now)
            request.staff, request.device, request.client, request.staff_session = user, device, user.client, session
            return fn(request, *a, **kw)
        return inner
    return deco


# --- rate limiting -----------------------------------------------------------

LIMITS = {
    # kind: (max failures, window)
    "login_phone": (5, timedelta(minutes=15)),
    "login_ip": (30, timedelta(minutes=15)),
    "invite_ip": (10, timedelta(minutes=15)),
    "setup_ip": (10, timedelta(hours=1)),
    "pin_device": (5, timedelta(minutes=15)),
}


def check_rate(kind, key):
    limit, window = LIMITS[kind]
    n = AuthAttempt.objects.filter(kind=kind, key=key, success=False, created_at__gte=timezone.now() - window).count()
    if n >= limit:
        raise ApiError(429, "too_many_attempts", "Too many attempts. Please wait a few minutes and try again.")


def note_attempt(kind, key, success):
    AuthAttempt.objects.create(kind=kind, key=key[:120], success=success)


def paginate(request, qs, default_size=25, max_size=100):
    try:
        page = max(1, int(request.GET.get("page", 1)))
        size = min(max_size, max(1, int(request.GET.get("page_size", default_size))))
    except ValueError:
        raise ApiError(400, "bad_page", "page and page_size must be numbers.")
    total = qs.count()
    items = list(qs[(page - 1) * size: page * size])
    return items, {"page": page, "page_size": size, "total": total, "has_next": page * size < total}
