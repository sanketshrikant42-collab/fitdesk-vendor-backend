"""Cloud Backup API tests.

Most tests use FakeStorage (in-memory, injected via
storage.set_storage_for_tests) so they're fast and deterministic. The
R2StorageS3ProtocolTests class drives the REAL R2Storage/boto3 code path
(presigned URLs over HTTP, head/delete/stream-hash) against moto's S3
emulator -- it is skipped automatically if moto isn't installed (moto is
a test-only tool, deliberately not in requirements.txt).
"""

import base64
import hashlib
import io
import json
import uuid
from datetime import date, timedelta
from unittest import mock, skipUnless

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from licensing.models import AuditLog, Client, Device, Feature, License, LicenseFeatureOverride, Plan, PlanFeature

from . import services
from .models import CloudBackup, GymBackupKey
from .storage import ObjectInfo, ObjectNotFound, StorageAuthError, StorageNotConfigured, StorageUnavailable, set_storage_for_tests

try:
    import moto  # noqa: F401
    HAVE_MOTO = True
except ImportError:  # pragma: no cover
    HAVE_MOTO = False

FERNET_KEY = Fernet.generate_key().decode()
FAKE_SECRET = "THIS-IS-THE-R2-SECRET-NEVER-LEAK-IT"


class FakeStorage:
    """In-memory stand-in for R2Storage with failure switches."""

    def __init__(self):
        self.objects = {}  # key -> (bytes, metadata)
        self.fail_with = None  # exception instance raised by every op
        self.fail_delete = False
        self.presigned = []

    def _maybe_fail(self):
        if self.fail_with is not None:
            raise self.fail_with

    def presign_put(self, key, metadata, expires_in, content_md5_b64=None):
        self._maybe_fail()
        self.presigned.append(("PUT", key, dict(metadata), content_md5_b64))
        return f"https://fake-r2.invalid/{key}?X-Amz-Signature=abc"

    def presign_get(self, key, expires_in):
        self._maybe_fail()
        self.presigned.append(("GET", key, {}))
        return f"https://fake-r2.invalid/{key}?X-Amz-Signature=def"

    def head(self, key):
        self._maybe_fail()
        if key not in self.objects:
            raise ObjectNotFound("NoSuchKey")
        data, meta = self.objects[key]
        return ObjectInfo(size=len(data), metadata=dict(meta), etag=hashlib.md5(data).hexdigest())

    def delete(self, key):
        self._maybe_fail()
        if self.fail_delete:
            raise StorageUnavailable("InternalError")
        self.objects.pop(key, None)

    def sha256_of(self, key):
        self._maybe_fail()
        if key not in self.objects:
            raise ObjectNotFound("NoSuchKey")
        data, _ = self.objects[key]
        return hashlib.sha256(data).hexdigest(), len(data)

    # test helper: simulate what the desktop's presigned PUT does
    def simulate_put(self, key, data, sha):
        self.objects[key] = (data, {"sha256": sha})


@override_settings(
    BACKUP_KEY_ENCRYPTION_KEY=FERNET_KEY,
    R2_SECRET_ACCESS_KEY=FAKE_SECRET,
    CLOUD_BACKUP_RETENTION_DAILY=30,
    CLOUD_BACKUP_RETENTION_MANUAL=10,
)
class CloudBackupApiTests(TestCase):

    def setUp(self):
        self.storage = FakeStorage()
        set_storage_for_tests(self.storage)

        self.feature, _ = Feature.objects.get_or_create(code="cloud_backup", defaults={"name": "Cloud Backup"})
        self.plan_with = Plan.objects.create(plan_id="t_with", name="With cloud")
        PlanFeature.objects.create(plan=self.plan_with, feature=self.feature)
        self.plan_without = Plan.objects.create(plan_id="t_without", name="Without cloud")

        self.gym_a = Client.objects.create(gym_name="Gym A", owner_name="A", phone="1")
        self.gym_b = Client.objects.create(gym_name="Gym B", owner_name="B", phone="2")
        self.lic_a = License.objects.create(client=self.gym_a, expiry_date=date.today() + timedelta(days=300), plan=self.plan_with, max_devices=2)
        self.lic_b = License.objects.create(client=self.gym_b, expiry_date=date.today() + timedelta(days=300), plan=self.plan_with, max_devices=2)
        self.auth_a = {"license_key": self.lic_a.key, "machine_id": "machine-a"}
        self.auth_b = {"license_key": self.lic_b.key, "machine_id": "machine-b"}

    def tearDown(self):
        set_storage_for_tests(None)

    # -- helpers ----------------------------------------------------------
    def post(self, name, body):
        return self.client.post(f"/api/backups/{name}/", data=json.dumps(body), content_type="application/json")

    def key_for(self, auth):
        r = self.post("key", auth)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def upload(self, auth, created_at=None, backup_type="scheduled", data=b"x" * 1000, complete=True, backup_id=None):
        cache = self.__dict__.setdefault("_key_ids", {})
        if auth["license_key"] not in cache:
            cache[auth["license_key"]] = self.key_for(auth)["key_id"]
        key_id = cache[auth["license_key"]]
        backup_id = backup_id or str(uuid.uuid4())
        sha = hashlib.sha256(data).hexdigest()
        r = self.post("request-upload", {
            **auth, "backup_id": backup_id, "backup_type": backup_type, "file_size": len(data),
            "checksum_sha256": sha, "created_at": (created_at or timezone.now()).isoformat(),
            "application_version": "1.1.0", "database_version": "7", "backup_format_version": 1,
            "encryption_key_id": key_id, "content_md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
        })
        if r.status_code != 200 or not complete:
            return backup_id, r
        obj = CloudBackup.objects.get(backup_id=backup_id, license__key=auth["license_key"])
        self.storage.simulate_put(obj.object_key, data, sha)
        r2 = self.post("complete-upload", {**auth, "backup_id": backup_id})
        return backup_id, r2

    # -- happy path ---------------------------------------------------------
    def test_full_upload_list_download_flow(self):
        bid, r = self.upload(self.auth_a)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "available")
        obj = CloudBackup.objects.get(backup_id=bid)
        self.assertTrue(obj.object_key.startswith(f"gyms/GYM-{self.gym_a.pk:05d}/backups/"))
        self.assertIsNotNone(obj.verified_at)

        r = self.post("list", self.auth_a)
        self.assertEqual([b["backup_id"] for b in r.json()["backups"]], [bid])

        r = self.post("request-download", {**self.auth_a, "backup_id": bid})
        self.assertEqual(r.status_code, 200)
        self.assertIn("download_url", r.json())
        self.assertEqual(r.json()["backup"]["checksum_sha256"], obj.checksum_sha256)

    def test_presigned_put_signs_checksum_metadata_and_exact_key(self):
        bid, r = self.upload(self.auth_a, complete=False)
        body = r.json()
        self.assertEqual(body["upload_headers"]["x-amz-meta-sha256"], CloudBackup.objects.get(backup_id=bid).checksum_sha256)
        method, key, meta, md5 = self.storage.presigned[-1]
        self.assertEqual(method, "PUT")
        self.assertEqual(key, CloudBackup.objects.get(backup_id=bid).object_key)
        self.assertIn("sha256", meta)

    def test_gym_key_is_stable_and_stored_encrypted(self):
        k1 = self.key_for(self.auth_a)
        k2 = self.key_for(self.auth_a)
        self.assertEqual(k1["key"], k2["key"])
        self.assertEqual(k1["key_id"], k2["key_id"])
        stored = GymBackupKey.objects.get(client=self.gym_a).key_encrypted
        self.assertNotIn(k1["key"], stored)
        self.assertNotEqual(self.key_for(self.auth_b)["key"], k1["key"])

    @override_settings(BACKUP_KEY_ENCRYPTION_KEY="")
    def test_key_service_not_configured(self):
        r = self.post("key", self.auth_a)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error_code"], "key_service_not_configured")

    # -- isolation ----------------------------------------------------------
    def test_gym_b_cannot_touch_gym_a_backup(self):
        bid, _ = self.upload(self.auth_a)
        for name in ("request-download", "verify", "delete", "complete-upload"):
            r = self.post(name, {**self.auth_b, "backup_id": bid})
            self.assertEqual(r.status_code, 404, f"{name}: {r.content}")
        r = self.post("list", self.auth_b)
        self.assertEqual(r.json()["backups"], [])
        self.assertEqual(CloudBackup.objects.get(backup_id=bid).status, "available")

    def test_client_supplied_gym_id_is_ignored(self):
        body = {**self.auth_b, "gym_id": f"GYM-{self.gym_a.pk:05d}", "client_id": self.gym_a.pk}
        r = self.post("list", body)
        self.assertEqual(r.json()["gym_ref"], f"GYM-{self.gym_b.pk:05d}")
        bid, r = self.upload({**self.auth_b, "gym_id": self.gym_a.pk})
        self.assertTrue(CloudBackup.objects.get(backup_id=bid).object_key.startswith(f"gyms/GYM-{self.gym_b.pk:05d}/"))

    def test_same_backup_id_in_two_gyms_is_isolated(self):
        shared = str(uuid.uuid4())
        self.upload(self.auth_a, backup_id=shared)
        self.upload(self.auth_b, backup_id=shared)
        self.assertEqual(CloudBackup.objects.filter(backup_id=shared).count(), 2)
        r = self.post("request-download", {**self.auth_b, "backup_id": shared})
        self.assertTrue(r.json()["backup"]["file_name"].startswith(f"FDPRO_GYM-{self.gym_b.pk:05d}"))

    def test_cannot_upload_with_other_gyms_key_id(self):
        other_key_id = self.key_for(self.auth_b)["key_id"]
        self.key_for(self.auth_a)
        data = b"z" * 10
        r = self.post("request-upload", {
            **self.auth_a, "backup_id": str(uuid.uuid4()), "backup_type": "scheduled", "file_size": 10,
            "checksum_sha256": hashlib.sha256(data).hexdigest(), "created_at": timezone.now().isoformat(),
            "encryption_key_id": other_key_id,
        })
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error_code"], "wrong_encryption_key")

    # -- license / device / entitlement --------------------------------------
    def test_invalid_license_rejected(self):
        r = self.post("list", {"license_key": "FDPX-NOPE", "machine_id": "m"})
        self.assertEqual(r.status_code, 401)

    def test_missing_machine_id_rejected(self):
        r = self.post("list", {"license_key": self.lic_a.key})
        self.assertEqual(r.status_code, 401)

    def test_expired_license_blocks_all_but_keeps_backups(self):
        bid, _ = self.upload(self.auth_a)
        self.lic_a.expiry_date = date.today() - timedelta(days=1)
        self.lic_a.save()
        for name in ("list", "request-download", "request-upload", "key"):
            self.assertEqual(self.post(name, {**self.auth_a, "backup_id": bid}).status_code, 401, name)
        # nothing was deleted because the license lapsed
        self.assertEqual(CloudBackup.objects.get(backup_id=bid).status, "available")
        self.assertIn(CloudBackup.objects.get(backup_id=bid).object_key, self.storage.objects)

    def test_blocked_device_rejected(self):
        self.post("list", self.auth_a)
        Device.objects.filter(license=self.lic_a, fingerprint="machine-a").update(status="blocked")
        self.assertEqual(self.post("list", self.auth_a).status_code, 403)

    def test_device_limit_enforced(self):
        self.lic_a.max_devices = 1
        self.lic_a.save()
        self.post("list", self.auth_a)
        r = self.post("list", {**self.auth_a, "machine_id": "another-pc"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error_code"], "device_limit_reached")

    def test_not_entitled_cannot_upload_but_can_restore(self):
        bid, _ = self.upload(self.auth_a)
        self.lic_a.plan = self.plan_without
        self.lic_a.save()
        status = self.post("status", self.auth_a).json()
        self.assertFalse(status["entitled"])
        _, r = self.upload(self.auth_a, complete=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error_code"], "not_entitled")
        self.assertEqual(self.post("request-download", {**self.auth_a, "backup_id": bid}).status_code, 200)

    def test_license_override_grants_feature(self):
        self.lic_a.plan = self.plan_without
        self.lic_a.save()
        LicenseFeatureOverride.objects.create(license=self.lic_a, feature=self.feature, enabled=True)
        _, r = self.upload(self.auth_a)
        self.assertEqual(r.status_code, 200)

    # -- validation / integrity -------------------------------------------
    def test_content_md5_is_signed_and_etag_checked(self):
        bid, r = self.upload(self.auth_a, complete=False)
        self.assertIn("Content-MD5", r.json()["upload_headers"])
        self.assertTrue(self.storage.presigned[-1][3])
        obj = CloudBackup.objects.get(backup_id=bid)
        # same size + same sha256 metadata, different bytes -> ETag (MD5) catches it
        self.storage.simulate_put(obj.object_key, b"y" * 1000, obj.checksum_sha256)
        r = self.post("complete-upload", {**self.auth_a, "backup_id": bid})
        self.assertEqual(r.status_code, 422)

    def test_checksum_mismatch_after_upload_is_rejected_and_object_removed(self):
        bid, r = self.upload(self.auth_a, complete=False)
        obj = CloudBackup.objects.get(backup_id=bid)
        self.storage.simulate_put(obj.object_key, b"tampered" * 125, "0" * 64)
        r = self.post("complete-upload", {**self.auth_a, "backup_id": bid})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn(obj.object_key, self.storage.objects)
        self.assertEqual(CloudBackup.objects.get(backup_id=bid).status, "failed")

    def test_complete_without_object_is_409_and_stays_pending(self):
        bid, _ = self.upload(self.auth_a, complete=False)
        r = self.post("complete-upload", {**self.auth_a, "backup_id": bid})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(CloudBackup.objects.get(backup_id=bid).status, "pending_upload")

    def test_retry_upload_same_backup_id_reuses_record(self):
        bid, _ = self.upload(self.auth_a, complete=False)
        _, r = self.upload(self.auth_a, complete=False, backup_id=bid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CloudBackup.objects.get(backup_id=bid).upload_requests, 2)
        _, r = self.upload(self.auth_a, backup_id=bid)
        self.assertEqual(r.status_code, 200)
        _, r = self.upload(self.auth_a, complete=False, backup_id=bid)
        self.assertTrue(r.json()["already_uploaded"])

    def test_bad_inputs(self):
        key_id = self.key_for(self.auth_a)["key_id"]
        base = {**self.auth_a, "backup_id": str(uuid.uuid4()), "backup_type": "scheduled", "file_size": 10,
                "checksum_sha256": "a" * 64, "created_at": timezone.now().isoformat(), "encryption_key_id": key_id}
        for patch in ({"backup_id": "../../etc"}, {"checksum_sha256": "xyz"}, {"backup_type": "evil"},
                      {"created_at": "not-a-date"}, {"file_size": 0}, {"file_size": "abc"},
                      {"application_version": "1.0/../../x"}):
            r = self.post("request-upload", {**base, **patch})
            self.assertEqual(r.status_code, 400, patch)
        r = self.post("request-upload", {**base, "file_size": 10**12})
        self.assertEqual(r.status_code, 413)

    def test_deep_verify_detects_corruption(self):
        bid, _ = self.upload(self.auth_a)
        obj = CloudBackup.objects.get(backup_id=bid)
        data, meta = self.storage.objects[obj.object_key]
        self.storage.objects[obj.object_key] = (b"y" * len(data), meta)
        r = self.post("verify", {**self.auth_a, "backup_id": bid, "deep": False})
        self.assertTrue(r.json()["verified"])  # size+metadata still look right
        r = self.post("verify", {**self.auth_a, "backup_id": bid, "deep": True})
        self.assertFalse(r.json()["verified"])
        self.assertEqual(CloudBackup.objects.get(backup_id=bid).status, "corrupt")
        self.assertEqual(self.post("request-download", {**self.auth_a, "backup_id": bid}).status_code, 404)

    # -- storage failures ---------------------------------------------------
    def test_r2_unavailable_returns_503_safe_message(self):
        self.storage.fail_with = StorageUnavailable("EndpointConnectionError")
        _, r = self.upload(self.auth_a, complete=False)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error_code"], "storage_unavailable")

    def test_invalid_r2_credentials_never_leak(self):
        self.storage.fail_with = StorageAuthError("SignatureDoesNotMatch")
        _, r = self.upload(self.auth_a, complete=False)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error_code"], "storage_auth_error")
        self.assertNotIn(FAKE_SECRET, r.content.decode())

    @override_settings(R2_ACCOUNT_ID="", R2_ENDPOINT="", R2_ACCESS_KEY_ID="", R2_SECRET_ACCESS_KEY="")
    def test_storage_not_configured(self):
        set_storage_for_tests(None)
        _, r = self.upload(self.auth_a, complete=False)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error_code"], "storage_not_configured")

    def test_no_response_ever_contains_secret_or_other_gym(self):
        bid, _ = self.upload(self.auth_a)
        for name in ("status", "list", "key", "request-download", "verify"):
            body = self.post(name, {**self.auth_a, "backup_id": bid}).content.decode()
            self.assertNotIn(FAKE_SECRET, body)
            self.assertNotIn("Gym B", body)

    # -- retention ----------------------------------------------------------
    def _seed_available(self, n, backup_type="scheduled", start_days_ago=None):
        start = timezone.now() - timedelta(days=start_days_ago or n + 1)
        ids = []
        for i in range(n):
            bid, r = self.upload(self.auth_a, created_at=start + timedelta(days=i), backup_type=backup_type)
            self.assertEqual(r.status_code, 200, r.content)
            ids.append(bid)
        return ids

    def test_retention_keeps_latest_30_daily(self):
        ids = self._seed_available(31)
        available = CloudBackup.objects.filter(client=self.gym_a, status="available")
        self.assertEqual(available.count(), 30)
        oldest = CloudBackup.objects.get(backup_id=ids[0])
        self.assertEqual(oldest.status, "deleted")
        self.assertNotIn(oldest.object_key, self.storage.objects)
        self.assertEqual(CloudBackup.objects.get(backup_id=ids[-1]).status, "available")

    def test_manual_pool_does_not_evict_daily(self):
        self._seed_available(30)
        self._seed_available(12, backup_type="manual", start_days_ago=20)
        self.assertEqual(CloudBackup.objects.filter(client=self.gym_a, backup_type="scheduled", status="available").count(), 30)
        self.assertEqual(CloudBackup.objects.filter(client=self.gym_a, backup_type="manual", status="available").count(), 10)

    def test_retention_never_touches_pending_or_other_gyms(self):
        pending_id, _ = self.upload(self.auth_a, created_at=timezone.now() - timedelta(days=60), complete=False)
        b_id, _ = self.upload(self.auth_b, created_at=timezone.now() - timedelta(days=90))
        self._seed_available(31)
        self.assertEqual(CloudBackup.objects.get(backup_id=pending_id).status, "pending_upload")
        self.assertEqual(CloudBackup.objects.get(backup_id=b_id).status, "available")

    def test_failed_retention_delete_is_recorded_and_retried(self):
        ids = self._seed_available(30)
        self.storage.fail_delete = True
        self.upload(self.auth_a)  # 31st -> oldest should be rotated, delete fails
        oldest = CloudBackup.objects.get(backup_id=ids[0])
        self.assertEqual(oldest.status, "deletion_pending")
        self.assertIn("delete failed", oldest.last_error)
        self.storage.fail_delete = False
        services.apply_retention(self.gym_a, storage=self.storage)
        self.assertEqual(CloudBackup.objects.get(backup_id=ids[0]).status, "deleted")

    def test_upload_failure_does_not_count_or_trigger_retention(self):
        self._seed_available(30)
        bid, _ = self.upload(self.auth_a, complete=False)  # never completed
        self.assertEqual(CloudBackup.objects.filter(client=self.gym_a, status="available").count(), 30)
        self.assertEqual(CloudBackup.objects.filter(client=self.gym_a, status="deleted").count(), 0)

    def test_old_backup_is_superseded_not_uploaded(self):
        self._seed_available(30, start_days_ago=31)
        _, r = self.upload(self.auth_a, created_at=timezone.now() - timedelta(days=200), complete=False)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error_code"], "superseded")

    def test_cannot_delete_last_backup(self):
        bid, _ = self.upload(self.auth_a)
        r = self.post("delete", {**self.auth_a, "backup_id": bid})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error_code"], "last_backup")
        bid2, _ = self.upload(self.auth_a)
        self.assertEqual(self.post("delete", {**self.auth_a, "backup_id": bid}).status_code, 200)
        self.assertEqual(CloudBackup.objects.get(backup_id=bid).status, "deleted")

    # -- rate limiting / audit ----------------------------------------------
    def test_download_rate_limited(self):
        bid, _ = self.upload(self.auth_a)
        limit = services.RATE_LIMITS_PER_HOUR["cloud_backup.download_requested"]
        for _ in range(limit):
            self.assertEqual(self.post("request-download", {**self.auth_a, "backup_id": bid}).status_code, 200)
        self.assertEqual(self.post("request-download", {**self.auth_a, "backup_id": bid}).status_code, 429)

    def test_audit_entries_have_no_urls_or_keys(self):
        bid, _ = self.upload(self.auth_a)
        self.post("request-download", {**self.auth_a, "backup_id": bid})
        key = self.key_for(self.auth_a)["key"]
        for entry in AuditLog.objects.filter(action__startswith="cloud_backup."):
            dumped = json.dumps(entry.metadata)
            self.assertNotIn("http", dumped)
            self.assertNotIn(key, dumped)

    def test_maintenance_command_runs(self):
        from django.core.management import call_command
        out = io.StringIO()
        self._seed_available(2)
        call_command("cloud_backup_maintenance", stdout=out)
        self.assertIn("retention deleted=0", out.getvalue())


class FeatureSeedMigrationTests(TestCase):
    def test_cloud_backup_feature_on_premium_and_legacy(self):
        feature = Feature.objects.get(code="cloud_backup")
        plans = set(feature.plans.values_list("plan_id", flat=True))
        self.assertTrue({"premium", "legacy_full_access"}.issubset(plans))
        self.assertNotIn("basic", plans)
        self.assertNotIn("standard", plans)


@skipUnless(HAVE_MOTO, "moto not installed")
@override_settings(
    R2_ACCOUNT_ID="", R2_ENDPOINT="https://s3.amazonaws.com", R2_ACCESS_KEY_ID="testing",
    R2_SECRET_ACCESS_KEY="testing", R2_BUCKET_NAME="fitdesk-pro-backups",
)
class R2StorageS3ProtocolTests(TestCase):
    """Real boto3 code path (presign, head, delete, streaming hash) against
    moto's S3 emulation -- proves the presigned PUT really enforces the
    signed checksum header."""

    def setUp(self):
        from moto import mock_aws
        self.mock = mock_aws()
        self.mock.start()
        import boto3
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="fitdesk-pro-backups")
        set_storage_for_tests(None)

    def tearDown(self):
        self.mock.stop()

    def test_presigned_roundtrip_head_hash_delete(self):
        import requests
        from .storage import R2Storage
        s = R2Storage()
        data = b"encrypted-backup-bytes" * 100
        sha = hashlib.sha256(data).hexdigest()
        md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
        url = s.presign_put("gyms/GYM-00001/backups/2026/10/x.fdb", {"sha256": sha, "backup-id": "b1"}, 300, content_md5_b64=md5)
        headers = {"Content-Type": "application/octet-stream", "x-amz-meta-sha256": sha, "x-amz-meta-backup-id": "b1", "Content-MD5": md5}
        # Content-MD5 and the sha256 metadata are part of the signature, so the
        # uploader can't change them; R2/S3 then reject a body that doesn't
        # match Content-MD5 (moto's emulator doesn't enforce that last part --
        # `manage.py r2_selftest` checks it against the real bucket).
        from urllib.parse import parse_qs, urlparse
        signed = parse_qs(urlparse(url).query)["X-Amz-SignedHeaders"][0].split(";")
        self.assertIn("content-md5", signed)
        self.assertIn("x-amz-meta-sha256", signed)
        r = requests.put(url, data=data, headers=headers)
        self.assertIn(r.status_code, (200, 201))
        info = s.head("gyms/GYM-00001/backups/2026/10/x.fdb")
        self.assertEqual(info.size, len(data))
        self.assertEqual(info.metadata["sha256"], sha)
        self.assertEqual(info.etag, hashlib.md5(data).hexdigest())
        self.assertEqual(s.sha256_of("gyms/GYM-00001/backups/2026/10/x.fdb"), (sha, len(data)))
        g = requests.get(s.presign_get("gyms/GYM-00001/backups/2026/10/x.fdb", 300))
        self.assertEqual(g.content, data)
        s.delete("gyms/GYM-00001/backups/2026/10/x.fdb")
        with self.assertRaises(ObjectNotFound):
            s.head("gyms/GYM-00001/backups/2026/10/x.fdb")
        s.delete("gyms/GYM-00001/backups/2026/10/x.fdb")  # idempotent

    def test_bad_credentials_map_to_safe_error(self):
        from .storage import R2Storage
        with override_settings(R2_ACCESS_KEY_ID="", R2_SECRET_ACCESS_KEY=""):
            with self.assertRaises(StorageNotConfigured):
                R2Storage()


class KeyRotationTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(gym_name="Rot Gym", owner_name="R", phone="3")

    def test_rotation_with_multifernet_keeps_keys_readable(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from . import keys
        old = Fernet.generate_key().decode()
        new = Fernet.generate_key().decode()
        with override_settings(BACKUP_KEY_ENCRYPTION_KEY=old):
            key_id, raw = keys.get_or_create_gym_key(self.client_obj)
        # replacing the key outright: loud failure, nothing regenerated
        with override_settings(BACKUP_KEY_ENCRYPTION_KEY=new):
            with self.assertRaises(keys.BackupKeyUnreadable):
                keys.get_or_create_gym_key(self.client_obj)
            with self.assertRaises(CommandError):
                call_command("check_backup_keys", stdout=io.StringIO(), stderr=io.StringIO())
            self.assertEqual(GymBackupKey.objects.count(), 1)
        # proper rotation: new,old -> rewrap -> new alone works, same key material
        with override_settings(BACKUP_KEY_ENCRYPTION_KEY=f"{new},{old}"):
            out = io.StringIO()
            call_command("check_backup_keys", "--rewrap", stdout=out)
            self.assertIn("rewrapped=1", out.getvalue())
        with override_settings(BACKUP_KEY_ENCRYPTION_KEY=new):
            self.assertEqual(keys.get_or_create_gym_key(self.client_obj), (key_id, raw))
            call_command("check_backup_keys", stdout=io.StringIO())

    def test_malformed_key_error_never_echoes_value(self):
        from . import keys
        with override_settings(BACKUP_KEY_ENCRYPTION_KEY="not-a-real-key-VALUE"):
            with self.assertRaises(keys.BackupKeyServiceNotConfigured) as ctx:
                keys.get_or_create_gym_key(self.client_obj)
            self.assertNotIn("not-a-real-key-VALUE", str(ctx.exception))
