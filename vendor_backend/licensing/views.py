import json
from datetime import date

from django.conf import settings
from django.core.mail import mail_admins
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from .models import Client, License, SupportQuery
from .notifications import send_telegram_notification


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

    Request body:  {"license_key": "FDPX-XXXX-XXXX-XXXX", "machine_id": "..."}
    Response body: {"valid": true/false, "status": "active|expired|suspended|revoked|not_found",
                     "expiry_date": "YYYY-MM-DD", "gym_name": "...", "message": "..."}
    """
    data = _json_body(request)
    key = (data.get("license_key") or "").strip()
    machine_id = (data.get("machine_id") or "").strip()

    if not key:
        return JsonResponse({"valid": False, "status": "not_found", "message": "No license key provided."}, status=400)

    try:
        lic = License.objects.select_related("client").get(key=key)
    except License.DoesNotExist:
        return JsonResponse({"valid": False, "status": "not_found", "message": "License key not recognized."}, status=404)

    # ----------------------------------------
    # MACHINE BINDING
    # ----------------------------------------

    # First activation / first successful validation:
    # bind this license to the current machine.
    if machine_id and not lic.machine_id:

        lic.machine_id = machine_id
        lic.save(update_fields=["machine_id"])

    # License is already activated on another computer.
    elif lic.machine_id and machine_id and lic.machine_id != machine_id:

        return JsonResponse({
            "valid": False,
            "status": "machine_mismatch",
            "message": (
                "This license is already activated on another computer. "
                "Please contact FitDesk Pro support."
            )
        }, status=403)

    
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

    return JsonResponse({
        "valid": status == "active",
        "status": status,
        "expiry_date": lic.expiry_date.isoformat(),
        "days_until_expiry": days_until_expiry,
        "gym_name": lic.client.gym_name,
        "message": messages.get(status, ""),
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

    if not key or not subject or not message:
        return JsonResponse({"ok": False, "message": "license_key, subject and message are all required."}, status=400)

    try:
        lic = License.objects.select_related("client").get(key=key)
    except License.DoesNotExist:
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