"""Shared license+machine resolution for every WhatsApp endpoint.

/api/send-whatsapp-receipt/, /api/whatsapp-status/, /api/whatsapp/connect/
start/ and /api/whatsapp/disconnect/ all need the exact same answer to
"which Client is this request allowed to act as?" -- this is the one
place that answers it, so they can never quietly disagree. The Client is
ALWAYS derived here from the validated License; nothing in this module
ever accepts a client_id/gym_id from the caller.

Device binding now goes through License.get_or_activate_device (see
models.py) -- the same method views.validate_license uses -- so a gym
running FitDesk Pro on more than one device (max_devices) gets consistent
behavior everywhere instead of this module enforcing the old single
machine_id rule independently. WhatsApp sending spends a real gym's real
Meta message quota/credentials, so the bar here is still higher than
validate_license's own tolerance for a missing machine_id: machine_id is
required outright when require_machine_id=True, not just checked when
present.
"""

from .models import License


def resolve_active_client_and_license(license_key, machine_id, require_machine_id=True):
    """Returns (client, license_obj, error) -- error is None on success,
    or {"error_code": "...", "message": "..."} on failure. error_code is
    one of: invalid_license, machine_mismatch, device_limit_reached.
    """
    license_key = (license_key or "").strip()
    machine_id = (machine_id or "").strip()

    if not license_key:
        return None, None, {"error_code": "invalid_license", "message": "No license key provided."}

    try:
        lic = License.objects.select_related("client").get(key=license_key)
    except License.DoesNotExist:
        return None, None, {"error_code": "invalid_license", "message": "License key not recognized."}

    if lic.effective_status != License.STATUS_ACTIVE:
        return None, None, {
            "error_code": "invalid_license",
            "message": f"This license is {lic.effective_status}.",
        }

    if require_machine_id:
        if not machine_id:
            return None, None, {"error_code": "invalid_license", "message": "Machine ID is required."}

        device, device_error = lic.get_or_activate_device(machine_id)

        if device_error == "device_blocked":
            return None, None, {
                "error_code": "machine_mismatch",
                "message": "This device has been blocked for this license.",
            }

        if device_error == "device_limit_reached":
            return None, None, {
                "error_code": "device_limit_reached",
                "message": "This license is already active on its maximum number of devices.",
            }

    return lic.client, lic, None
