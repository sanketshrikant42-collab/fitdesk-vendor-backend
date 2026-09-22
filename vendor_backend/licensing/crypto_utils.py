"""Encryption at rest for per-client WhatsApp access tokens.

Each gym owner connects their own Meta WhatsApp Business account, so this
backend now holds many real, live Meta access tokens -- one per Client --
instead of a single token in an env var. That's a materially bigger
target than before, so the token is never stored as plaintext: it's
encrypted with a symmetric key (Fernet, from the well-maintained
`cryptography` package) that itself lives only in an environment
variable, never in the database and never in this repo.

WHATSAPP_TOKEN_ENCRYPTION_KEY must be a urlsafe-base64-encoded 32-byte
key -- generate one locally with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and set it as a Render environment variable. Losing/rotating this key
means every already-connected gym's WhatsApp token becomes unreadable
and has to reconnect -- so treat it like any other production secret:
back it up somewhere safe outside of git.
"""

from django.conf import settings


class TokenEncryptionNotConfigured(Exception):
    """Raised when WHATSAPP_TOKEN_ENCRYPTION_KEY isn't set. Callers must
    treat this the same as "WhatsApp isn't configured" -- never fall back
    to storing the token in plaintext."""


def _get_fernet():
    from cryptography.fernet import Fernet

    key = getattr(settings, "WHATSAPP_TOKEN_ENCRYPTION_KEY", "") or ""
    if not key:
        raise TokenEncryptionNotConfigured(
            "WHATSAPP_TOKEN_ENCRYPTION_KEY is not set -- cannot encrypt/decrypt WhatsApp access tokens."
        )
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def encrypt_token(plaintext_token):
    """Returns an encrypted string safe to store in
    WhatsAppConfiguration.access_token_encrypted. Returns "" for an empty
    input rather than encrypting an empty string, so "no token stored"
    stays distinguishable from "token is an empty string"."""
    if not plaintext_token:
        return ""
    fernet = _get_fernet()
    return fernet.encrypt(plaintext_token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token):
    """Returns the plaintext token, or None if there's nothing stored or
    it can't be decrypted (wrong/rotated key, corrupted value) -- never
    raises into a caller that's just trying to check "is this
    configured?", since a decryption failure should be treated exactly
    like "not configured", not crash the request."""
    if not encrypted_token:
        return None
    try:
        fernet = _get_fernet()
        return fernet.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except TokenEncryptionNotConfigured:
        raise
    except Exception as exc:
        print(f"[WhatsApp] WARNING: could not decrypt stored access token: {exc}")
        return None
