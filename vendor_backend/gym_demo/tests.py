import json
import secrets
from datetime import timedelta
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gym_accounts.models import StaffDevice
from gym_core.models import Bill, CheckIn, Member
from licensing.models import Client

from .models import DemoGym
from .seed import IST, build


def dev():
    return {"installation_id": "inst-" + secrets.token_hex(8), "device_secret": secrets.token_urlsafe(40),
            "name": "Lead's phone", "platform": "web"}


class DemoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.gym = build(seed=7)

    def post(self, path, data, token=None):
        kw = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        r = self.client.post(path, json.dumps(data), content_type="application/json", **kw)
        return r.status_code, r.json()

    def get(self, path, token):
        r = self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {token}")
        return r.status_code, r.json()

    def sign_in(self, role="owner", device=None):
        return self.post("/api/v1/auth/demo/", {"role": role, "device": device or dev()})

    def test_data_is_believable(self):
        t = timezone.localdate(timezone=IST)
        self.assertEqual(Member.objects.filter(client=self.gym).count(), 46)
        from gym_core.models import Membership
        self.assertGreaterEqual(Membership.objects.filter(client=self.gym, trainer__staff_user__role="trainer").count(), 4)
        self.assertGreater(Bill.objects.filter(client=self.gym).count(), 46)
        self.assertTrue(CheckIn.objects.filter(client=self.gym, local_date=t - timedelta(days=1)).exists())
        self.assertTrue(Member.objects.filter(client=self.gym, date_of_birth__month=t.month, date_of_birth__day=t.day).exists())
        st, j = self.sign_in()
        tok = j["access_token"]
        st, d = self.get("/api/v1/dashboard/insights/", tok)
        self.assertEqual(st, 200)

    def test_rebuild_replaces_the_old_demo(self):
        old = self.gym.id
        new = build(seed=8)
        self.assertFalse(Client.objects.filter(pk=old).exists())
        self.assertEqual(DemoGym.objects.get().client_id, new.id)
        self.assertEqual(Client.objects.filter(gym_name="Pulse Fitness Club").count(), 1)

    def test_one_tap_sign_in_every_role_no_approval(self):
        d = dev()
        for role in ("owner", "manager", "receptionist", "trainer"):
            st, j = self.sign_in(role, d)
            self.assertEqual(st, 200, j)
            self.assertEqual(j["user"]["role"], role)
            self.assertTrue(j["gym"]["demo"])
            st, me = self.get("/api/v1/me/", j["access_token"])
            self.assertEqual(st, 200)
            self.assertTrue(me["gym"]["demo"])
        sd = StaffDevice.objects.get(installation_id=d["installation_id"])
        self.assertEqual((sd.status, sd.shared), ("approved", True))
        self.assertEqual(self.sign_in("superadmin")[0], 400)

    def test_blocked_and_allowed(self):
        tok = self.sign_in()[1]["access_token"]
        for path, body in [("/api/v1/me/password/", {"current_password": "x", "new_password": "y"}),
                           ("/api/v1/gym/settings/", {"gym_name": "Hacked"}),
                           ("/api/v1/team/invites/", {"name": "X", "phone": "9876543210", "role": "manager"}),
                           ("/api/v1/support/", {"subject": "hi", "message": "hi"})]:
            st, j = self.post(path, body, tok)
            self.assertEqual((st, j["error"]), (403, "demo_blocked"), path)
        self.assertEqual(self.client.get("/api/v1/data-export/", HTTP_AUTHORIZATION=f"Bearer {tok}").status_code, 403)
        st, j = self.post("/api/v1/expenses/", {"category": "Cleaning", "title": "Mop", "amount": "250", "mode": "Cash"}, tok)
        self.assertIn(st, (200, 201), j)                      # normal work is allowed
        self.assertEqual(self.get("/api/v1/gym/settings/", tok)[0], 200)   # reading settings is fine

    def test_whatsapp_links_have_no_number_in_demo(self):
        tok = self.sign_in()[1]["access_token"]
        b = Bill.objects.filter(client=self.gym).first()
        st, j = self.get(f"/api/v1/bills/{b.id}/", tok)
        self.assertTrue(j["bill"]["whatsapp"]["link"].startswith("https://wa.me/?text="), j["bill"]["whatsapp"]["link"])

    def test_real_gyms_untouched(self):
        from gym_core.rules import whatsapp_link
        self.assertTrue(whatsapp_link("+919876543210", "hi").startswith("https://wa.me/919876543210"))

    def test_nightly_rebuild_once_a_day(self):
        out = StringIO()
        call_command("run_daily_jobs", stdout=out)
        self.assertNotIn("Demo gym rebuilt", out.getvalue())          # built today already
        DemoGym.objects.update(built_at=timezone.now() - timedelta(days=1))
        call_command("run_daily_jobs", stdout=out)
        self.assertIn("Demo gym rebuilt", out.getvalue())

    def test_no_demo_no_button(self):
        DemoGym.objects.all().delete()
        cache.clear()
        st, j = self.sign_in()
        self.assertEqual((st, j["error"]), (404, "no_demo"))

    def test_control_panel_keeps_demo_out_of_numbers(self):
        from django.contrib.auth.models import User
        User.objects.create_superuser("s", "s@x.in", "pw-Strong-1")
        self.client.login(username="s", password="pw-Strong-1")
        from control_panel import services as S
        r = [x for x in S.gym_rows() if x["c"].id == self.gym.id][0]
        self.assertEqual(r["badge"], "Demo")
        self.assertEqual(S.today_summary()["gyms_active"], 0)
