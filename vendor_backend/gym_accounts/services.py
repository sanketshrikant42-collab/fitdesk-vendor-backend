"""Business rules for staff access: owner setup, invites, sign-in,
device approval, limits. Views stay thin; tests target these rules
through the API."""

import re

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from licensing.models import License
from licensing.phone_utils import validate_whatsapp_phone

from .api import ApiError, check_rate, licence_for, note_attempt, require_active_licence
from .models import (GymProfile, Invite, PermissionOverride, OwnerSetupCode, StaffDevice, StaffSession, StaffUser, TeamActivity,
                     sha256)
from .permissions import (INVITABLE_ROLES, ROLE_OWNER, SALARY_ACCESS, effective_permissions, salary_access, scope_for,
                          MODULE_CODES)

INSTALLATION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
PLATFORMS = {p for p, _ in StaffDevice.PLATFORMS}


# --- input helpers ----------------------------------------------------------

def clean_phone(raw):
    phone, err = validate_whatsapp_phone(raw)
    if err:
        raise ApiError(400, "bad_phone", err)
    return phone


def clean_name(raw, field="name"):
    name = (raw or "").strip()
    if not name or len(name) > 120:
        raise ApiError(400, f"bad_{field}", "Enter a name (up to 120 characters).")
    return name


def clean_password(raw, user=None):
    if not isinstance(raw, str) or not raw:
        raise ApiError(400, "bad_password", "Enter a password.")
    try:
        validate_password(raw, user=None)
    except ValidationError as e:
        raise ApiError(400, "weak_password", " ".join(e.messages))
    return raw


def clean_pin(raw):
    pin = str(raw or "")
    if not re.fullmatch(r"\d{4,6}", pin):
        raise ApiError(400, "bad_pin", "PIN must be 4 to 6 digits.")
    return pin


def clean_device(data):
    d = data.get("device")
    if not isinstance(d, dict):
        raise ApiError(400, "device_required", "Device details are required.")
    inst = str(d.get("installation_id") or "")
    secret = str(d.get("device_secret") or "")
    platform = str(d.get("platform") or "").lower()
    if not INSTALLATION_RE.match(inst):
        raise ApiError(400, "bad_device", "installation_id must be 8-64 letters, digits, - or _.")
    if len(secret) < 32 or len(secret) > 200:
        raise ApiError(400, "bad_device", "device_secret must be 32-200 characters.")
    if platform not in PLATFORMS:
        raise ApiError(400, "bad_device", f"platform must be one of {sorted(PLATFORMS)}.")
    name = (str(d.get("name") or "").strip() or f"{platform.title()} device")[:80]
    return {"installation_id": inst, "secret": secret, "platform": platform, "name": name,
            "app_version": str(d.get("app_version") or "")[:30]}


# --- limits -------------------------------------------------------------------

def users_used(client):
    active = StaffUser.objects.filter(client=client, status=StaffUser.STATUS_ACTIVE).count()
    pending = Invite.objects.filter(client=client, status=Invite.STATUS_PENDING, expires_at__gt=timezone.now()).count()
    return active + pending


def device_counts(client, profile):
    approved = StaffDevice.objects.filter(client=client, status=StaffDevice.STATUS_APPROVED)
    owner_personal = approved.filter(registered_by__role=ROLE_OWNER, shared=False).count()
    free = min(owner_personal, profile.owner_free_devices)
    return approved.count() - free, free


def device_would_be_free(device, profile):
    """An owner's personal device is free while the owner still has an
    unused free slot."""
    if device.shared or not device.registered_by or device.registered_by.role != ROLE_OWNER:
        return False
    used = StaffDevice.objects.filter(client=device.client, status=StaffDevice.STATUS_APPROVED,
                                      registered_by__role=ROLE_OWNER, shared=False).exclude(pk=device.pk).count()
    return used < profile.owner_free_devices


def locked_profile(client):
    GymProfile.for_client(client)
    return GymProfile.objects.select_for_update().get(client=client)


# --- serialisers ----------------------------------------------------------------

def user_json(u, with_perms=False):
    out = {"id": u.id, "name": u.name, "phone": u.phone, "email": u.email, "role": u.role,
           "status": u.status, "linked_record": u.linked_record, "has_pin": bool(u.pin_hash),
           "must_change_password": u.must_change_password,
           "created_at": u.created_at.isoformat(), "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}
    if with_perms:
        out["permissions"] = effective_permissions(u)
        out["scopes"] = {m: scope_for(u, m) for m in MODULE_CODES}
        out["overrides"] = [{"module": o.module, "action": o.action, "allowed": o.allowed}
                            for o in u.permission_overrides.all()]
    out["salary_access"] = salary_access(u)
    return out


def set_salary_access(user, access):
    """Owner's per-person choice: none / view (see salaries, payroll, PT
    earnings) / manage (also set salaries, pay salary, give advances)."""
    if access not in SALARY_ACCESS:
        raise ApiError(400, "bad_salary_access", "Salary access must be none, view or manage.")
    if user.role == "owner":
        return
    user.permission_overrides.filter(module="salaries").delete()
    PermissionOverride.objects.bulk_create([PermissionOverride(user=user, module="salaries", action=a, allowed=True)
                                            for a in SALARY_ACCESS[access]])


def device_json(d):
    return {"id": d.id, "name": d.name, "platform": d.platform, "app_version": d.app_version, "shared": d.shared,
            "status": d.status, "registered_by": ({"id": d.registered_by.id, "name": d.registered_by.name}
                                                    if d.registered_by else None),
            "created_at": d.created_at.isoformat(), "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "decided_at": d.decided_at.isoformat() if d.decided_at else None}


def invite_json(i):
    return {"id": i.id, "name": i.name, "phone": i.phone, "role": i.role, "linked_record": i.linked_record,
            "salary_access": i.salary_access,
            "status": i.status if (i.status != Invite.STATUS_PENDING or i.is_usable) else "expired",
            "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat()}


def licence_json(client):
    lic, status = licence_for(client)
    if not lic:
        return {"status": status}
    return {"status": status, "expiry_date": lic.expiry_date.isoformat(),
            "plan": lic.plan.name if lic.plan_id else None, "features": lic.resolved_features}


def tokens_json(session, access, refresh):
    return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
            "access_expires_at": session.access_expires_at.isoformat(),
            "refresh_expires_at": session.refresh_expires_at.isoformat()}


def signed_in_json(user, device, session, access, refresh):
    out = {"user": user_json(user, with_perms=True), "device": device_json(device),
           "gym": {"id": user.client_id, "name": user.client.gym_name, "gym_code": GymProfile.for_client(user.client).gym_code},
           "licence": licence_json(user.client)}
    out.update(tokens_json(session, access, refresh))
    return out


# --- devices --------------------------------------------------------------------

def find_or_register_device(client, user, dev):
    """Returns the gym's StaffDevice for this installation, creating it as
    pending if new. A known installation must present its original
    secret, so nobody can reuse someone else's installation id."""
    device = StaffDevice.objects.filter(client=client, installation_id=dev["installation_id"]).first()
    if device is None:
        device = StaffDevice.objects.create(client=client, installation_id=dev["installation_id"],
                                            secret_hash=sha256(dev["secret"]), name=dev["name"],
                                            platform=dev["platform"], app_version=dev["app_version"],
                                            registered_by=user)
        TeamActivity.record(client, "device_requested", actor=user, device=device, target=device.name,
                            platform=device.platform)
        return device
    if not device.check_secret(dev["secret"]):
        raise ApiError(403, "device_mismatch", "This device can't be verified. Reinstall the app or ask the owner to remove it.")
    if dev["app_version"] and device.app_version != dev["app_version"]:
        device.app_version = dev["app_version"]
        device.save(update_fields=["app_version"])
    return device


def device_gate(device, user):
    if device.status == StaffDevice.STATUS_PENDING:
        raise ApiError(403, "device_pending", "Waiting for the gym owner to approve this device.",
                       device=device_json(device))
    if device.status in (StaffDevice.STATUS_REJECTED, StaffDevice.STATUS_REMOVED):
        raise ApiError(403, "device_blocked", "This device isn't allowed for this gym. Ask the owner.",
                       device=device_json(device))
    if not device.usable_by(user):
        raise ApiError(403, "device_personal", "This device belongs to another staff member. Ask the owner to mark it shared.")


def approve_device(actor, device, request_device=None):
    with transaction.atomic():
        profile = locked_profile(device.client)
        device = StaffDevice.objects.select_for_update().get(pk=device.pk)
        if device.status == StaffDevice.STATUS_APPROVED:
            return device
        used, _ = device_counts(device.client, profile)
        if not device_would_be_free(device, profile) and used >= profile.max_devices:
            raise ApiError(409, "device_limit_reached",
                           f"All {profile.max_devices} device slots are in use. Remove an old device or upgrade the plan.",
                           used=used, limit=profile.max_devices)
        device.status, device.decided_by, device.decided_at = StaffDevice.STATUS_APPROVED, actor, timezone.now()
        device.save(update_fields=["status", "decided_by", "decided_at"])
    TeamActivity.record(device.client, "device_approved", actor=actor, device=request_device, target=device.name)
    return device


def set_device_status(actor, device, status, action, request_device=None):
    device.status, device.decided_by, device.decided_at = status, actor, timezone.now()
    device.save(update_fields=["status", "decided_by", "decided_at"])
    StaffSession.revoke_all(action, device=device)
    TeamActivity.record(device.client, action, actor=actor, device=request_device, target=device.name)
    return device


# --- flows ------------------------------------------------------------------------

def owner_setup(data, ip):
    """First Owner login for a gym: licence key + vendor-issued setup
    code. The owner's first device is approved straight away."""
    check_rate("setup_ip", ip)
    key = str(data.get("license_key") or "").strip()
    code = str(data.get("setup_code") or "").strip().upper().replace("-", "").replace(" ", "")
    lic = License.objects.select_related("client").filter(key=key).first()
    setup = OwnerSetupCode.objects.filter(code_hash=sha256(code)).first() if code else None
    if lic is None or setup is None or setup.client_id != lic.client_id or not setup.is_usable:
        note_attempt("setup_ip", ip, False)
        raise ApiError(400, "bad_setup_code", "Licence key or setup code is wrong or expired. Ask FitDesk support for a new code.")
    client = lic.client
    require_active_licence(client)
    existing_owner = StaffUser.objects.filter(client=client, role=ROLE_OWNER).first()
    if existing_owner is not None:
        return _recover_owner_device(existing_owner, setup, data, ip)
    name, phone = clean_name(data.get("name")), clean_phone(data.get("phone"))
    password = clean_password(data.get("password"))
    dev = clean_device(data)
    with transaction.atomic():
        locked_profile(client)
        if StaffUser.objects.filter(client=client, role=ROLE_OWNER).exists():
            raise ApiError(409, "owner_exists", "This gym already has an owner login. Sign in instead.")
        if StaffUser.objects.filter(client=client, phone=phone).exists():
            raise ApiError(409, "phone_taken", "This mobile number is already used by a staff login in this gym.")
        owner = StaffUser(client=client, name=name, phone=phone, role=ROLE_OWNER)
        owner.set_password(password)
        owner.last_login_at = timezone.now()
        owner.save()
        setup.used_at = timezone.now()
        setup.save(update_fields=["used_at"])
        device = find_or_register_device(client, owner, dev)
        if device.registered_by_id != owner.id:
            raise ApiError(409, "device_in_use", "This installation is already registered to this gym.")
        device.status, device.decided_by, device.decided_at = StaffDevice.STATUS_APPROVED, owner, timezone.now()
        device.save(update_fields=["status", "decided_by", "decided_at"])
        session, access, refresh = StaffSession.start(owner, device)
    note_attempt("setup_ip", ip, True)
    TeamActivity.record(client, "owner_created", actor=owner, device=device, target=owner.name)
    return signed_in_json(owner, device, session, access, refresh)


def _recover_owner_device(owner, setup, data, ip):
    """The owner already exists but is on a new PC/phone (or lost the old
    one), so no approved owner device is left to approve it. A fresh
    setup code from FitDesk support plus the owner's own mobile and
    password approves this device. Being vendor-authorised, it may go
    over the device limit; the owner can then remove the old device."""
    phone = clean_phone(data.get("phone"))
    if phone != owner.phone or not owner.check_password(data.get("password") or ""):
        note_attempt("setup_ip", ip, False)
        raise ApiError(409, "owner_exists",
                       "This gym already has an owner. To approve this device, enter the owner's own mobile number and password.")
    if not owner.is_active:
        raise ApiError(403, "user_disabled", "The owner login is disabled. Contact FitDesk support.")
    dev = clean_device(data)
    with transaction.atomic():
        locked_profile(owner.client)
        setup = OwnerSetupCode.objects.select_for_update().get(pk=setup.pk)
        if not setup.is_usable:
            raise ApiError(400, "bad_setup_code", "This setup code has already been used.")
        device = find_or_register_device(owner.client, owner, dev)
        if not device.usable_by(owner):
            raise ApiError(409, "device_in_use", "This device belongs to another staff member.")
        if not device.is_approved:
            device.status, device.decided_by, device.decided_at = StaffDevice.STATUS_APPROVED, owner, timezone.now()
            device.save(update_fields=["status", "decided_by", "decided_at"])
        setup.used_at = timezone.now()
        setup.save(update_fields=["used_at"])
        session, access, refresh = StaffSession.start(owner, device)
        owner.last_login_at = timezone.now()
        owner.save(update_fields=["last_login_at"])
    note_attempt("setup_ip", ip, True)
    TeamActivity.record(owner.client, "owner_device_recovered", actor=owner, device=device, target=device.name)
    return signed_in_json(owner, device, session, access, refresh)

def accept_invite(data, ip):
    check_rate("invite_ip", ip)
    code = str(data.get("invite_code") or "").strip().upper().replace("-", "").replace(" ", "")
    invite = Invite.objects.select_related("client").filter(code_hash=sha256(code)).first() if code else None
    if invite is None or not invite.is_usable:
        note_attempt("invite_ip", ip, False)
        raise ApiError(400, "bad_invite_code", "This invite code is wrong or has expired. Ask the owner for a new one.")
    client = invite.client
    require_active_licence(client)
    password = clean_password(data.get("password"))
    name = clean_name(data.get("name") or invite.name)
    dev = clean_device(data)
    with transaction.atomic():
        locked_profile(client)
        invite = Invite.objects.select_for_update().get(pk=invite.pk)
        if not invite.is_usable:
            raise ApiError(400, "bad_invite_code", "This invite has already been used.")
        if StaffUser.objects.filter(client=client, phone=invite.phone).exists():
            raise ApiError(409, "phone_taken", "A login with this mobile number already exists in this gym.")
        user = StaffUser(client=client, name=name, phone=invite.phone, role=invite.role, linked_record=invite.linked_record)
        user.set_password(password)
        user.save()
        if invite.salary_access != "none":
            set_salary_access(user, invite.salary_access)
        invite.status, invite.accepted_user, invite.accepted_at = Invite.STATUS_ACCEPTED, user, timezone.now()
        invite.save(update_fields=["status", "accepted_user", "accepted_at"])
        device = find_or_register_device(client, user, dev)
    note_attempt("invite_ip", ip, True)
    TeamActivity.record(client, "invite_accepted", actor=user, device=device, target=user.name, role=user.role)
    out = {"user": user_json(user), "device": device_json(device), "gym": {"name": client.gym_name}}
    if device.is_approved and device.usable_by(user):
        session, access, refresh = StaffSession.start(user, device)
        return {"status": "signed_in", **signed_in_json(user, device, session, access, refresh)}
    return {"status": "device_pending", **out}


def login(data, ip):
    check_rate("login_ip", ip)
    phone = clean_phone(data.get("phone"))
    check_rate("login_phone", phone)
    password = data.get("password") or ""
    gym_code = str(data.get("gym_code") or "").strip().upper()
    dev = clean_device(data)

    candidates = StaffUser.objects.select_related("client").filter(phone=phone)
    if gym_code:
        candidates = candidates.filter(client__gym_profile__gym_code=gym_code)
    matches = [u for u in candidates if u.check_password(password)]
    if not matches:
        note_attempt("login_phone", phone, False)
        note_attempt("login_ip", ip, False)
        raise ApiError(401, "bad_credentials", "Mobile number or password is wrong.")
    if len(matches) > 1:
        raise ApiError(409, "choose_gym", "This number has logins at more than one gym. Choose one.",
                       gyms=[{"name": u.client.gym_name, "gym_code": GymProfile.for_client(u.client).gym_code} for u in matches])
    user = matches[0]
    note_attempt("login_phone", phone, True)
    if not user.is_active:
        raise ApiError(403, "user_disabled", "Your access to this gym has been turned off by the owner.")
    require_active_licence(user.client)
    device = find_or_register_device(user.client, user, dev)
    device_gate(device, user)
    session, access, refresh = StaffSession.start(user, device)
    user.last_login_at = timezone.now()
    user.save(update_fields=["last_login_at"])
    TeamActivity.record(user.client, "signed_in", actor=user, device=device, target=device.name)
    return signed_in_json(user, device, session, access, refresh)


def device_status(data):
    dev = clean_device(data)
    devices = StaffDevice.objects.select_related("client").filter(installation_id=dev["installation_id"])
    devices = [d for d in devices if d.check_secret(dev["secret"])]
    if not devices:
        raise ApiError(404, "device_unknown", "This device hasn't been registered yet.")
    return {"devices": [{"gym": d.client.gym_name, **device_json(d)} for d in devices]}


def refresh(data):
    token = str(data.get("refresh_token") or "")
    dev = clean_device(data)
    session = (StaffSession.objects.select_related("user", "user__client", "device")
               .filter(refresh_hash=sha256(token)).first()) if token else None
    if (session is None or session.revoked_at is not None or session.refresh_expires_at <= timezone.now()
            or session.device.installation_id != dev["installation_id"] or not session.device.check_secret(dev["secret"])):
        raise ApiError(401, "session_ended", "Please sign in again.")
    user, device = session.user, session.device
    if not user.is_active:
        session.revoke("user_disabled")
        raise ApiError(403, "user_disabled", "Your access to this gym has been turned off by the owner.")
    if not device.is_approved or not device.usable_by(user):
        session.revoke("device_not_approved")
        raise ApiError(403, "device_not_approved", "This device is no longer approved for this gym.")
    require_active_licence(user.client)
    access, new_refresh = session.rotate()
    return {**tokens_json(session, access, new_refresh), "user": user_json(user, with_perms=True),
            "licence": licence_json(user.client)}


def pin_switch(data, ip):
    """Hand a shared front-desk device to another staff member using
    their PIN, without a full sign-out."""
    dev = clean_device(data)
    check_rate("pin_device", dev["installation_id"])
    device = (StaffDevice.objects.select_related("client")
              .filter(installation_id=dev["installation_id"], status=StaffDevice.STATUS_APPROVED, shared=True)
              .first())
    if device is None or not device.check_secret(dev["secret"]):
        raise ApiError(403, "not_shared_device", "PIN switching only works on an approved shared device.")
    user = StaffUser.objects.filter(client=device.client, id=data.get("user_id")).first()
    if user is None or not user.check_pin(str(data.get("pin") or "")):
        note_attempt("pin_device", dev["installation_id"], False)
        raise ApiError(401, "bad_pin", "Wrong PIN.")
    if not user.is_active:
        raise ApiError(403, "user_disabled", "Your access to this gym has been turned off by the owner.")
    require_active_licence(device.client)
    note_attempt("pin_device", dev["installation_id"], True)
    session, access, refresh_tok = StaffSession.start(user, device)
    user.last_login_at = timezone.now()
    user.save(update_fields=["last_login_at"])
    TeamActivity.record(device.client, "pin_switch", actor=user, device=device, target=device.name)
    return signed_in_json(user, device, session, access, refresh_tok)


def pin_users(data):
    """Names of staff who have a PIN, for the shared-device switch screen."""
    dev = clean_device(data)
    device = StaffDevice.objects.filter(installation_id=dev["installation_id"], status=StaffDevice.STATUS_APPROVED,
                                        shared=True).first()
    if device is None or not device.check_secret(dev["secret"]):
        raise ApiError(403, "not_shared_device", "PIN switching only works on an approved shared device.")
    users = StaffUser.objects.filter(client=device.client, status=StaffUser.STATUS_ACTIVE).exclude(pin_hash="")
    return {"users": [{"id": u.id, "name": u.name, "role": u.role} for u in users]}


def create_invite(actor, data, request_device):
    name, phone = clean_name(data.get("name")), clean_phone(data.get("phone"))
    role = str(data.get("role") or "")
    if role not in INVITABLE_ROLES:
        raise ApiError(400, "bad_role", f"Role must be one of {INVITABLE_ROLES}.")
    sal = str(data.get("salary_access") or "none")
    if sal not in SALARY_ACCESS:
        raise ApiError(400, "bad_salary_access", "Salary access must be none, view or manage.")
    client = actor.client
    with transaction.atomic():
        profile = locked_profile(client)
        if StaffUser.objects.filter(client=client, phone=phone).exists():
            raise ApiError(409, "phone_taken", "Someone in this gym already has a login with this number.")
        Invite.objects.filter(client=client, phone=phone, status=Invite.STATUS_PENDING).update(status=Invite.STATUS_CANCELLED)
        used = users_used(client)
        if used >= profile.max_users:
            raise ApiError(409, "user_limit_reached",
                           f"All {profile.max_users} user slots are in use. Disable someone or upgrade the plan.",
                           used=used, limit=profile.max_users)
        invite, code = Invite.new(client=client, name=name, phone=phone, role=role,
                                  linked_record=str(data.get("linked_record") or "")[:60], invited_by=actor,
                                  salary_access=sal)
    TeamActivity.record(client, "invite_sent", actor=actor, device=request_device, target=name, role=role)
    pretty = f"{code[:4]}-{code[4:]}"
    share = (f"Hi {name}, you've been added to {client.gym_name} on FitDesk Pro as {role}. "
             f"Install the app and enter invite code {pretty} (valid {Invite.TTL_HOURS} hours).")
    return {"invite": invite_json(invite), "invite_code": pretty, "share_text": share}


def team_summary(client):
    profile = GymProfile.for_client(client)
    used, free = device_counts(client, profile)
    return {"users": {"used": users_used(client), "limit": profile.max_users},
            "devices": {"used": used, "limit": profile.max_devices, "owner_free_in_use": free},
            "pending_devices": StaffDevice.objects.filter(client=client, status=StaffDevice.STATUS_PENDING).count(),
            "pending_invites": Invite.objects.filter(client=client, status=Invite.STATUS_PENDING,
                                                     expires_at__gt=timezone.now()).count(),
            "licence": licence_json(client), "gym_code": profile.gym_code}


def gym_object(model, client, pk):
    """Fetch by id but only within the caller's gym; another gym's id
    looks exactly like a missing one."""
    obj = model.objects.filter(client=client, pk=pk).first()
    if obj is None:
        raise ApiError(404, "not_found", "Not found.")
    return obj

