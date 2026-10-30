"""Phase 2 acceptance tests: staff sign-in, roles, invites, device
approval, limits, gym isolation, revocation, and that the legacy
/api/validate-license/ contract is untouched."""

import json
import secrets
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from licensing.models import Client, Device, License

from .models import GymProfile, Invite, OwnerSetupCode, StaffDevice, StaffSession, StaffUser, TeamActivity

PW = "Strong-pass-2026"


def device(name="Test PC", platform="windows"):
    return {"installation_id": "inst-" + secrets.token_hex(8), "device_secret": secrets.token_urlsafe(40),
            "name": name, "platform": platform, "app_version": "2.0.0"}


class Api(TestCase):
    def post(self, url, data=None, token=None, ip="10.0.0.1"):
        h = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        r = self.client.post("/api/v1/" + url, json.dumps(data or {}), content_type="application/json", REMOTE_ADDR=ip, **h)
        return r.status_code, r.json()

    def get(self, url, token=None):
        h = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        r = self.client.get("/api/v1/" + url, **h)
        return r.status_code, r.json()

    def make_gym(self, name="P&P Fitness", days=200, **profile):
        c = Client.objects.create(gym_name=name, owner_name="Owner", phone="9000000000")
        lic = License.objects.create(client=c, expiry_date=date.today() + timedelta(days=days))
        if profile:
            GymProfile.objects.update_or_create(client=c, defaults=profile)
        return c, lic

    def setup_owner(self, client, lic, phone="9800000001", dev=None):
        _, code = OwnerSetupCode.issue(client)
        dev = dev or device("Owner Mac", "macos")
        s, body = self.post("auth/owner-setup/", {"license_key": lic.key, "setup_code": code, "name": "Owner",
                                                  "phone": phone, "password": PW, "device": dev})
        self.assertEqual(s, 201, body)
        return body["access_token"], dev, body

    def invite_and_join(self, owner_token, phone, role="receptionist", dev=None, approve=True):
        s, inv = self.post("team/invites/", {"name": f"Staff {phone[-3:]}", "phone": phone, "role": role}, owner_token)
        self.assertEqual(s, 201, inv)
        dev = dev or device()
        s, joined = self.post("auth/join/", {"invite_code": inv["invite_code"], "password": PW, "device": dev})
        self.assertEqual(s, 201, joined)
        self.assertEqual(joined["status"], "device_pending")
        if approve:
            s, b = self.post(f"team/devices/{joined['device']['id']}/approve/", {}, owner_token)
            self.assertEqual(s, 200, b)
        return dev, joined

    def login(self, phone, dev, password=PW, ip="10.0.0.1"):
        return self.post("auth/login/", {"phone": phone, "password": password, "device": dev}, ip=ip)


class OwnerSetupTests(Api):
    def test_setup_requires_valid_code_for_that_gym(self):
        c1, l1 = self.make_gym()
        c2, l2 = self.make_gym("Other Gym")
        _, other_code = OwnerSetupCode.issue(c2)
        s, b = self.post("auth/owner-setup/", {"license_key": l1.key, "setup_code": other_code, "name": "X",
                                               "phone": "9800000001", "password": PW, "device": device()})
        self.assertEqual((s, b["error"]), (400, "bad_setup_code"))
        s, b = self.post("auth/owner-setup/", {"license_key": l1.key, "setup_code": "WRONGCODE1", "name": "X",
                                               "phone": "9800000001", "password": PW, "device": device()})
        self.assertEqual(s, 400)
        self.assertFalse(StaffUser.objects.exists())

    def test_setup_creates_owner_with_approved_device_and_is_single_use(self):
        c, lic = self.make_gym()
        _, code = OwnerSetupCode.issue(c)
        payload = {"license_key": lic.key, "setup_code": code, "name": "Owner", "phone": "98000 00001",
                   "password": PW, "device": device("Owner Mac", "macos")}
        s, b = self.post("auth/owner-setup/", payload)
        self.assertEqual(s, 201)
        self.assertEqual(b["user"]["role"], "owner")
        self.assertEqual(b["user"]["phone"], "+919800000001")
        self.assertEqual(b["device"]["status"], "approved")
        self.assertIn("team", b["user"]["permissions"])
        s, b = self.post("auth/owner-setup/", {**payload, "device": device()})
        self.assertEqual((s, b["error"]), (400, "bad_setup_code"))

    def test_second_owner_refused(self):
        c, lic = self.make_gym()
        self.setup_owner(c, lic)
        _, code = OwnerSetupCode.issue(c)
        s, b = self.post("auth/owner-setup/", {"license_key": lic.key, "setup_code": code, "name": "Y",
                                               "phone": "9800000002", "password": PW, "device": device()})
        self.assertEqual((s, b["error"]), (409, "owner_exists"))

    def test_owner_on_new_device_recovers_with_fresh_code(self):
        c, lic = self.make_gym(max_devices=1, owner_free_devices=0)
        self.setup_owner(c, lic)
        new_pc = device("Owner new PC")
        s, b = self.login("9800000001", new_pc)
        self.assertEqual((s, b["error"]), (403, "device_pending"))
        _, code = OwnerSetupCode.issue(c)
        s, b = self.post("auth/owner-setup/", {"license_key": lic.key, "setup_code": code, "name": "ignored",
                                               "phone": "9800000001", "password": "wrong-pass-123", "device": new_pc})
        self.assertEqual((s, b["error"]), (409, "owner_exists"))
        s, b = self.post("auth/owner-setup/", {"license_key": lic.key, "setup_code": code, "name": "ignored",
                                               "phone": "9800000001", "password": PW, "device": new_pc})
        self.assertEqual(s, 201, b)
        self.assertEqual((b["user"]["role"], b["device"]["status"]), ("owner", "approved"))
        self.assertEqual(StaffUser.objects.filter(client=c).count(), 1)
        self.assertEqual(self.login("9800000001", new_pc)[0], 200)
        s, b = self.post("auth/owner-setup/", {"license_key": lic.key, "setup_code": code, "name": "x",
                                               "phone": "9800000001", "password": PW, "device": device()})
        self.assertEqual((s, b["error"]), (400, "bad_setup_code"))

    def test_weak_password_refused(self):
        c, lic = self.make_gym()
        _, code = OwnerSetupCode.issue(c)
        s, b = self.post("auth/owner-setup/", {"license_key": lic.key, "setup_code": code, "name": "Y",
                                               "phone": "9800000002", "password": "12345678", "device": device()})
        self.assertEqual((s, b["error"]), (400, "weak_password"))


class InviteAndDeviceTests(Api):
    def setUp(self):
        self.c, self.lic = self.make_gym()
        self.owner, self.owner_dev, _ = self.setup_owner(self.c, self.lic)

    def test_full_flow_invite_join_approve_login(self):
        s, inv = self.post("team/invites/", {"name": "Priya", "phone": "9900000530", "role": "receptionist"}, self.owner)
        self.assertEqual(s, 201)
        self.assertRegex(inv["invite_code"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertIn(inv["invite_code"], inv["share_text"])
        dev = device("Reception PC")
        s, j = self.post("auth/join/", {"invite_code": inv["invite_code"], "password": PW, "device": dev})
        self.assertEqual((s, j["status"]), (201, "device_pending"))
        s, b = self.login("9900000530", dev)
        self.assertEqual((s, b["error"]), (403, "device_pending"))
        s, b = self.post(f"team/devices/{j['device']['id']}/approve/", {}, self.owner)
        self.assertEqual(s, 200)
        s, b = self.login("9900000530", dev)
        self.assertEqual(s, 200, b)
        perms = b["user"]["permissions"]
        self.assertEqual(perms["billing"], ["view", "add"])
        self.assertEqual(perms["staff"], [])
        self.assertEqual(perms["team"], [])
        # invite code is single use
        s, b = self.post("auth/join/", {"invite_code": inv["invite_code"], "password": PW, "device": device()})
        self.assertEqual(s, 400)

    def test_expired_invite_refused(self):
        s, inv = self.post("team/invites/", {"name": "A", "phone": "9900000001", "role": "trainer"}, self.owner)
        Invite.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        s, b = self.post("auth/join/", {"invite_code": inv["invite_code"], "password": PW, "device": device()})
        self.assertEqual((s, b["error"]), (400, "bad_invite_code"))

    def test_rejected_device_cannot_sign_in(self):
        dev, j = self.invite_and_join(self.owner, "9900000002", approve=False)
        s, _ = self.post(f"team/devices/{j['device']['id']}/reject/", {}, self.owner)
        self.assertEqual(s, 200)
        s, b = self.login("9900000002", dev)
        self.assertEqual((s, b["error"]), (403, "device_blocked"))

    def test_device_secret_must_match(self):
        dev, _ = self.invite_and_join(self.owner, "9900000003")
        stolen = {**dev, "device_secret": secrets.token_urlsafe(40)}
        s, b = self.login("9900000003", stolen)
        self.assertEqual((s, b["error"]), (403, "device_mismatch"))

    def test_personal_device_not_usable_by_other_staff_until_shared(self):
        dev_a, _ = self.invite_and_join(self.owner, "9900000004")
        _, _ = self.invite_and_join(self.owner, "9900000005")
        s, b = self.login("9900000005", dev_a)
        self.assertEqual((s, b["error"]), (403, "device_personal"))
        d = StaffDevice.objects.get(installation_id=dev_a["installation_id"])
        s, _ = self.post(f"team/devices/{d.id}/set-shared/", {"shared": True}, self.owner)
        self.assertEqual(s, 200)
        s, b = self.login("9900000005", dev_a)
        self.assertEqual(s, 200)

    def test_pin_switch_on_shared_device(self):
        dev, _ = self.invite_and_join(self.owner, "9900000006")
        d = StaffDevice.objects.get(installation_id=dev["installation_id"])
        self.post(f"team/devices/{d.id}/set-shared/", {"shared": True}, self.owner)
        _, b = self.invite_and_join(self.owner, "9900000007")
        s, tok = self.login("9900000007", dev)
        self.assertEqual(s, 200)
        s, _ = self.post("me/pin/", {"password": PW, "pin": "4821"}, tok["access_token"])
        self.assertEqual(s, 200)
        s, users = self.post("auth/pin-users/", {"device": dev})
        uid = [u["id"] for u in users["users"]][0]
        s, b = self.post("auth/pin-switch/", {"device": dev, "user_id": uid, "pin": "0000"})
        self.assertEqual((s, b["error"]), (401, "bad_pin"))
        s, b = self.post("auth/pin-switch/", {"device": dev, "user_id": uid, "pin": "4821"})
        self.assertEqual(s, 200, b)
        self.assertEqual(b["user"]["id"], uid)


class LimitTests(Api):
    def test_user_limit_counts_owner_and_pending_invites(self):
        c, lic = self.make_gym(max_users=3)
        owner, _, _ = self.setup_owner(c, lic)
        self.assertEqual(self.post("team/invites/", {"name": "A", "phone": "9900000011", "role": "trainer"}, owner)[0], 201)
        self.assertEqual(self.post("team/invites/", {"name": "B", "phone": "9900000012", "role": "trainer"}, owner)[0], 201)
        s, b = self.post("team/invites/", {"name": "C", "phone": "9900000013", "role": "trainer"}, owner)
        self.assertEqual((s, b["error"], b["limit"]), (409, "user_limit_reached", 3))

    def test_device_limit_with_free_owner_device(self):
        c, lic = self.make_gym(max_users=10, max_devices=1, owner_free_devices=1)
        owner, _, _ = self.setup_owner(c, lic)
        s, summary = self.get("team/summary/", owner)
        self.assertEqual(summary["devices"], {"used": 0, "limit": 1, "owner_free_in_use": 1})
        self.invite_and_join(owner, "9900000021")          # takes the one paid slot
        _, j = self.invite_and_join(owner, "9900000022", approve=False)
        s, b = self.post(f"team/devices/{j['device']['id']}/approve/", {}, owner)
        self.assertEqual((s, b["error"]), (409, "device_limit_reached"))
        # removing a device frees the slot
        first = StaffDevice.objects.get(registered_by__phone="+919900000021")
        self.post(f"team/devices/{first.id}/remove/", {}, owner)
        s, _ = self.post(f"team/devices/{j['device']['id']}/approve/", {}, owner)
        self.assertEqual(s, 200)

    def test_owners_second_device_needs_approval_and_a_slot(self):
        c, lic = self.make_gym(max_devices=1, owner_free_devices=1)
        owner, _, _ = self.setup_owner(c, lic)
        phone_dev = device("Owner iPhone", "ios")
        s, b = self.login("9800000001", phone_dev)
        self.assertEqual((s, b["error"]), (403, "device_pending"))
        s, _ = self.post(f"team/devices/{b['device']['id']}/approve/", {}, owner)
        self.assertEqual(s, 200)  # uses the single paid slot
        self.assertEqual(self.login("9800000001", phone_dev)[0], 200)


class IsolationTests(Api):
    """User A of gym 1 can't read or change anything of gym 2."""

    def setUp(self):
        self.c1, l1 = self.make_gym("Gym One")
        self.c2, l2 = self.make_gym("Gym Two")
        self.o1, _, _ = self.setup_owner(self.c1, l1, phone="9800000101")
        self.o2, _, _ = self.setup_owner(self.c2, l2, phone="9800000202")
        _, self.j2 = self.invite_and_join(self.o2, "9900000202", approve=False)
        self.u2 = StaffUser.objects.get(client=self.c2, phone="+919900000202")

    def test_lists_only_show_own_gym(self):
        _, users = self.get("team/users/", self.o1)
        self.assertEqual({u["phone"] for u in users["users"]}, {"+919800000101"})
        _, devs = self.get("team/devices/", self.o1)
        self.assertTrue(all(d["registered_by"]["name"] == "Owner" for d in devs["devices"]))
        self.assertEqual(len(devs["devices"]), 1)
        _, act = self.get("team/activity/", self.o1)
        self.assertFalse(any("202" in (a["target"] or "") for a in act["activity"]))

    def test_cannot_touch_other_gyms_objects(self):
        dev_id = self.j2["device"]["id"]
        for url in [f"team/devices/{dev_id}/approve/", f"team/devices/{dev_id}/remove/",
                    f"team/users/{self.u2.id}/disable/", f"team/users/{self.u2.id}/update/",
                    f"team/users/{self.u2.id}/permissions/", f"team/users/{self.u2.id}/reset-password/"]:
            s, b = self.post(url, {"name": "hacked", "overrides": [], "temporary_password": PW}, self.o1)
            self.assertEqual(s, 404, url)
        s, _ = self.get(f"team/users/{self.u2.id}/", self.o1)
        self.assertEqual(s, 404)
        self.assertEqual(StaffDevice.objects.get(pk=dev_id).status, "pending")
        self.assertEqual(StaffUser.objects.get(pk=self.u2.pk).status, "active")

    def test_same_phone_in_two_gyms_must_choose(self):
        _, inv = self.post("team/invites/", {"name": "Shared trainer", "phone": "9900000999", "role": "trainer"}, self.o1)
        self.post("auth/join/", {"invite_code": inv["invite_code"], "password": PW, "device": device()})
        _, inv = self.post("team/invites/", {"name": "Shared trainer", "phone": "9900000999", "role": "trainer"}, self.o2)
        self.post("auth/join/", {"invite_code": inv["invite_code"], "password": PW, "device": device()})
        s, b = self.login("9900000999", device())
        self.assertEqual((s, b["error"]), (409, "choose_gym"))
        self.assertEqual(len(b["gyms"]), 2)
        code = GymProfile.objects.get(client=self.c1).gym_code
        s, b = self.post("auth/login/", {"phone": "9900000999", "password": PW, "gym_code": code, "device": device()})
        self.assertEqual((s, b["error"]), (403, "device_pending"))
        self.assertEqual(StaffDevice.objects.get(pk=b["device"]["id"]).client, self.c1)


class RevocationAndRoleTests(Api):
    def setUp(self):
        self.c, self.lic = self.make_gym()
        self.owner, _, _ = self.setup_owner(self.c, self.lic)
        self.dev, _ = self.invite_and_join(self.owner, "9900000301")
        s, b = self.login("9900000301", self.dev)
        self.tok, self.refresh_tok = b["access_token"], b["refresh_token"]
        self.user = StaffUser.objects.get(phone="+919900000301")

    def test_receptionist_cannot_use_team_screens(self):
        for url in ["team/summary/", "team/users/", "team/devices/", "team/activity/"]:
            s, b = self.get(url, self.tok)
            self.assertEqual((s, b["error"]), (403, "owner_only"), url)
        s, b = self.post("team/invites/", {"name": "x", "phone": "9900000302", "role": "manager"}, self.tok)
        self.assertEqual(s, 403)

    def test_disabled_user_refused_on_next_request(self):
        self.assertEqual(self.get("me/", self.tok)[0], 200)
        self.post(f"team/users/{self.user.id}/disable/", {}, self.owner)
        s, b = self.get("me/", self.tok)
        self.assertEqual(s, 401)  # session already revoked
        s, b = self.login("9900000301", self.dev)
        self.assertEqual((s, b["error"]), (403, "user_disabled"))

    def test_removed_device_refused_on_next_request_and_refresh(self):
        d = StaffDevice.objects.get(installation_id=self.dev["installation_id"])
        self.post(f"team/devices/{d.id}/remove/", {}, self.owner)
        self.assertEqual(self.get("me/", self.tok)[0], 401)
        s, _ = self.post("auth/refresh/", {"refresh_token": self.refresh_tok, "device": self.dev})
        self.assertEqual(s, 401)

    def test_device_status_change_without_revocation_still_blocks(self):
        StaffDevice.objects.filter(installation_id=self.dev["installation_id"]).update(status="removed")
        s, b = self.get("me/", self.tok)
        self.assertEqual((s, b["error"]), (403, "device_not_approved"))

    def test_owner_cannot_be_disabled_or_overridden(self):
        owner = StaffUser.objects.get(role="owner")
        s, b = self.post(f"team/users/{owner.id}/disable/", {}, self.owner)
        self.assertEqual((s, b["error"]), (400, "owner_protected"))

    def test_overrides_apply_but_never_grant_team(self):
        s, b = self.post(f"team/users/{self.user.id}/permissions/",
                         {"overrides": [{"module": "team", "action": "view", "allowed": True}]}, self.owner)
        self.assertEqual((s, b["error"]), (400, "owner_only_module"))
        s, b = self.post(f"team/users/{self.user.id}/permissions/",
                         {"overrides": [{"module": "reports", "action": "view", "allowed": True},
                                        {"module": "billing", "action": "add", "allowed": False}]}, self.owner)
        self.assertEqual(s, 200)
        _, me = self.get("me/", self.tok)
        self.assertEqual(me["user"]["permissions"]["reports"], ["view"])
        self.assertEqual(me["user"]["permissions"]["billing"], ["view"])

    def test_refresh_rotates(self):
        s, b = self.post("auth/refresh/", {"refresh_token": self.refresh_tok, "device": self.dev})
        self.assertEqual(s, 200)
        s, _ = self.post("auth/refresh/", {"refresh_token": self.refresh_tok, "device": self.dev})
        self.assertEqual(s, 401)
        self.assertEqual(self.get("me/", b["access_token"])[0], 200)

    def test_expired_licence_locks_everyone(self):
        self.lic.expiry_date = date.today() - timedelta(days=1)
        self.lic.save()
        s, b = self.get("me/", self.tok)
        self.assertEqual((s, b["error"], b["licence_status"]), (403, "licence_inactive", "expired"))
        s, b = self.login("9900000301", self.dev)
        self.assertEqual((s, b["error"]), (403, "licence_inactive"))

    def test_suspended_licence_locks_owner_too(self):
        self.lic.status = License.STATUS_SUSPENDED
        self.lic.save()
        s, b = self.get("team/summary/", self.owner)
        self.assertEqual((s, b["licence_status"]), (403, "suspended"))

    def test_logout_and_activity_log(self):
        self.assertEqual(self.post("auth/logout/", {}, self.tok)[0], 200)
        self.assertEqual(self.get("me/", self.tok)[0], 401)
        actions = set(TeamActivity.objects.values_list("action", flat=True))
        self.assertTrue({"owner_created", "invite_sent", "invite_accepted", "device_approved", "signed_in"} <= actions)

    def test_password_change_ends_other_sessions(self):
        dev2_tok = self.tok
        s, b = self.post("me/password/", {"current_password": PW, "new_password": "Another-pass-2027"}, self.tok)
        self.assertEqual(s, 200)
        self.assertEqual(self.get("me/", dev2_tok)[0], 401)
        self.assertEqual(self.get("me/", b["access_token"])[0], 200)


class RateLimitTests(Api):
    def test_login_locks_after_five_failures(self):
        c, lic = self.make_gym()
        _, dev, _ = self.setup_owner(c, lic)
        for _ in range(5):
            self.assertEqual(self.login("9800000001", dev, password="wrong-one-1")[0], 401)
        s, b = self.login("9800000001", dev)
        self.assertEqual((s, b["error"]), (429, "too_many_attempts"))


class LegacyContractTests(Api):
    """The installed v1.x desktop must see exactly what it saw before."""

    def test_validate_license_unchanged_and_staff_devices_separate(self):
        c, lic = self.make_gym()
        self.setup_owner(c, lic)
        r = self.client.post("/api/validate-license/", json.dumps({"license_key": lic.key, "machine_id": "legacy-pc"}),
                             content_type="application/json")
        body = r.json()
        self.assertEqual(r.status_code, 200)
        for k in ("valid", "status", "expiry_date", "days_until_expiry", "gym_name", "message"):
            self.assertIn(k, body)
        self.assertTrue(body["valid"])
        # Staff devices never consume the legacy desktop's licensing.Device slots.
        self.assertEqual(Device.objects.filter(license=lic).count(), 1)
        self.assertEqual(lic.max_devices, 1)
