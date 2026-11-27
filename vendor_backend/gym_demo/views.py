"""POST /api/v1/auth/demo/  {"role": "owner|manager|receptionist|trainer", device fields}

Signs a lead straight into the demo gym: no password, no device approval.
Only the demo gym can be entered this way."""
from django.core.cache import cache
from django.db import transaction

from gym_accounts import services as AS
from gym_accounts.api import ApiError, client_ip, endpoint, json_body, ok
from gym_accounts.models import StaffDevice, StaffSession, StaffUser, TeamActivity

from .models import DemoGym

ROLES = ["owner", "manager", "receptionist", "trainer"]
PER_IP_PER_HOUR = 60


@endpoint("POST")
def demo_sign_in(request):
    k = "demo-ip:" + client_ip(request)
    n = cache.get(k, 0)
    if n >= PER_IP_PER_HOUR:
        raise ApiError(429, "too_many", "Too many demo sign-ins from here. Try again in an hour.")
    cache.set(k, n + 1, 3600)
    demo = DemoGym.objects.select_related("client").first()
    if demo is None:
        raise ApiError(404, "no_demo", "The demo isn't available right now. Please try again later.")
    data = json_body(request)
    role = data.get("role") or "owner"
    if role not in ROLES:
        raise ApiError(400, "bad_role", "Unknown role.")
    client = demo.client
    user = StaffUser.objects.filter(client=client, role=role, status=StaffUser.STATUS_ACTIVE).order_by("id").first()
    if user is None:
        raise ApiError(404, "no_demo", "The demo isn't available right now. Please try again later.")
    dev = AS.clean_device(data)
    with transaction.atomic():
        device = AS.find_or_register_device(client, user, dev)
        if not device.is_approved or not device.shared:          # demo devices: approved and shared at once
            device.status, device.shared = StaffDevice.STATUS_APPROVED, True
            device.save(update_fields=["status", "shared"])
    AS.device_gate(device, user)
    session, access, refresh = StaffSession.start(user, device)
    TeamActivity.record(client, "signed_in", actor=user, device=device, target=device.name, demo=True)
    return ok(AS.signed_in_json(user, device, session, access, refresh))
