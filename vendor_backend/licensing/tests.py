import json

from django.test import TestCase, RequestFactory, override_settings
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

from datetime import date, timedelta

from . import views
from .license_utils import resolve_active_client_and_license
from .models import Client, License, Device, Plan, Feature, PlanFeature, AuditLog


# A fixed throwaway Ed25519 keypair, generated once for these tests --
# NOT the real production signing key (that only ever exists in Render's
# environment, per licensing/signing_utils.py's docstring).
_TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
_TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key()
_TEST_PRIVATE_KEY_HEX = _TEST_PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption(),
).hex()


def _verify(license_payload, signature_hex):
    canonical = json.dumps(license_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        _TEST_PUBLIC_KEY.verify(bytes.fromhex(signature_hex), canonical)
        return True
    except InvalidSignature:
        return False


class LicenseFeatureDeviceTests(TestCase):
    """Covers the scenarios from the Phase 2 design doc's test plan:
    plan/feature resolution, device-limit enforcement, signature
    integrity, and -- most importantly -- that none of this changes
    behavior for a pre-existing, already-installed customer until the
    signing key is actually configured."""

    def setUp(self):
        self.rf = RequestFactory()
        self.client_obj = Client.objects.create(gym_name="P&P Fitness", owner_name="Owner", phone="9999999999")

        # A plan_id distinct from anything licensing/migrations/0005 seeds
        # (basic/standard/premium/legacy_full_access), so these tests
        # never collide with that data migration's rows.
        self.plan = Plan.objects.create(plan_id="test_plan", name="Test Plan")
        for code, name in [("members", "Members"), ("billing", "Billing"), ("whatsapp", "WhatsApp Receipts")]:
            feature, _ = Feature.objects.get_or_create(code=code, defaults={"name": name})
            if code != "whatsapp":
                PlanFeature.objects.create(plan=self.plan, feature=feature)

        self.license = License.objects.create(
            client=self.client_obj,
            expiry_date=date.today() + timedelta(days=300),
            plan=self.plan,
            max_devices=1,
        )

    def _validate(self, machine_id, device_name=""):
        body = json.dumps({"license_key": self.license.key, "machine_id": machine_id, "device_name": device_name})
        req = self.rf.post("/api/validate-license/", data=body, content_type="application/json")
        resp = views.validate_license(req)
        return resp.status_code, json.loads(resp.content)

    # -- Unsigned fallback (pre-rollout / signing key not yet set) ------

    def test_legacy_fields_present_even_without_signing_key_configured(self):
        """No LICENSE_SIGNING_PRIVATE_KEY set -> response looks exactly
        like the production API does today. This is what guarantees an
        already-installed FitDesk Pro build (and an unmodified Render
        deploy mid-rollout) keeps working unchanged."""
        status, data = self._validate("MACHINE-A")
        self.assertEqual(status, 200)
        self.assertTrue(data["valid"])
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["gym_name"], "P&P Fitness")
        self.assertNotIn("license_payload", data)
        self.assertNotIn("signature", data)

    # -- Signed payload, once configured ---------------------------------

    @override_settings(LICENSE_SIGNING_PRIVATE_KEY=_TEST_PRIVATE_KEY_HEX)
    def test_signed_payload_present_and_verifiable(self):
        status, data = self._validate("MACHINE-A")
        self.assertEqual(status, 200)
        self.assertIn("license_payload", data)
        self.assertIn("signature", data)
        self.assertTrue(_verify(data["license_payload"], data["signature"]))
        self.assertEqual(sorted(data["license_payload"]["features"]), ["billing", "members"])
        self.assertEqual(data["license_payload"]["plan_id"], "test_plan")

    @override_settings(LICENSE_SIGNING_PRIVATE_KEY=_TEST_PRIVATE_KEY_HEX)
    def test_tampered_payload_fails_verification(self):
        status, data = self._validate("MACHINE-A")
        tampered = dict(data["license_payload"])
        tampered["features"] = tampered["features"] + ["whatsapp"]
        self.assertFalse(_verify(tampered, data["signature"]))

    @override_settings(LICENSE_SIGNING_PRIVATE_KEY=_TEST_PRIVATE_KEY_HEX)
    def test_license_level_override_adds_a_feature_on_top_of_the_plan(self):
        whatsapp = Feature.objects.get(code="whatsapp")
        self.license.feature_overrides.create(feature=whatsapp, enabled=True, note="add-on")
        status, data = self._validate("MACHINE-A")
        self.assertIn("whatsapp", data["license_payload"]["features"])

    @override_settings(LICENSE_SIGNING_PRIVATE_KEY=_TEST_PRIVATE_KEY_HEX)
    def test_license_level_override_can_revoke_a_plan_feature(self):
        members = Feature.objects.get(code="members")
        self.license.feature_overrides.create(feature=members, enabled=False, note="temporary revoke")
        status, data = self._validate("MACHINE-A")
        self.assertNotIn("members", data["license_payload"]["features"])

    # -- Device activation / limit -----------------------------------

    def test_first_device_auto_activates(self):
        status, data = self._validate("MACHINE-A")
        self.assertTrue(data["valid"])
        self.assertEqual(self.license.devices.filter(status="active").count(), 1)

    def test_second_device_over_the_limit_is_cleanly_rejected(self):
        self._validate("MACHINE-A")
        status, data = self._validate("MACHINE-B")
        self.assertEqual(status, 403)
        self.assertEqual(data["status"], "device_limit_reached")
        self.assertFalse(data["valid"])
        # The first device must be completely unaffected.
        self.assertEqual(self.license.devices.filter(status="active").count(), 1)
        self.assertTrue(self.license.devices.get(fingerprint="MACHINE-A").status == "active")

    def test_raising_max_devices_lets_the_next_device_activate_without_reinstall(self):
        self._validate("MACHINE-A")
        self._validate("MACHINE-B")  # rejected, limit still 1

        self.license.max_devices = 2
        self.license.save(update_fields=["max_devices"])

        body = json.dumps({"license_key": self.license.key, "machine_id": "MACHINE-B"})
        req = self.rf.post("/api/devices/activate/", data=body, content_type="application/json")
        resp = views.activate_device(req)
        data = json.loads(resp.content)
        self.assertTrue(data["activated"])
        self.assertEqual(self.license.devices.filter(status="active").count(), 2)

    def test_blocked_device_is_rejected_even_within_the_limit(self):
        self._validate("MACHINE-A")
        self.license.devices.filter(fingerprint="MACHINE-A").update(status="blocked")
        status, data = self._validate("MACHINE-A")
        self.assertEqual(status, 403)
        self.assertEqual(data["status"], "device_blocked")

    def test_whatsapp_endpoints_share_the_same_device_limit_as_validate_license(self):
        """Regression test for a real inconsistency found during review:
        license_utils.py used to enforce the OLD single machine_id field
        independently of validate_license's own logic. Both now go
        through License.get_or_activate_device, so they can't drift."""
        self._validate("MACHINE-A")  # binds MACHINE-A via validate_license
        client, lic, error = resolve_active_client_and_license(self.license.key, "MACHINE-B")
        self.assertIsNone(client)
        self.assertEqual(error["error_code"], "device_limit_reached")

    # -- Untouched by any of this --------------------------------------

    def test_unknown_license_key_unaffected(self):
        body = json.dumps({"license_key": "FDPX-DOES-NOT-EXIST", "machine_id": "MACHINE-A"})
        req = self.rf.post("/api/validate-license/", data=body, content_type="application/json")
        resp = views.validate_license(req)
        self.assertEqual(resp.status_code, 404)

    def test_expired_license_still_fully_locks(self):
        self.license.expiry_date = date.today() - timedelta(days=1)
        self.license.save(update_fields=["expiry_date"])
        status, data = self._validate("MACHINE-A")
        self.assertFalse(data["valid"])
        self.assertEqual(data["status"], "expired")

    def test_device_activation_writes_an_audit_log_row(self):
        self._validate("MACHINE-A")
        self.license.max_devices = 2
        self.license.save(update_fields=["max_devices"])
        body = json.dumps({"license_key": self.license.key, "machine_id": "MACHINE-B"})
        req = self.rf.post("/api/devices/activate/", data=body, content_type="application/json")
        views.activate_device(req)
        self.assertTrue(AuditLog.objects.filter(license=self.license, action="device_activated").exists())
