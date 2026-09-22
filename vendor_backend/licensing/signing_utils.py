"""Signs the license/entitlement payload the desktop app receives from
/api/validate-license/, so the desktop never has to blindly trust a JSON
field it could locally edit.

Uses Ed25519 (via the `cryptography` package, already a dependency here
for licensing/crypto_utils.py's Fernet token encryption -- no new backend
package needed). This is a SEPARATE keypair from that Fernet key: Fernet
is symmetric (same key encrypts and decrypts, so it must never leave the
server); Ed25519 here is asymmetric (the private key signs, server-side
only, and a public key -- safe to embed in the distributed desktop app --
verifies).

Generate the keypair ONCE, locally, and never regenerate it casually --
every already-activated desktop install has the old public key baked in,
so rotating the private key means every installed copy of FitDesk Pro
needs an update before it can validate a license again:

    python -c "
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    print('LICENSE_SIGNING_PRIVATE_KEY (Render env var, keep secret) =')
    print(priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex())
    print()
    print('LICENSE_SIGNING_PUBLIC_KEY (bake into the desktop app, safe to share) =')
    print(pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex())
    "

Set LICENSE_SIGNING_PRIVATE_KEY as a Render environment variable (never
commit it, never put it in .env in a way that gets committed). The public
key is not a secret -- it goes into the desktop's config.py as a plain
constant.
"""

import json

from django.conf import settings


class SigningKeyNotConfigured(Exception):
    """Raised when LICENSE_SIGNING_PRIVATE_KEY isn't set. validate_license
    must treat this as a server misconfiguration, not silently return an
    unsigned payload."""


def _canonical_json(payload):
    """Deterministic byte representation -- sorted keys, no whitespace --
    so the exact same payload always produces the exact same signature,
    and the desktop can reproduce this encoding to verify."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _get_private_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    hex_key = getattr(settings, "LICENSE_SIGNING_PRIVATE_KEY", "") or ""
    if not hex_key:
        raise SigningKeyNotConfigured(
            "LICENSE_SIGNING_PRIVATE_KEY is not set -- cannot sign license payloads."
        )
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_key))


def sign_license_payload(payload):
    """Returns (canonical_payload_dict, signature_hex). `payload` should
    already contain only JSON-safe values (strings, ints, lists, None) --
    e.g. dates as isoformat strings, not date objects."""
    private_key = _get_private_key()
    signature = private_key.sign(_canonical_json(payload))
    return payload, signature.hex()


def build_signed_license_response(license_obj):
    """The single place that decides what goes into a signed license
    payload -- called by validate_license so every caller gets the same
    shape. Kept deliberately small and JSON-safe (see _canonical_json).

    Returns {"payload": {...}, "signature": "<hex>"}. The caller (the
    validate_license view) is responsible for putting `payload` and
    `signature` into the response as their OWN nested object (e.g.
    response["license_payload"] / response["signature"]) rather than
    spreading `payload`'s keys loose at the top level next to
    unrelated, unsigned convenience fields like `message` or
    `device_status` -- keeping the signed object self-contained is what
    lets the desktop verifier canonicalize exactly the bytes that were
    signed, with no ambiguity about which top-level keys were covered by
    the signature and which weren't.
    """
    payload = {
        "license_id": license_obj.pk,
        "client_id": license_obj.client_id,
        "license_key": license_obj.key,
        "status": license_obj.effective_status,
        "expiry_date": license_obj.expiry_date.isoformat(),
        "plan_id": license_obj.plan.plan_id if license_obj.plan_id else None,
        "features": license_obj.resolved_features,
        "max_devices": license_obj.max_devices,
        "issued_at": license_obj.issued_at.isoformat(),
    }
    _, signature = sign_license_payload(payload)
    return {"payload": payload, "signature": signature}
