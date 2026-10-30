"""/api/v1/ endpoints for staff sign-in and the owner's Team & Access
screen. Request/response shapes are documented in API_V1.md."""

from django.db import transaction
from django.utils import timezone

from . import services as S
from .api import ApiError, client_ip, endpoint, json_body, ok, paginate, staff_required
from .models import Invite, PermissionOverride, StaffDevice, StaffSession, StaffUser, TeamActivity
from .permissions import ACTIONS, MODULE_CODES, OWNER_ONLY_MODULES, ROLE_OWNER, INVITABLE_ROLES


# --- sign-in (no token needed) --------------------------------------------

@endpoint("POST")
def owner_setup(request):
    return ok(S.owner_setup(json_body(request), client_ip(request)), status=201)


@endpoint("POST")
def join(request):
    return ok(S.accept_invite(json_body(request), client_ip(request)), status=201)


@endpoint("POST")
def login(request):
    return ok(S.login(json_body(request), client_ip(request)))


@endpoint("POST")
def refresh(request):
    return ok(S.refresh(json_body(request)))


@endpoint("POST")
def device_status(request):
    return ok(S.device_status(json_body(request)))


@endpoint("POST")
def pin_users(request):
    return ok(S.pin_users(json_body(request)))


@endpoint("POST")
def pin_switch(request):
    return ok(S.pin_switch(json_body(request), client_ip(request)))


# --- signed-in user ------------------------------------------------------

@endpoint("POST")
@staff_required()
def logout(request):
    request.staff_session.revoke("logout")
    return ok()


@endpoint("GET")
@staff_required()
def me(request):
    from .models import GymProfile
    return ok({"user": S.user_json(request.staff, with_perms=True), "device": S.device_json(request.device),
               "gym": {"id": request.client.id, "name": request.client.gym_name,
                       "gym_code": GymProfile.for_client(request.client).gym_code},
               "licence": S.licence_json(request.client), "server_time": timezone.now().isoformat()})


@endpoint("POST")
@staff_required()
def change_password(request):
    data = json_body(request)
    user = request.staff
    if not user.check_password(data.get("current_password") or ""):
        raise ApiError(400, "bad_credentials", "Current password is wrong.")
    user.set_password(S.clean_password(data.get("new_password")))
    user.must_change_password = False
    user.save(update_fields=["password_hash", "must_change_password"])
    StaffSession.revoke_all("password_changed", user=user)
    session, access, refresh_tok = StaffSession.start(user, request.device)
    TeamActivity.record(request.client, "password_changed", actor=user, device=request.device, target=user.name)
    return ok(S.tokens_json(session, access, refresh_tok))


@endpoint("POST")
@staff_required()
def set_pin(request):
    data = json_body(request)
    user = request.staff
    if not user.check_password(data.get("password") or ""):
        raise ApiError(400, "bad_credentials", "Password is wrong.")
    pin = data.get("pin")
    user.set_pin(S.clean_pin(pin) if pin else "")
    user.save(update_fields=["pin_hash"])
    return ok({"has_pin": bool(user.pin_hash)})


# --- Team & Access (owner only) -----------------------------------------------

@endpoint("GET")
@staff_required(owner_only=True)
def team_summary(request):
    return ok(S.team_summary(request.client))


@endpoint("GET")
@staff_required(owner_only=True)
def team_users(request):
    qs = StaffUser.objects.filter(client=request.client).prefetch_related("permission_overrides", "registered_devices")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q) | qs.filter(phone__icontains=q)
    items, page = paginate(request, qs.order_by("-role", "name").distinct())
    users = []
    for u in items:
        j = S.user_json(u)
        j["devices"] = [S.device_json(d) for d in u.registered_devices.all() if d.status != StaffDevice.STATUS_REMOVED]
        users.append(j)
    return ok({"users": users, "pagination": page})


@endpoint("GET")
@staff_required(owner_only=True)
def team_user_detail(request, user_id):
    u = S.gym_object(StaffUser, request.client, user_id)
    j = S.user_json(u, with_perms=True)
    j["devices"] = [S.device_json(d) for d in u.registered_devices.exclude(status=StaffDevice.STATUS_REMOVED)]
    return ok({"user": j})


def _not_owner(u):
    if u.role == ROLE_OWNER:
        raise ApiError(400, "owner_protected", "The owner's own login can't be changed here. Contact FitDesk support to transfer ownership.")


@endpoint("POST")
@staff_required(owner_only=True)
def team_user_update(request, user_id):
    u = S.gym_object(StaffUser, request.client, user_id)
    _not_owner(u)
    data = json_body(request)
    changed = {}
    if "name" in data:
        u.name = S.clean_name(data["name"]); changed["name"] = u.name
    if "role" in data:
        if data["role"] not in INVITABLE_ROLES:
            raise ApiError(400, "bad_role", f"Role must be one of {INVITABLE_ROLES}.")
        u.role = data["role"]; changed["role"] = u.role
    if "linked_record" in data:
        u.linked_record = str(data["linked_record"] or "")[:60]; changed["linked_record"] = u.linked_record
    if "email" in data:
        u.email = str(data["email"] or "")[:254]; changed["email"] = u.email
    u.save()
    TeamActivity.record(request.client, "user_updated", actor=request.staff, device=request.device, target=u.name, **changed)
    return ok({"user": S.user_json(u, with_perms=True)})


@endpoint("POST")
@staff_required(owner_only=True)
def team_user_disable(request, user_id):
    u = S.gym_object(StaffUser, request.client, user_id)
    _not_owner(u)
    u.status, u.disabled_at = StaffUser.STATUS_DISABLED, timezone.now()
    u.save(update_fields=["status", "disabled_at"])
    n = StaffSession.revoke_all("user_disabled", user=u)
    TeamActivity.record(request.client, "user_disabled", actor=request.staff, device=request.device, target=u.name)
    return ok({"user": S.user_json(u), "sessions_ended": n})


@endpoint("POST")
@staff_required(owner_only=True)
def team_user_enable(request, user_id):
    with transaction.atomic():
        profile = S.locked_profile(request.client)
        u = S.gym_object(StaffUser, request.client, user_id)
        if u.is_active:
            return ok({"user": S.user_json(u)})
        used = S.users_used(request.client)
        if used >= profile.max_users:
            raise ApiError(409, "user_limit_reached", f"All {profile.max_users} user slots are in use.", used=used, limit=profile.max_users)
        u.status, u.disabled_at = StaffUser.STATUS_ACTIVE, None
        u.save(update_fields=["status", "disabled_at"])
    TeamActivity.record(request.client, "user_enabled", actor=request.staff, device=request.device, target=u.name)
    return ok({"user": S.user_json(u)})


@endpoint("POST")
@staff_required(owner_only=True)
def team_user_reset_password(request, user_id):
    """Owner sets a temporary password; the person must change it at
    next sign-in. All their sessions end."""
    u = S.gym_object(StaffUser, request.client, user_id)
    _not_owner(u)
    temp = S.clean_password(json_body(request).get("temporary_password"))
    u.set_password(temp)
    u.must_change_password = True
    u.save(update_fields=["password_hash", "must_change_password"])
    n = StaffSession.revoke_all("password_reset", user=u)
    TeamActivity.record(request.client, "password_reset", actor=request.staff, device=request.device, target=u.name)
    return ok({"sessions_ended": n})


@endpoint("POST")
@staff_required(owner_only=True)
def team_user_salary_access(request, user_id):
    """{"access": "none" | "view" | "manage"}. The owner decides, per person,
    who can see salaries and payroll (view) or also pay them (manage)."""
    u = S.gym_object(StaffUser, request.client, user_id)
    _not_owner(u)
    access = str(json_body(request).get("access") or "")
    S.set_salary_access(u, access)
    TeamActivity.record(request.client, "salary_access_changed", actor=request.staff, device=request.device,
                        target=u.name, access=access)
    return ok({"user": S.user_json(u, with_perms=True)})


@endpoint("POST")
@staff_required(owner_only=True)
def team_user_sign_out(request, user_id):
    u = S.gym_object(StaffUser, request.client, user_id)
    qs = {"user": u}
    n = StaffSession.revoke_all("signed_out_by_owner", **qs) if u.id != request.staff.id else \
        StaffSession.objects.filter(user=u, revoked_at__isnull=True).exclude(pk=request.staff_session.pk).update(
            revoked_at=timezone.now(), revoked_reason="signed_out_by_owner")
    TeamActivity.record(request.client, "signed_out_everywhere", actor=request.staff, device=request.device, target=u.name)
    return ok({"sessions_ended": n})


@endpoint("GET", "POST")
@staff_required(owner_only=True)
def team_user_permissions(request, user_id):
    """GET: effective permissions + overrides. POST {"overrides": [{module,
    action, allowed}]} replaces this person's overrides; [] resets to the
    role default."""
    u = S.gym_object(StaffUser, request.client, user_id)
    if request.method == "POST":
        _not_owner(u)
        rows = json_body(request).get("overrides")
        if not isinstance(rows, list):
            raise ApiError(400, "bad_overrides", "overrides must be a list.")
        clean = {}
        for r in rows:
            m, a, allowed = (r or {}).get("module"), (r or {}).get("action"), (r or {}).get("allowed")
            if m not in MODULE_CODES or a not in ACTIONS or not isinstance(allowed, bool):
                raise ApiError(400, "bad_overrides", f"Unknown module/action: {m}.{a}")
            if m in OWNER_ONLY_MODULES and allowed:
                raise ApiError(400, "owner_only_module", f"'{m}' can only ever be used by the owner.")
            clean[(m, a)] = allowed
        with transaction.atomic():
            u.permission_overrides.all().delete()
            PermissionOverride.objects.bulk_create(
                [PermissionOverride(user=u, module=m, action=a, allowed=v) for (m, a), v in clean.items()])
        TeamActivity.record(request.client, "permissions_changed", actor=request.staff, device=request.device,
                            target=u.name, overrides=[f"{m}.{a}={'on' if v else 'off'}" for (m, a), v in clean.items()])
    return ok({"user": S.user_json(u, with_perms=True)})


@endpoint("GET", "POST")
@staff_required(owner_only=True)
def team_invites(request):
    if request.method == "POST":
        return ok(S.create_invite(request.staff, json_body(request), request.device), status=201)
    qs = Invite.objects.filter(client=request.client)
    if request.GET.get("status") == "pending":
        qs = qs.filter(status=Invite.STATUS_PENDING, expires_at__gt=timezone.now())
    items, page = paginate(request, qs)
    return ok({"invites": [S.invite_json(i) for i in items], "pagination": page})


@endpoint("POST")
@staff_required(owner_only=True)
def team_invite_cancel(request, invite_id):
    inv = S.gym_object(Invite, request.client, invite_id)
    if inv.status == Invite.STATUS_PENDING:
        inv.status = Invite.STATUS_CANCELLED
        inv.save(update_fields=["status"])
        TeamActivity.record(request.client, "invite_cancelled", actor=request.staff, device=request.device, target=inv.name)
    return ok({"invite": S.invite_json(inv)})


@endpoint("GET")
@staff_required(owner_only=True)
def team_devices(request):
    qs = StaffDevice.objects.filter(client=request.client).select_related("registered_by")
    status = request.GET.get("status")
    qs = qs.filter(status=status) if status else qs.exclude(status=StaffDevice.STATUS_REMOVED)
    items, page = paginate(request, qs)
    return ok({"devices": [S.device_json(d) for d in items], "pagination": page})


@endpoint("POST")
@staff_required(owner_only=True)
def team_device_action(request, device_id, action):
    d = S.gym_object(StaffDevice, request.client, device_id)
    data = json_body(request)
    if action == "approve":
        d = S.approve_device(request.staff, d, request.device)
    elif action == "reject":
        if d.status != StaffDevice.STATUS_PENDING:
            raise ApiError(400, "not_pending", "Only a device waiting for approval can be rejected.")
        d = S.set_device_status(request.staff, d, StaffDevice.STATUS_REJECTED, "device_rejected", request.device)
    elif action == "remove":
        if d.pk == request.device.pk:
            raise ApiError(400, "current_device", "You can't remove the device you're using right now.")
        d = S.set_device_status(request.staff, d, StaffDevice.STATUS_REMOVED, "device_removed", request.device)
    elif action == "rename":
        d.name = (str(data.get("name") or "").strip() or d.name)[:80]
        d.save(update_fields=["name"])
        TeamActivity.record(request.client, "device_renamed", actor=request.staff, device=request.device, target=d.name)
    elif action == "set-shared":
        shared = data.get("shared")
        if not isinstance(shared, bool):
            raise ApiError(400, "bad_value", "shared must be true or false.")
        with transaction.atomic():
            profile = S.locked_profile(request.client)
            d = StaffDevice.objects.select_for_update().get(pk=d.pk)
            was_free = d.is_approved and S.device_would_be_free(d, profile)
            d.shared = shared
            if d.is_approved and was_free and shared:
                used, _ = S.device_counts(request.client, profile)
                if used >= profile.max_devices:
                    raise ApiError(409, "device_limit_reached", "Making this shared would use a paid device slot, and none are free.")
            d.save(update_fields=["shared"])
            if not shared:
                StaffSession.objects.filter(device=d, revoked_at__isnull=True).exclude(user=d.registered_by).update(
                    revoked_at=timezone.now(), revoked_reason="device_unshared")
        TeamActivity.record(request.client, "device_shared" if shared else "device_unshared", actor=request.staff,
                            device=request.device, target=d.name)
    else:
        raise ApiError(404, "not_found", "Unknown action.")
    return ok({"device": S.device_json(d), "summary": S.team_summary(request.client)})


@endpoint("GET")
@staff_required(owner_only=True)
def team_activity(request):
    qs = TeamActivity.objects.filter(client=request.client).select_related("device")
    items, page = paginate(request, qs, default_size=50)
    return ok({"activity": [{"id": a.id, "at": a.created_at.isoformat(), "who": a.actor_label, "action": a.action,
                             "target": a.target, "device": a.device.name if a.device else None, "details": a.metadata}
                            for a in items], "pagination": page})


# --- local test console (DEBUG only) ------------------------------------------

def test_console(request):
    """Click-through page for trying the owner/staff flow locally. Only
    routed when DEBUG=True (see urls.py), so it never exists in production."""
    from django.shortcuts import render
    return render(request, "gym_accounts/console.html")
