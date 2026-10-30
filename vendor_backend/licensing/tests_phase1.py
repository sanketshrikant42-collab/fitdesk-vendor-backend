"""Phase 1 hardening (29 Oct 2026): key guessing is slowed per network
address, validate-license needs a machine_id, correct keys are never
slowed, and the health check works."""
from datetime import date, timedelta

from django.test import TestCase, override_settings

from .models import Client, License


class KeyGuessingTests(TestCase):
    def setUp(self):
        self.c = Client.objects.create(gym_name="P&P Fitness", owner_name="O", phone="9000000000")
        self.lic = License.objects.create(client=self.c, expiry_date=date.today() + timedelta(days=100))

    def post(self, body, ip="1.2.3.4", url="/api/validate-license/"):
        return self.client.post(url, body, content_type="application/json", HTTP_X_FORWARDED_FOR=ip)

    def test_machine_id_required(self):
        r = self.post({"license_key": self.lic.key})
        self.assertEqual((r.status_code, r.json()["status"]), (400, "machine_id_required"))
        r = self.post({"license_key": self.lic.key, "machine_id": "abc123"})
        self.assertEqual((r.status_code, r.json()["valid"]), (200, True))

    def test_wrong_keys_are_limited_per_address(self):
        for i in range(20):
            self.assertEqual(self.post({"license_key": f"FDPX-0000-0000-{i:04d}", "machine_id": "m"}).status_code, 404)
        r = self.post({"license_key": "FDPX-0000-0000-9999", "machine_id": "m"})
        self.assertEqual((r.status_code, r.json()["status"]), (429, "rate_limited"))
        # even a right key waits from that address (no oracle while blocked) ...
        self.assertEqual(self.post({"license_key": self.lic.key, "machine_id": "m"}).status_code, 429)
        # ... but other gyms on other networks are unaffected
        r = self.post({"license_key": self.lic.key, "machine_id": "m"}, ip="5.6.7.8")
        self.assertEqual((r.status_code, r.json()["valid"]), (200, True))

    def test_correct_keys_never_count(self):
        for _ in range(30):
            self.assertEqual(self.post({"license_key": self.lic.key, "machine_id": "m"}).status_code, 200)

    def test_support_endpoints_limited(self):
        for i in range(20):
            r = self.client.get(f"/api/support-queries/?license_key=FDPX-0000-0000-{i:04d}", HTTP_X_FORWARDED_FOR="9.9.9.9")
            self.assertEqual(r.status_code, 404)
        r = self.client.get(f"/api/support-queries/?license_key={self.lic.key}", HTTP_X_FORWARDED_FOR="9.9.9.9")
        self.assertEqual(r.status_code, 429)
        r = self.post({"license_key": self.lic.key, "subject": "s", "message": "m"}, ip="9.9.9.9", url="/api/submit-query/")
        self.assertEqual(r.status_code, 429)

    def test_health_check(self):
        r = self.client.get("/healthz/")
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))


class SettingsTests(TestCase):
    def test_admins_parsing(self):
        from vendor_backend.settings import _admins
        self.assertEqual(_admins(""), [])
        self.assertEqual(_admins("a@b.com, Sanket <s@x.com>"), [("Admin", "a@b.com"), ("Sanket", "s@x.com")])

