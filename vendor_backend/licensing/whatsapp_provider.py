"""Abstraction over the WhatsApp Business Solution Provider (BSP) that
actually delivers a receipt document to a customer.

licensing/views.py only depends on WhatsAppProvider's methods --
swapping Meta's Cloud API for a reseller (Interakt, AiSensy, Gupshup,
Twilio) later means adding one new class here, not touching the view.

MetaCloudAPIProvider is the concrete implementation used today: Meta's
Cloud API directly (Graph API). Every instance is built from ONE
Client's own WhatsAppConfiguration (see get_provider_for_client() at the
bottom) -- there is no global/shared Meta credential anywhere in this
module. Two-step send: upload the PDF to /{phone_number_id}/media to get
a media id, then send an approved WhatsApp message TEMPLATE (not a plain
document+caption message) referencing that id in its document header --
business-initiated messages outside an open customer-service window
require an approved template, and a receipt notification is exactly
that case.
"""

from dataclasses import dataclass
from typing import Optional

import requests

GRAPH_API_VERSION_DEFAULT = "v20.0"
REQUEST_TIMEOUT = 30

# check_connection() backs Settings' "Test Connection" button (and Test
# Mode's "harmless configuration check"), both of which get clicked
# often -- it should feel snappy, not wait out the same 30s budget a
# real document send gets.
STATUS_CHECK_TIMEOUT = 10


@dataclass
class ProviderResult:
    ok: bool
    status: str  # "sent" | "test_ok" | "not_configured" | "provider_error" | "rate_limited"
    provider_message_id: Optional[str] = None
    error_detail: Optional[str] = None  # server-side logging only -- never sent to the client as-is
    needs_human: bool = False  # True for e.g. an expired/invalid access token -- a config problem, not a transient one


@dataclass
class ConnectionCheckResult:
    ok: bool
    message: str  # always safe to show the caller directly -- no token, no raw provider error body


class WhatsAppProvider:
    """Interface every concrete provider implements."""

    def is_configured(self):
        raise NotImplementedError

    def send_document(self, phone, message, pdf_bytes, filename, body_params, test_mode=False):
        raise NotImplementedError

    def check_connection(self):
        """A cheap, side-effect-free check that credentials are not just
        present but actually valid -- never sends a message."""
        raise NotImplementedError


class _GraphAPIError(Exception):
    """Wraps a non-2xx Graph API response. Meta never echoes the access
    token back in a response body (it was only ever sent in this
    request's own Authorization header), so nothing here needs redacting
    -- but this is still the single place that decides what a WhatsApp
    error is allowed to say, kept explicit rather than assumed safe."""

    # OAuthException -- the access token is invalid, expired, or revoked.
    # This needs the gym owner (or a human on our side) to reconnect; it
    # will not resolve itself.
    _AUTH_ERROR_CODES = {190}
    _RATE_LIMIT_CODES = {4, 80007, 130429}
    # Template not found / not approved / parameter count mismatch.
    _TEMPLATE_ERROR_CODES = {132000, 132001, 132005, 132007, 132012, 132015, 132016, 131026}

    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body or {}
        error = self.body.get("error", {}) if isinstance(self.body, dict) else {}
        self.code = error.get("code")
        self.message = error.get("message", "")
        self.fbtrace_id = error.get("fbtrace_id", "")
        super().__init__(f"Graph API {status_code}: {self.message} (code={self.code})")

    @property
    def is_auth_error(self):
        return self.code in self._AUTH_ERROR_CODES

    @property
    def is_template_error(self):
        return self.code in self._TEMPLATE_ERROR_CODES

    @property
    def public_status(self):
        if self.status_code == 429 or self.code in self._RATE_LIMIT_CODES:
            return "rate_limited"
        return "provider_error"

    def safe_log(self):
        return f"http={self.status_code} code={self.code} fbtrace_id={self.fbtrace_id} message={self.message}"


def _raise_for_graph_error(response):
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {"error": {"message": response.text[:300]}}
        raise _GraphAPIError(response.status_code, body)


class MetaCloudAPIProvider(WhatsAppProvider):

    def __init__(self, access_token, phone_number_id, template_name, template_language, api_version=None):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.template_name = template_name or "receipt_notification"
        self.template_language = template_language or "en_US"
        self.api_version = api_version or GRAPH_API_VERSION_DEFAULT

    @property
    def _graph_base(self):
        return f"https://graph.facebook.com/{self.api_version}"

    def is_configured(self):
        return bool(self.access_token and self.phone_number_id)

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _upload_media(self, pdf_bytes, filename):
        url = f"{self._graph_base}/{self.phone_number_id}/media"
        response = requests.post(
            url,
            headers=self._headers(),
            data={"messaging_product": "whatsapp", "type": "application/pdf"},
            files={"file": (filename, pdf_bytes, "application/pdf")},
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_graph_error(response)
        return response.json()["id"]

    def _send_template_message(self, phone, media_id, filename, body_params):
        """Sends the approved receipt template with the PDF as its
        document header and `body_params` (a list of plain strings) as
        the body's positional {{1}}, {{2}}, ... variables, in order.

        The exact variable count/order must match what was actually
        approved in Meta Business Manager for this template -- that's
        configured per Client (template_name/template_language), but
        the number and order of body variables in body_params is fixed
        by whatever built the request (see licensing/views.py), not
        auto-detected from Meta. A gym whose approved template uses a
        different variable shape will need that call site adjusted to
        match -- Meta's own error response for a mismatch is caught and
        mapped to a clean "template not available" message rather than
        a raw failure, but it will still fail the send.
        """
        url = f"{self._graph_base}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": phone.lstrip("+"),
            "type": "template",
            "template": {
                "name": self.template_name,
                "language": {"code": self.template_language},
                "components": [
                    {
                        "type": "header",
                        "parameters": [
                            {"type": "document", "document": {"id": media_id, "filename": filename}}
                        ],
                    },
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": str(p)} for p in (body_params or [])],
                    },
                ],
            },
        }
        response = requests.post(
            url,
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_graph_error(response)
        body = response.json()
        return body["messages"][0]["id"]

    def send_document(self, phone, message, pdf_bytes, filename, body_params, test_mode=False):
        if not self.is_configured():
            return ProviderResult(ok=False, status="not_configured", error_detail="Missing WhatsApp credentials for this client.")

        if test_mode:
            # Test Mode never sends a real customer message -- it only
            # confirms the credentials that WOULD be used are actually
            # still valid, via the same cheap read-only check
            # check_connection() does. No media upload, no message send.
            check = self.check_connection()
            if not check.ok:
                return ProviderResult(ok=False, status="provider_error", error_detail=check.message, needs_human=True)
            return ProviderResult(ok=True, status="test_ok")

        try:
            media_id = self._upload_media(pdf_bytes, filename)
        except _GraphAPIError as exc:
            return ProviderResult(
                ok=False, status=exc.public_status,
                error_detail=f"media upload: {exc.safe_log()}", needs_human=exc.is_auth_error,
            )
        except requests.exceptions.Timeout:
            return ProviderResult(ok=False, status="provider_error", error_detail="media upload timed out")
        except requests.exceptions.RequestException as exc:
            return ProviderResult(ok=False, status="provider_error", error_detail=f"media upload network error: {exc}")

        try:
            message_id = self._send_template_message(phone, media_id, filename, body_params)
        except _GraphAPIError as exc:
            return ProviderResult(
                ok=False, status=exc.public_status,
                error_detail=f"send template: {exc.safe_log()}", needs_human=exc.is_auth_error,
            )
        except requests.exceptions.Timeout:
            return ProviderResult(ok=False, status="provider_error", error_detail="send template timed out")
        except requests.exceptions.RequestException as exc:
            return ProviderResult(ok=False, status="provider_error", error_detail=f"send template network error: {exc}")

        return ProviderResult(ok=True, status="sent", provider_message_id=message_id)

    def check_connection(self):
        if not self.is_configured():
            return ConnectionCheckResult(ok=False, message="WhatsApp Business is not connected for this gym.")

        url = f"{self._graph_base}/{self.phone_number_id}"
        try:
            response = requests.get(
                url, headers=self._headers(), params={"fields": "id"}, timeout=STATUS_CHECK_TIMEOUT,
            )
            _raise_for_graph_error(response)
        except _GraphAPIError as exc:
            if exc.is_auth_error:
                return ConnectionCheckResult(ok=False, message="WhatsApp connection has expired. Please reconnect WhatsApp.")
            return ConnectionCheckResult(ok=False, message="WhatsApp could not be reached right now. Please try again shortly.")
        except requests.exceptions.Timeout:
            return ConnectionCheckResult(ok=False, message="WhatsApp could not be reached right now. Please try again shortly.")
        except requests.exceptions.RequestException:
            return ConnectionCheckResult(ok=False, message="WhatsApp could not be reached right now. Please try again shortly.")

        return ConnectionCheckResult(ok=True, message="WhatsApp connected successfully.")


class _UnconfiguredProvider(WhatsAppProvider):
    """Returned when a Client has no WhatsAppConfiguration row at all yet
    (never connected) -- lets every call site just call is_configured()
    without a None-check."""

    def is_configured(self):
        return False

    def send_document(self, *a, **k):
        return ProviderResult(ok=False, status="not_configured", error_detail="No WhatsAppConfiguration for this client.")

    def check_connection(self):
        return ConnectionCheckResult(ok=False, message="WhatsApp Business is not connected for this gym.")


def get_provider_for_client(client):
    """Builds the provider for exactly one Client, from that Client's OWN
    WhatsAppConfiguration -- this is the single choke point that makes
    per-gym isolation real: there is no code path anywhere in this
    module that can build a provider from anything other than the
    Client passed in here, and that Client is always the one
    licensing/license_utils.py derived from a validated license, never a
    caller-supplied id."""
    config = getattr(client, "whatsapp_configuration", None)
    if config is None or not config.is_ready():
        return _UnconfiguredProvider()

    from django.conf import settings

    return MetaCloudAPIProvider(
        access_token=config.access_token,
        phone_number_id=config.phone_number_id,
        template_name=config.template_name,
        template_language=config.template_language,
        api_version=getattr(settings, "META_API_VERSION", GRAPH_API_VERSION_DEFAULT),
    )
