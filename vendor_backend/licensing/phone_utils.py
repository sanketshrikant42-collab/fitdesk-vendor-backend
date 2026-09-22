"""Server-side phone validation for /api/send-whatsapp-receipt/.

The FitDesk Pro desktop client already normalizes phone numbers to
+91XXXXXXXXXX before sending, but this endpoint must never forward an
unvalidated string straight to the WhatsApp provider on the strength of
that alone -- a different or compromised client could send anything.
This is an independent re-implementation (not a shared import from the
desktop repo -- they're two separate codebases), deliberately using the
same permissive-but-sane rules so a legitimately normalized number from
the real client always passes.
"""


def validate_whatsapp_phone(raw):
    """Returns (normalized_phone, None) on success, or (None, reason) on
    failure. normalized_phone is in E.164-ish form, e.g. '+919876543210'."""

    if raw is None:
        return None, "Phone number is required."

    text = str(raw).strip()
    if not text:
        return None, "Phone number is required."

    had_plus = text.startswith("+")
    digits = "".join(ch for ch in text if ch.isdigit())

    if not digits:
        return None, "Phone number contains no digits."

    if had_plus:
        if 8 <= len(digits) <= 15:
            return "+" + digits, None
        return None, "Phone number is not a valid international number."

    if len(digits) == 10:
        return "+91" + digits, None

    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits, None

    if 8 <= len(digits) <= 15:
        return "+" + digits, None

    return None, "Phone number is not valid."
