"""Meta Embedded Signup token exchange.

Turns the short-lived authorization `code` FB.login() hands back on the
signup landing page (licensing/templates/licensing/whatsapp_connect.html)
into this gym's actual WhatsApp access token and phone number details.
This is a one-time exchange per connection attempt, not a per-message
call, so it's kept separate from licensing/whatsapp_provider.py's
per-send MetaCloudAPIProvider.

Uses the vendor's OWN Meta App credentials (META_APP_ID/META_APP_SECRET,
env vars, never per-gym) to complete the exchange -- the resulting
access token belongs to the connecting gym's WhatsApp Business Account,
not to this app.
"""

import requests
from django.conf import settings

GRAPH_API_VERSION_DEFAULT = "v20.0"


class SignupExchangeError(Exception):
    """message is safe to show the gym owner; safe_log is server-side
    only and may contain Meta's raw error body (never a token -- the
    token being exchanged FOR is the whole point of this call, so there
    is nothing yet to leak until exchange succeeds, and the response
    body itself never echoes back client_secret)."""

    def __init__(self, message, safe_log):
        self.message = message
        self.safe_log = safe_log
        super().__init__(safe_log)


def _api_version():
    return getattr(settings, "META_API_VERSION", GRAPH_API_VERSION_DEFAULT)


def exchange_code_for_token(code):
    """Exchanges an Embedded Signup authorization code for an access
    token. Returns the access token string. Raises SignupExchangeError
    on any failure -- callers must not silently continue without one."""
    app_id = getattr(settings, "META_APP_ID", "")
    app_secret = getattr(settings, "META_APP_SECRET", "")
    if not app_id or not app_secret:
        raise SignupExchangeError(
            "WhatsApp connection is not fully set up on the server yet.",
            "META_APP_ID/META_APP_SECRET not configured.",
        )

    url = f"https://graph.facebook.com/{_api_version()}/oauth/access_token"
    try:
        response = requests.get(
            url,
            params={"client_id": app_id, "client_secret": app_secret, "code": code},
            timeout=20,
        )
    except requests.exceptions.RequestException as exc:
        raise SignupExchangeError(
            "Could not reach Meta to complete the connection. Please try again.",
            f"network error exchanging code: {exc}",
        )

    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise SignupExchangeError(
            "Meta rejected the connection attempt. Please try again.",
            f"http={response.status_code} body={body}",
        )

    try:
        return response.json()["access_token"]
    except (ValueError, KeyError) as exc:
        raise SignupExchangeError(
            "Meta's response was not understood. Please try again.",
            f"unexpected token-exchange response shape: {exc}",
        )


def fetch_phone_number_details(access_token, phone_number_id):
    """Returns (display_phone_number, verified_name). Never raises -- a
    business name/number that doesn't resolve yet shouldn't fail the
    whole connect flow; both come back as "" if unavailable."""
    if not phone_number_id:
        return "", ""
    url = f"https://graph.facebook.com/{_api_version()}/{phone_number_id}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "display_phone_number,verified_name"},
            timeout=20,
        )
        if response.status_code >= 400:
            return "", ""
        data = response.json()
        return data.get("display_phone_number", ""), data.get("verified_name", "")
    except requests.exceptions.RequestException:
        return "", ""
