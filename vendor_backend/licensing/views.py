import json
from datetime import date

from django.conf import settings
from django.core.mail import mail_admins
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from .models import Client, License, SupportQuery, WhatsAppSendLog, WhatsAppConfiguration, WhatsAppConnectSession, AuditLog
from .notifications import send_telegram_notification
from .phone_utils import validate_whatsapp_phone
from .ratelimit import is_rate_limited
from . import abuse
from .license_utils import resolve_active_client_and_license
from .whatsapp_provider import get_provider_for_client
from .meta_signup import exchange_code_for_token, fetch_phone_number_details, SignupExchangeError
from .signing_utils import build_signed_license_response, SigningKeyNotConfigured


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


@csrf_exempt
@require_POST
def validate_license(request):
    """
    Called by FitDesk Pro on startup.

    Request body:  {"license_key": "FDPX-XXXX-XXXX-XXXX", "machine_id": "...", "device_name": "..." (optional)}

    Response body -- every field that existed before this change is still
    present, unchanged, so an already-installed FitDesk Pro build that only
    understands the old shape keeps working exactly as it did:
        {"valid": true/false, "status": "active|expired|suspended|revoked|not_found|device_limit_reached|device_blocked",
         "expiry_date": "YYYY-MM-DD", "days_until_expiry": N, "gym_name": "...", "message": "..."}

    New fields, added for the feature-entitlement/device-activation layer
    (only present once LICENSE_SIGNING_PRIVATE_KEY is configured -- see
    licensing/signing_utils.py; omitted, not null, if it isn't, so an
    updated desktop build can tell "not signed yet" apart from "signed
    with zero features"):
        {"device_status": "active|limit_reached",
         "license_payload": {"license_id", "client_id", "license_key", "status",
                              "expiry_date", "plan_id", "features", "max_devices", "issued_at"},
         "signature": "<hex, Ed25519 signature over license_payload's canonical JSON>"}
    The desktop trusts ONLY fields read from inside a signature-verified
    license_payload -- never device_status, and never anything from the
    legacy top-level fields above, for feature/entitlement decisions.
    """
    if abuse.is_blocked("licence_ip", request):
        return JsonResponse({"valid": False, "status": "rate_limited", "message": abuse.MESSAGE}, status=429)

    data = _json_body(request)
    key = (data.get("license_key") or "").strip()
    machine_id = (data.get("machine_id") or "").strip()
    device_name = (data.get("device_name") or "").strip()[:120]

    if not key:
        return JsonResponse({"valid": False, "status": "not_found", "message": "No license key provided."}, status=400)

    # Every FitDesk Pro build sends machine_id (services/license_service.py
    # and ui/license/activation.py). Without it a key could be checked, and
    # a signed payload obtained, without binding any device (Phase 1 fix).
    if not machine_id:
        return JsonResponse({"valid": False, "status": "machine_id_required",
                             "message": "This request didn't identify the computer. Please update FitDesk Pro."}, status=400)

    try:
        lic = License.objects.select_related("client", "plan").get(key=key)
    except License.DoesNotExist:
        abuse.note_failure("licence_ip", request)
        return JsonResponse({"valid": False, "status": "not_found", "message": "License key not recognized."}, status=404)

    # ----------------------------------------
    # DEVICE BINDING (Device model -- supports more than one machine,
    # bounded by lic.max_devices; see License.get_or_activate_device)
    # ----------------------------------------

    device_error = None
    if machine_id:
        device, device_error = lic.get_or_activate_device(machine_id, device_name=device_name)

        if device_error == "device_blocked":
            return JsonResponse({
                "valid": False,
                "status": "device_blocked",
                "message": (
                    "This device has been blocked for this license. "
                    "Please contact FitDesk Pro support."
                ),
            }, status=403)

        if device_error == "device_limit_reached":
            return JsonResponse({
                "valid": False,
                "status": "device_limit_reached",
                "gym_name": lic.client.gym_name,
                "message": (
                    f"This license is already active on {lic.max_devices} device(s), "
                    "its current limit. Please contact FitDesk Pro support to add "
                    "this device or increase your device limit."
                ),
            }, status=403)

        # Backward-compat only: keep the legacy scalar field roughly in
        # sync so any external tooling still reading it directly (not
        # this codebase -- see license_utils.py, which now also goes
        # through get_or_activate_device) doesn't see a stale value.
        if not lic.machine_id:
            lic.machine_id = machine_id
            lic.save(update_fields=["machine_id"])

    # Record successful contact with the vendor server.
    lic.last_check_in = timezone.now()
    lic.save(update_fields=["last_check_in"])

    status = lic.effective_status
    days_until_expiry = (lic.expiry_date - date.today()).days
    messages = {
        "active": "License is active.",
        "expired": "This license expired on %s. Please contact support to renew." % lic.expiry_date,
        "suspended": "This license has been suspended. Please contact support.",
        "revoked": "This license has been revoked. Please contact support.",
    }

    response_data = {
        "valid": status == "active",
        "status": status,
        "expiry_date": lic.expiry_date.isoformat(),
        "days_until_expiry": days_until_expiry,
        "gym_name": lic.client.gym_name,
        "message": messages.get(status, ""),
    }

    # ----------------------------------------
    # SIGNED ENTITLEMENT PAYLOAD (new)
    #
    # "license_payload" + "signature" travel together as one unit -- the
    # desktop verifies the signature against exactly the bytes of
    # license_payload (canonical JSON, sorted keys) and trusts ONLY
    # fields read from inside it (features, max_devices, plan_id, ...).
    # device_status is a convenience/UX hint computed the same way but
    # deliberately left OUTSIDE the signed payload and at the top level:
    # it's not something a tampered value could exploit (the device
    # limit is enforced server-side, synchronously, above -- a forged
    # "device_status": "active" on a rejected request changes nothing,
    # since get_or_activate_device() already ran and already said no).
    # ----------------------------------------
    try:
        signed = build_signed_license_response(lic)
        response_data.update({
            "license_payload": signed["payload"],
            "signature": signed["signature"],
            "device_status": lic.device_status,
        })
    except SigningKeyNotConfigured:
        # LICENSE_SIGNING_PRIVATE_KEY isn't set yet (e.g. mid-rollout,
        # before the Render env var is added). Fall back to the legacy
        # response rather than 500ing every customer's startup -- an
        # updated desktop build treats a missing "signature" key exactly
        # like "server hasn't been upgraded yet" and just skips
        # feature-gating, same as it does against the current production
        # server today.
        print("WARNING: LICENSE_SIGNING_PRIVATE_KEY not configured -- returning unsigned legacy response.")

    return JsonResponse(response_data)


@csrf_exempt
@require_POST
def activate_device(request):
    """Explicit "activate this as an additional device" call -- used when
    the desktop has already been told device_status == "limit_reached" by
    validate_license and the gym owner has confirmed (e.g. after buying a
    higher device limit) that this specific machine should now count
    against the license. Distinct from validate_license's own automatic
    first-bind-up-to-the-limit behavior; this exists for the "admin just
    raised max_devices, activate the 3rd PC without reinstalling" flow.

    Request body:  {"license_key": "...", "machine_id": "...", "device_name": "..." (optional)}
    Response body: {"activated": true/false, "message": "...", "max_devices": N, "active_device_count": N}
    """
    data = _json_body(request)
    key = (data.get("license_key") or "").strip()
    machine_id = (data.get("machine_id") or "").strip()
    device_name = (data.get("device_name") or "").strip()[:120]

    if abuse.is_blocked("licence_ip", request):
        return JsonResponse({"activated": False, "message": abuse.MESSAGE}, status=429)
    if not key or not machine_id:
        return JsonResponse({"activated": False, "message": "license_key and machine_id are required."}, status=400)

    try:
        lic = License.objects.select_related("client").get(key=key)
    except License.DoesNotExist:
        abuse.note_failure("licence_ip", request)
        return JsonResponse({"activated": False, "message": "License key not recognized."}, status=404)

    if lic.effective_status != License.STATUS_ACTIVE:
        return JsonResponse({"activated": False, "message": f"This license is {lic.effective_status}."}, status=403)

    device, error = lic.get_or_activate_device(machine_id, device_name=device_name)

    if error == "device_blocked":
        return JsonResponse({"activated": False, "message": "This device has been blocked for this license."}, status=403)

    if error == "device_limit_reached":
        return JsonResponse({
            "activated": False,
            "message": f"Device limit reached ({lic.max_devices}). Contact FitDesk Pro support to increase it.",
            "max_devices": lic.max_devices,
            "active_device_count": lic.active_device_count,
        }, status=403)

    AuditLog.record("device_activated", license=lic, machine_id=machine_id, device_name=device_name)

    return JsonResponse({
        "activated": True,
        "message": "Device activated.",
        "max_devices": lic.max_devices,
        "active_device_count": lic.active_device_count,
    })


@csrf_exempt
@require_POST
def submit_query(request):
    """
    Called from the "Raise a Query" screen inside FitDesk Pro.

    Request body: {"license_key": "...", "subject": "...", "message": "..."}
    """
    data = _json_body(request)
    key = (data.get("license_key") or "").strip()
    subject = (data.get("subject") or "").strip()
    message = (data.get("message") or "").strip()

    if abuse.is_blocked("support_ip", request):
        return JsonResponse({"ok": False, "message": abuse.MESSAGE}, status=429)
    if not key or not subject or not message:
        return JsonResponse({"ok": False, "message": "license_key, subject and message are all required."}, status=400)

    try:
        lic = License.objects.select_related("client").get(key=key)
    except License.DoesNotExist:
        abuse.note_failure("support_ip", request)
        return JsonResponse({"ok": False, "message": "License key not recognized."}, status=404)

    client = lic.client
    query = SupportQuery.objects.create(client=client, subject=subject, message=message)

    notification_text = (
        f"Gym: {client.gym_name}\n"
        f"Owner: {client.owner_name}\n"
        f"Phone: {client.phone}\n\n"
        f"Subject: {subject}\n\n"
        f"{message}\n\n"
        f"— Reply by calling {client.phone} or resolve it in the admin dashboard."
    )

    # Notify the vendor (you) immediately via email. Configure ADMINS +
    # EMAIL_* in settings.py — see README for SMTP or console-based setup.
    try:
        mail_admins(
            subject=f"[FitDesk Support] {client.gym_name}: {subject}",
            message=notification_text,
            fail_silently=True,
        )
    except Exception:
        # A notification failure should never block the gym owner's query
        # from being saved.
        pass

    # Also push an instant Telegram alert if TELEGRAM_BOT_TOKEN /
    # TELEGRAM_CHAT_ID are configured in settings.py (no-op otherwise).
    telegram_text = (
        f"🔔 <b>New FitDesk Support Query</b>\n\n"
        f"<b>Gym:</b> {client.gym_name}\n"
        f"<b>Owner:</b> {client.owner_name}\n"
        f"<b>Phone:</b> {client.phone}\n\n"
        f"<b>Subject:</b> {subject}\n"
        f"<b>Message:</b> {message}"
    )
    send_telegram_notification(telegram_text)

    return JsonResponse({"ok": True, "query_id": query.id, "message": "Your query has been received. Our team will contact you shortly."})

@require_GET
def get_support_queries(request):
    """
    Return all support queries for the client identified
    by the provided license key.
    """

    license_key = request.GET.get("license_key", "").strip()

    if abuse.is_blocked("support_ip", request):
        return JsonResponse({"ok": False, "message": abuse.MESSAGE}, status=429)

    if not license_key:
        return JsonResponse(
            {
                "ok": False,
                "message": "License key is required."
            },
            status=400
        )

    try:
        license_obj = License.objects.select_related(
            "client"
        ).get(
            key=license_key
        )

    except License.DoesNotExist:
        abuse.note_failure("support_ip", request)
        return JsonResponse(
            {
                "ok": False,
                "message": "Invalid license key."
            },
            status=404
        )

    queries = SupportQuery.objects.filter(
        client=license_obj.client
    ).order_by("-created_at")

    results = []

    for query in queries:

        results.append(
            {
                "id": query.id,
                "subject": query.subject,
                "message": query.message,
                "status": query.status,
                "created_at": query.created_at.strftime(
                    "%d %b %Y, %I:%M %p"
                ),
                "admin_notes": query.admin_notes or "",
                "resolved_at": (
                    query.resolved_at.strftime(
                        "%d %b %Y, %I:%M %p"
                    )
                    if query.resolved_at
                    else None
                ),
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "queries": results
        }
    )


# ==============================================================
# WHATSAPP -- PER-GYM META CLOUD API INTEGRATION
#
# Every gym owner connects their OWN Meta WhatsApp Business account
# (see whatsapp_connect_start / whatsapp_connect_page /
# whatsapp_connect_callback below). There is no global WhatsApp
# credential anywhere in this project -- every endpoint here derives the
# Client strictly from a validated license+machine_id
# (license_utils.resolve_active_client_and_license) and then only ever
# touches THAT Client's own WhatsAppConfiguration row. A request
# authenticated with Gym A's license can never read, use, or modify Gym
# B's WhatsApp configuration -- there is no code path in this file that
# accepts a client id/gym id from the caller at all.
# ==============================================================

MAX_PDF_BYTES = 16 * 1024 * 1024  # comfortably above any real receipt PDF, well under what Meta's media endpoint accepts

ALLOWED_MESSAGE_TYPES = {"payment_receipt", "renewal_receipt", "invoice", "membership_confirmation"}

# Client-safe messages for each failure error_code -- result.error_detail
# (which can carry a raw Graph API error body) never reaches the caller;
# only these fixed strings do. Mirrors the friendly-message mapping the
# task spec asked for (expired token / invalid phone / missing template /
# rate limit / timeout / not configured).
_SAFE_ERROR_MESSAGES = {
    "not_configured": "WhatsApp Business is not connected for this gym.",
    "provider_error": "We could not send the WhatsApp message right now. Please try again shortly.",
    "rate_limited": "WhatsApp is temporarily rate limited. Please try again.",
}


def _mask_license_key(key):
    if not key:
        return "(none)"
    return ("…" + key[-4:]) if len(key) > 4 else "…"


def _pdf_looks_valid(pdf_file):
    """Cheap content check beyond just trusting the filename/content-type
    the client claims -- a real PDF starts with the '%PDF-' signature."""
    try:
        head = pdf_file.read(5)
        pdf_file.seek(0)
        return head == b"%PDF-"
    except Exception:
        return False


def _log_whatsapp_send(client, license_obj, phone, message_type, document_reference, mode, status, error_code="", provider_message_id=""):
    # A logging failure must never take down the response the desktop
    # app is waiting on -- it already got (or didn't get) its WhatsApp
    # send; losing the audit row for it is the lesser problem.
    try:
        WhatsAppSendLog.objects.create(
            client=client,
            license=license_obj,
            phone=phone,
            message_type=message_type,
            document_reference=document_reference,
            mode=mode,
            status=status,
            error_code=error_code,
            provider_message_id=provider_message_id,
        )
    except Exception as exc:
        print(f"[WhatsApp] WARNING: could not write WhatsAppSendLog row: {exc}")


def _resolve_or_error(request_data, require_machine_id=True):
    """Shared front door for every WhatsApp endpoint: resolves
    (client, license) from license_key+machine_id, or returns a ready-
    to-return error dict. Every caller below must use this rather than
    querying License directly, so they can never drift out of agreement
    about what a valid, bound license means."""
    license_key = (request_data.get("license_key") or "").strip()
    machine_id = (request_data.get("machine_id") or "").strip()
    client, lic, error = resolve_active_client_and_license(license_key, machine_id, require_machine_id=require_machine_id)
    return client, lic, error, _mask_license_key(license_key)


@csrf_exempt
@require_POST
def send_whatsapp_receipt(request):
    """
    Called by FitDesk Pro's "Send via WhatsApp" flow (manual button and
    automatic post-payment/renewal sends).

    multipart/form-data request fields:
      license_key, machine_id, phone, message, message_type,
      document_reference, test_mode ("true"/"false"), receipt_pdf (file),
      customer_name (optional, used as a template body variable)

    Response body:
      success -> {"sent": true, "message": "...", "provider": "whatsapp_cloud_api", "provider_message_id": "..."}
      failure -> {"sent": false, "error_code": "invalid_license"|"machine_mismatch"
                              |"invalid_phone"|"not_configured"|"provider_error"
                              |"rate_limited"|"invalid_pdf"|"test_mode",
                  "message": "<safe, human-readable>"}
      ("test_mode" isn't really a failure -- see Test Mode handling below;
      it uses "sent": false because nothing was actually sent, with a
      distinct error_code so the desktop can show it as a successful
      dry run rather than an error.)

    Never returns a raw provider error/stack trace to the caller --
    those are print()'d server-side only, matching how the rest of this
    project logs (there's no `logging` module usage anywhere else here).
    """
    license_key = (request.POST.get("license_key") or "").strip()
    machine_id = (request.POST.get("machine_id") or "").strip()
    phone_raw = (request.POST.get("phone") or "").strip()
    message = (request.POST.get("message") or "").strip()
    message_type = (request.POST.get("message_type") or "").strip()
    document_reference = (request.POST.get("document_reference") or "").strip()
    customer_name = (request.POST.get("customer_name") or "").strip() or "Customer"
    test_mode = (request.POST.get("test_mode") or "").strip().lower() in ("true", "1", "yes")
    mode = "test" if test_mode else "production"
    pdf_file = request.FILES.get("receipt_pdf")

    masked_key = _mask_license_key(license_key)

    # ----------------------------------------------------------------
    # 1. LICENSE + MACHINE BINDING -- the actual security boundary.
    #    Nothing below this point runs, and the WhatsApp provider is
    #    never touched, for a request that doesn't resolve to a real,
    #    active, correctly-bound account. The Client is ALWAYS the one
    #    derived here -- nothing later in this view can substitute a
    #    different one.
    # ----------------------------------------------------------------
    client, lic, error = resolve_active_client_and_license(license_key, machine_id)
    if error:
        print(f"[WhatsApp] rejected: license={masked_key} error={error['error_code']}")
        return JsonResponse({"sent": False, "error_code": error["error_code"], "message": error["message"]})

    # ----------------------------------------------------------------
    # 2. RATE LIMIT -- per license key, checked before any provider
    #    call and before this request is itself logged (so it isn't
    #    counted against its own check).
    # ----------------------------------------------------------------
    if is_rate_limited(lic):
        print(f"[WhatsApp] rate_limited: license={masked_key}")
        return JsonResponse({"sent": False, "error_code": "rate_limited", "message": _SAFE_ERROR_MESSAGES["rate_limited"]})

    # ----------------------------------------------------------------
    # 3. INPUT VALIDATION -- phone is re-validated here even though the
    #    desktop app already normalizes it; nothing caller-supplied is
    #    forwarded to the WhatsApp provider unchecked.
    # ----------------------------------------------------------------
    phone, phone_error = validate_whatsapp_phone(phone_raw)
    if phone_error:
        _log_whatsapp_send(client, lic, phone_raw, message_type, document_reference, mode, WhatsAppSendLog.STATUS_FAILED, error_code="invalid_phone")
        print(f"[WhatsApp] invalid_phone: license={masked_key} raw={phone_raw!r} reason={phone_error}")
        return JsonResponse({"sent": False, "error_code": "invalid_phone", "message": "The customer's WhatsApp number is invalid."})

    if message_type not in ALLOWED_MESSAGE_TYPES:
        _log_whatsapp_send(client, lic, phone, message_type, document_reference, mode, WhatsAppSendLog.STATUS_FAILED, error_code="invalid_message_type")
        return JsonResponse({"sent": False, "error_code": "provider_error", "message": "Unrecognized message type."})

    if not pdf_file:
        _log_whatsapp_send(client, lic, phone, message_type, document_reference, mode, WhatsAppSendLog.STATUS_FAILED, error_code="invalid_pdf")
        return JsonResponse({"sent": False, "error_code": "invalid_pdf", "message": "No receipt PDF was provided."})

    if pdf_file.size > MAX_PDF_BYTES:
        _log_whatsapp_send(client, lic, phone, message_type, document_reference, mode, WhatsAppSendLog.STATUS_FAILED, error_code="invalid_pdf")
        return JsonResponse({"sent": False, "error_code": "invalid_pdf", "message": "The receipt PDF is too large to send."})

    if not _pdf_looks_valid(pdf_file):
        _log_whatsapp_send(client, lic, phone, message_type, document_reference, mode, WhatsAppSendLog.STATUS_FAILED, error_code="invalid_pdf")
        print(f"[WhatsApp] invalid_pdf: license={masked_key} filename={pdf_file.name!r} did not look like a real PDF")
        return JsonResponse({"sent": False, "error_code": "invalid_pdf", "message": "The receipt file is not a valid PDF."})

    if not message:
        message = "Your receipt is attached."

    # ----------------------------------------------------------------
    # 4. THIS CLIENT'S OWN WHATSAPP CONFIGURATION -- get_provider_for_client()
    #    only ever builds credentials from client.whatsapp_configuration;
    #    there is no other source it could pull from.
    # ----------------------------------------------------------------
    provider = get_provider_for_client(client)
    if not provider.is_configured():
        _log_whatsapp_send(client, lic, phone, message_type, document_reference, mode, WhatsAppSendLog.STATUS_FAILED, error_code="not_configured")
        print(f"[WhatsApp] not_configured: license={masked_key} client={client.gym_name!r} mode={mode}")
        return JsonResponse({"sent": False, "error_code": "not_configured", "message": _SAFE_ERROR_MESSAGES["not_configured"]})

    # ----------------------------------------------------------------
    # 5. SEND -- the PDF is read into memory only for the duration of
    #    this request and never written to disk; nothing persists past
    #    the provider call below. In Test Mode, send_document() itself
    #    guarantees no real Meta message-send call is made -- see
    #    licensing/whatsapp_provider.py.
    # ----------------------------------------------------------------
    pdf_bytes = pdf_file.read()
    filename = pdf_file.name or f"{document_reference or 'receipt'}.pdf"
    body_params = [customer_name, client.gym_name, document_reference or ""]

    result = provider.send_document(
        phone=phone, message=message, pdf_bytes=pdf_bytes, filename=filename,
        body_params=body_params, test_mode=test_mode,
    )
    del pdf_bytes  # discard immediately -- nothing about the PDF is retained past this call

    if result.status == "test_ok":
        _log_whatsapp_send(client, lic, phone, message_type, document_reference, mode, WhatsAppSendLog.STATUS_SENT, error_code="test_mode")
        print(f"[WhatsApp] test_mode OK (no message sent): license={masked_key} client={client.gym_name!r}")
        return JsonResponse({
            "sent": False, "error_code": "test_mode",
            "message": "Test successful -- all checks passed. No message was sent (Test Mode).",
        })

    if result.ok:
        _log_whatsapp_send(
            client, lic, phone, message_type, document_reference, mode,
            WhatsAppSendLog.STATUS_SENT, provider_message_id=result.provider_message_id or "",
        )
        print(
            f"[WhatsApp] sent: license={masked_key} client={client.gym_name!r} phone={phone} "
            f"message_type={message_type} provider_message_id={result.provider_message_id}"
        )
        return JsonResponse({
            "sent": True,
            "message": "WhatsApp receipt sent successfully",
            "provider": "whatsapp_cloud_api",
            "provider_message_id": result.provider_message_id,
        })

    # ---- Failure: map to a friendly message, never the raw provider detail ----
    _log_whatsapp_send(client, lic, phone, message_type, document_reference, mode, WhatsAppSendLog.STATUS_FAILED, error_code=result.status)

    log_line = (
        f"[WhatsApp] FAILED: license={masked_key} client={client.gym_name!r} phone={phone} "
        f"message_type={message_type} mode={mode} status={result.status} detail={result.error_detail}"
    )
    if result.needs_human:
        # An expired/invalid access token doesn't fix itself -- the gym
        # owner needs to reconnect WhatsApp from Settings.
        print(f"[WhatsApp] *** ACTION NEEDED *** {log_line}")
        friendly = "WhatsApp connection has expired. Please reconnect WhatsApp."
    elif result.error_detail and "template" in (result.error_detail or "").lower():
        friendly = "The configured WhatsApp receipt template is unavailable or not approved."
    else:
        print(log_line)
        friendly = _SAFE_ERROR_MESSAGES.get(result.status, "We could not send the WhatsApp message right now.")

    if result.needs_human:
        # Mark the connection as needing attention so Settings' status
        # display and /api/whatsapp-status/ both reflect it without
        # requiring a separate check first.
        try:
            config = client.whatsapp_configuration
            config.connection_status = WhatsAppConfiguration.STATUS_ERROR
            config.last_error = friendly
            config.save(update_fields=["connection_status", "last_error"])
        except WhatsAppConfiguration.DoesNotExist:
            pass

    return JsonResponse({"sent": False, "error_code": result.status, "message": friendly})


@csrf_exempt
@require_POST
def whatsapp_status(request):
    """
    "Is WhatsApp connected and working right now for THIS gym?" -- called
    by FitDesk Pro Settings' "Test Connection" button (clicked often
    during setup) and to render the Settings screen's connection status.
    NEVER sends an actual WhatsApp message under any circumstance.

    JSON request: {"license_key": "...", "machine_id": "..."}
    JSON response (always HTTP 200):
      {"ok": true, "connected": true, "configured": true,
       "business_name": "...", "phone_number": "...",
       "message": "WhatsApp is connected"}
      {"ok": true, "connected": false, "configured": false,
       "business_name": "", "phone_number": "", "message": "<safe reason>"}
    "ok" is false only when the license itself doesn't resolve --
    "connected"/"configured" are the actual WhatsApp status once the
    license is fine.
    """
    data = _json_body(request)
    client, lic, error, masked_key = _resolve_or_error(data)
    if error:
        print(f"[WhatsApp] status check rejected: license={masked_key} error={error['error_code']}")
        return JsonResponse({
            "ok": False, "connected": False, "configured": False,
            "business_name": "", "phone_number": "", "message": error["message"],
        })

    config = getattr(client, "whatsapp_configuration", None)
    if config is None or not config.enabled:
        print(f"[WhatsApp] status check: not configured, client={client.gym_name!r}")
        return JsonResponse({
            "ok": True, "connected": False, "configured": False,
            "business_name": "", "phone_number": "",
            "message": "WhatsApp Business is not connected for this gym.",
        })

    provider = get_provider_for_client(client)
    if not provider.is_configured():
        print(f"[WhatsApp] status check: enabled but not ready, client={client.gym_name!r}")
        return JsonResponse({
            "ok": True, "connected": False, "configured": False,
            "business_name": config.business_name, "phone_number": config.phone_number,
            "message": "WhatsApp Business is not connected for this gym.",
        })

    check = provider.check_connection()
    config.last_connection_check = timezone.now()
    if check.ok:
        config.connection_status = WhatsAppConfiguration.STATUS_CONNECTED
        config.last_error = ""
    else:
        config.connection_status = WhatsAppConfiguration.STATUS_ERROR
        config.last_error = check.message
    config.save(update_fields=["last_connection_check", "connection_status", "last_error"])

    print(f"[WhatsApp] status check: client={client.gym_name!r} connected={check.ok}")
    return JsonResponse({
        "ok": True, "connected": check.ok, "configured": True,
        "business_name": config.business_name, "phone_number": config.phone_number,
        "message": "WhatsApp is connected" if check.ok else check.message,
    })


# ==============================================================
# WHATSAPP CONNECT / DISCONNECT (Meta Embedded Signup)
# ==============================================================

@csrf_exempt
@require_POST
def whatsapp_connect_start(request):
    """
    Starts a WhatsApp connection attempt for the Client resolved from
    license_key+machine_id. Returns a one-time link the desktop app
    opens in the user's system browser (Meta's Embedded Signup needs a
    real web page context -- it cannot run inside a native PySide6
    widget), scoped to exactly that Client via a short-lived
    WhatsAppConnectSession.

    JSON request: {"license_key": "...", "machine_id": "..."}
    JSON response:
      {"ok": true, "connect_url": "https://.../whatsapp/connect/<token>/", "expires_in": 900}
      {"ok": false, "message": "<safe reason>"}
    """
    data = _json_body(request)
    client, lic, error, masked_key = _resolve_or_error(data)
    if error:
        print(f"[WhatsApp] connect/start rejected: license={masked_key} error={error['error_code']}")
        return JsonResponse({"ok": False, "message": error["message"]})

    session = WhatsAppConnectSession.create_for(client)
    connect_path = f"/whatsapp/connect/{session.token}/"
    base_url = getattr(settings, "VENDOR_PUBLIC_BASE_URL", "") or request.build_absolute_uri("/").rstrip("/")
    connect_url = base_url.rstrip("/") + connect_path

    print(f"[WhatsApp] connect/start: license={masked_key} client={client.gym_name!r} session created")
    return JsonResponse({"ok": True, "connect_url": connect_url, "expires_in": 900})


def whatsapp_connect_page(request, token):
    """
    Plain HTML landing page (NOT under /api/) that the desktop app opens
    in the gym owner's default browser. Hosts Meta's Embedded Signup JS
    SDK, scoped to the Client this `token` was minted for at
    /api/whatsapp/connect/start/. See
    licensing/templates/licensing/whatsapp_connect.html.
    """
    try:
        session = WhatsAppConnectSession.objects.select_related("client").get(token=token)
        valid = session.is_valid
    except WhatsAppConnectSession.DoesNotExist:
        session = None
        valid = False

    context = {
        "valid": valid,
        "gym_name": session.client.gym_name if session else "",
        "meta_app_id": getattr(settings, "META_APP_ID", ""),
        "meta_configuration_id": getattr(settings, "META_CONFIGURATION_ID", ""),
        "api_version": getattr(settings, "META_API_VERSION", "v20.0"),
        "session_token_json": json.dumps(token),
        "callback_url_json": json.dumps(
            (getattr(settings, "VENDOR_PUBLIC_BASE_URL", "") or request.build_absolute_uri("/").rstrip("/")).rstrip("/")
            + "/api/whatsapp/connect/callback/"
        ),
    }
    return render(request, "licensing/whatsapp_connect.html", context)


@csrf_exempt
@require_POST
def whatsapp_connect_callback(request):
    """
    Called by the JS on the connect landing page once the gym owner
    finishes Meta's Embedded Signup flow. Identifies the Client SOLELY
    from the WhatsAppConnectSession `session_token` minted at connect/
    start/ -- never from anything else in the request body -- so a
    completed signup can only ever attach to the Client that actually
    started it.

    JSON request: {"session_token": "...", "code": "...", "waba_id": "...", "phone_number_id": "..."}
    JSON response: {"ok": true, "message": "WhatsApp connected."} or {"ok": false, "message": "<safe reason>"}
    """
    data = _json_body(request)
    session_token = (data.get("session_token") or "").strip()
    code = (data.get("code") or "").strip()
    waba_id = (data.get("waba_id") or "").strip()
    phone_number_id = (data.get("phone_number_id") or "").strip()

    if not session_token or not code:
        return JsonResponse({"ok": False, "message": "This connection link is invalid. Please try again from FitDesk Pro."})

    try:
        session = WhatsAppConnectSession.objects.select_related("client").get(token=session_token)
    except WhatsAppConnectSession.DoesNotExist:
        return JsonResponse({"ok": False, "message": "This connection link is invalid. Please try again from FitDesk Pro."})

    if not session.is_valid:
        return JsonResponse({"ok": False, "message": "This connection link has expired. Please try again from FitDesk Pro."})

    if not phone_number_id:
        return JsonResponse({"ok": False, "message": "Meta did not return a WhatsApp phone number. Please try again."})

    client = session.client

    try:
        access_token = exchange_code_for_token(code)
    except SignupExchangeError as exc:
        print(f"[WhatsApp] connect/callback: token exchange failed for client={client.gym_name!r}: {exc.safe_log}")
        return JsonResponse({"ok": False, "message": exc.message})

    display_phone_number, verified_name = fetch_phone_number_details(access_token, phone_number_id)

    config, _created = WhatsAppConfiguration.objects.get_or_create(client=client)
    config.set_access_token(access_token)
    config.phone_number_id = phone_number_id
    config.waba_id = waba_id
    config.business_name = verified_name or client.gym_name
    config.phone_number = display_phone_number
    config.connection_status = WhatsAppConfiguration.STATUS_CONNECTED
    config.enabled = True
    config.last_connection_check = timezone.now()
    config.last_error = ""
    config.save()

    session.consume()

    print(f"[WhatsApp] connect/callback: client={client.gym_name!r} connected phone_number_id={phone_number_id}")
    return JsonResponse({"ok": True, "message": "WhatsApp connected."})


@csrf_exempt
@require_POST
def whatsapp_disconnect(request):
    """
    Disconnects WhatsApp for the Client resolved from
    license_key+machine_id ONLY -- never the Client itself, the License,
    billing, or membership data, and never any other Client's
    configuration.

    JSON request: {"license_key": "...", "machine_id": "..."}
    JSON response: {"ok": true, "message": "WhatsApp disconnected."}
    """
    data = _json_body(request)
    client, lic, error, masked_key = _resolve_or_error(data)
    if error:
        print(f"[WhatsApp] disconnect rejected: license={masked_key} error={error['error_code']}")
        return JsonResponse({"ok": False, "message": error["message"]})

    try:
        config = client.whatsapp_configuration
        config.disconnect()
        print(f"[WhatsApp] disconnected: license={masked_key} client={client.gym_name!r}")
    except WhatsAppConfiguration.DoesNotExist:
        pass  # already disconnected -- disconnecting twice is not an error

    return JsonResponse({"ok": True, "message": "WhatsApp disconnected."})
