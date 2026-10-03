"""Live connectivity test against the REAL R2 bucket, using only objects
under a throwaway prefix -- it never reads, lists or touches any gym's
backups.

    python manage.py r2_selftest            # uses R2_* env vars
    python manage.py r2_selftest --prefix _selftest

Checks: credentials/bucket reachable, upload, metadata read-back,
download + byte-for-byte compare, presigned PUT and GET round-trip over
real HTTPS (the exact path the desktop uses), delete, and that the
deleted object is really gone. Prints PASS/FAIL per step, never prints
any credential or presigned URL.
"""

import base64
import hashlib
import os
import uuid

import requests
from django.core.management.base import BaseCommand, CommandError

from cloud_backup.storage import ObjectNotFound, R2Storage, StorageError


class Command(BaseCommand):
    help = "Round-trip test against the configured R2 bucket under a throwaway prefix."

    def add_arguments(self, parser):
        parser.add_argument("--prefix", default="_selftest")

    def handle(self, *args, **opts):
        prefix = opts["prefix"].strip("/")
        # Hard guard: the self-test may only ever touch keys under
        # _selftest/<random run id>/ -- never gyms/ or anything else.
        if prefix != "_selftest" and not prefix.startswith("_selftest/"):
            raise CommandError("The self-test only runs under the _selftest/ prefix.")
        failures = 0

        def step(name, fn):
            nonlocal failures
            try:
                fn()
                self.stdout.write(self.style.SUCCESS(f"PASS  {name}"))
                return True
            except Exception as exc:  # report the safe code only
                failures += 1
                code = getattr(exc, "code", type(exc).__name__)
                detail = getattr(exc, "detail_code", "")
                self.stdout.write(self.style.ERROR(f"FAIL  {name}: {code} {detail}".rstrip()))
                return False

        try:
            storage = R2Storage()
        except StorageError as exc:
            raise CommandError(f"R2 not configured: {exc.code}")

        run = uuid.uuid4().hex[:12]
        key = f"{prefix}/{run}/direct.bin"
        pkey = f"{prefix}/{run}/presigned.bin"
        payload = os.urandom(256 * 1024)
        digest = hashlib.sha256(payload).hexdigest()

        self.stdout.write(f"bucket: {storage.bucket}   test prefix: {prefix}/{run}/")
        step("credentials valid + bucket reachable (list under the test prefix)",
             lambda: storage.list_keys(f"{prefix}/{run}/"))
        step("server upload", lambda: storage.put_bytes(key, payload, {"sha256": digest}))

        def head_check():
            info = storage.head(key)
            assert info.size == len(payload), "size mismatch"
            assert info.metadata.get("sha256") == digest, "metadata mismatch"
        step("metadata read-back", head_check)

        def download_check():
            assert storage.get_bytes(key) == payload, "content mismatch"
        step("server download + compare", download_check)

        def list_check():
            assert key in storage.list_keys(f"{prefix}/{run}/"), "uploaded object not listed"
        step("list shows the test object", list_check)

        md5 = base64.b64encode(hashlib.md5(payload).digest()).decode()
        put_headers = {
            "Content-Type": "application/octet-stream",
            "x-amz-meta-sha256": digest,
            "x-amz-meta-backup-id": run,
            "Content-MD5": md5,
        }

        def presigned_roundtrip():
            url = storage.presign_put(pkey, {"sha256": digest, "backup-id": run}, 300, content_md5_b64=md5)
            r = requests.put(url, data=payload, headers=put_headers, timeout=60)
            assert r.status_code in (200, 201), f"presigned PUT http {r.status_code}"
            info = storage.head(pkey)
            assert info.metadata.get("sha256") == digest, "presigned metadata missing"
            g = requests.get(storage.presign_get(pkey, 300), timeout=60)
            assert g.status_code == 200 and hashlib.sha256(g.content).hexdigest() == digest, "presigned GET mismatch"
        step("presigned PUT/GET over HTTPS (desktop path)", presigned_roundtrip)

        def tamper_rejected():
            url = storage.presign_put(pkey + ".x", {"sha256": digest, "backup-id": run}, 300, content_md5_b64=md5)
            r = requests.put(url, data=payload, headers={**put_headers, "x-amz-meta-sha256": "0" * 64}, timeout=60)
            assert r.status_code == 403, f"expected 403 for tampered signed header, got {r.status_code}"
        step("presigned URL rejects altered signed metadata", tamper_rejected)

        def corrupt_body_rejected():
            url = storage.presign_put(pkey + ".y", {"sha256": digest, "backup-id": run}, 300, content_md5_b64=md5)
            r = requests.put(url, data=payload[:-1] + b"!", headers=put_headers, timeout=60)
            assert r.status_code in (400, 403), f"expected R2 to reject a body that fails Content-MD5, got {r.status_code}"
        step("R2 rejects an upload body that doesn't match Content-MD5", corrupt_body_rejected)

        def cleanup():
            # Deletes ONLY the objects this run created under <prefix>/<run>/.
            for k in (key, pkey, pkey + ".x", pkey + ".y"):
                storage.delete(k)
            try:
                storage.head(key)
                raise AssertionError("object still present after delete")
            except ObjectNotFound:
                pass
            assert storage.list_keys(f"{prefix}/{run}/") == [], "test prefix not empty after cleanup"
        step("delete + confirm gone", cleanup)

        if failures:
            raise CommandError(f"{failures} step(s) failed.")
        self.stdout.write(self.style.SUCCESS("R2 self-test passed."))
