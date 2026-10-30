"""Sending email for a gym: receipts and diet plans (with the PDF
attached) and reminder messages.

Each gym either uses its own mailbox (Gmail / Outlook / Zoho with an app
password, or any SMTP server) or the server's mailbox (EMAIL_HOST env vars)
with the gym's email as Reply-To. Nothing is sent until one of the two is
set up, so a message never silently goes nowhere."""
import base64
import hashlib
import html
import smtplib
import socket

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from gym_accounts.api import ApiError
from gym_core.models import GymSettings

from .models import EmailLog, EmailSettings


# --- the stored password ------------------------------------------------------------------------

def _fernet():
    from cryptography.fernet import Fernet
    key = hashlib.sha256(("fitdesk-email:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(pw):
    return _fernet().encrypt(pw.encode()).decode() if pw else ""


def decrypt(token):
    from cryptography.fernet import InvalidToken
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:      # SECRET_KEY changed: the owner types the password again
        return ""


# --- settings -----------------------------------------------------------------------------------

def server_ready():
    """The server's own mailbox is usable (not the dev console backend)."""
    return bool(getattr(settings, "EMAIL_HOST", "")) and "smtp" in settings.EMAIL_BACKEND


def settings_json(es):
    return {"mode": es.mode, "provider": es.provider, "sender_email": es.sender_email, "from_name": es.from_name,
            "reply_to": es.reply_to, "smtp_host": es.smtp_host, "smtp_port": es.smtp_port, "security": es.security,
            "username": es.username, "has_password": bool(es.password_enc),
            "verified_at": es.verified_at.isoformat() if es.verified_at else None, "last_error": es.last_error,
            "ready": ready(es), "server_available": server_ready(),
            "providers": {k: {"host": h, "port": p, "security": s} for k, (h, p, s) in EmailSettings.PROVIDERS.items()}}


def ready(es):
    if es.mode == "fitdesk":
        return server_ready()
    return bool(es.sender_email and es.smtp_host and es.password_enc)


def update_settings(request, data):
    from gym_core.services import clean_email
    es = EmailSettings.for_client(request.client)
    if "mode" in data:
        if data["mode"] not in ("own", "fitdesk"):
            raise ApiError(400, "bad_mode", "Choose the gym's own email or FitDesk's.")
        es.mode = data["mode"]
    if "provider" in data:
        if data["provider"] not in EmailSettings.PROVIDERS:
            raise ApiError(400, "bad_provider", "Unknown email provider.")
        es.provider = data["provider"]
        host, port, sec = EmailSettings.PROVIDERS[es.provider]
        if host:
            es.smtp_host, es.smtp_port, es.security = host, port, sec
    for f in ("sender_email", "reply_to"):
        if f in data:
            setattr(es, f, clean_email(data[f]))
    if "from_name" in data:
        es.from_name = str(data["from_name"] or "").strip()[:120]
    if "username" in data:
        es.username = str(data["username"] or "").strip()[:254]
    if es.provider == "custom":
        if "smtp_host" in data:
            es.smtp_host = str(data["smtp_host"] or "").strip()[:120]
        if "smtp_port" in data:
            try:
                es.smtp_port = int(data["smtp_port"])
            except (TypeError, ValueError):
                raise ApiError(400, "bad_port", "Port must be a number, usually 587 or 465.")
        if "security" in data and data["security"] in ("tls", "ssl"):
            es.security = data["security"]
    if data.get("password"):
        es.password_enc = encrypt(str(data["password"]).replace(" ", "") if es.provider == "gmail" else str(data["password"]))
    if data.get("clear_password"):
        es.password_enc = ""
    es.verified_at = None          # changed: send a test again
    es.save()
    return es


# --- sending ------------------------------------------------------------------------------------

FRIENDLY = [
    ((smtplib.SMTPAuthenticationError,), "The email or app password was not accepted. For Gmail, use an app password "
                                          "(Google Account → Security → 2-Step Verification → App passwords), not your normal password."),
    ((smtplib.SMTPRecipientsRefused,), "The member's email address was refused. Check it for typos."),
    ((smtplib.SMTPSenderRefused,), "The mail server refused the sender address. Use the same address you signed in with."),
    ((socket.gaierror, socket.timeout, ConnectionError, TimeoutError, OSError),
     "Couldn't reach the mail server. Check the internet connection and the server name / port."),
]


def friendly(e):
    for types, msg in FRIENDLY:
        if isinstance(e, types):
            return msg
    return f"Email not sent: {str(e)[:200]}"


def _connection(es):
    if es.mode == "fitdesk":
        return get_connection(fail_silently=False)
    pw = decrypt(es.password_enc)
    if not pw:
        raise ApiError(400, "email_not_set_up", "Type the email password again in Settings → Email.")
    backend = settings.EMAIL_BACKEND if "locmem" in settings.EMAIL_BACKEND else "django.core.mail.backends.smtp.EmailBackend"
    return get_connection(backend, host=es.smtp_host, port=es.smtp_port, username=es.username or es.sender_email,
                          password=pw, use_tls=es.security == "tls", use_ssl=es.security == "ssl", timeout=20,
                          fail_silently=False)


def send(request, to, subject, text, html_body=None, attachments=(), kind="", ref="", member=None, es=None):
    """Sends one email and logs it. Raises ApiError with a plain-English
    reason if it can't be sent."""
    from gym_core.services import clean_email
    es = es or EmailSettings.for_client(request.client)
    if not ready(es):
        raise ApiError(400, "email_not_set_up", "Set up email first: Settings → Email (owner).")
    to = clean_email(to)
    if not to:
        raise ApiError(400, "no_email", "Enter the member's email address.")
    gs = GymSettings.for_client(request.client)
    name = (es.from_name or gs.gym_name).replace('"', "")
    if es.mode == "fitdesk":
        sender = f'"{name}" <{settings.DEFAULT_FROM_EMAIL}>'
        reply = es.reply_to or gs.email or es.sender_email
    else:
        sender = f'"{name}" <{es.sender_email}>'
        reply = es.reply_to
    msg = EmailMultiAlternatives(subject, text, sender, [to], reply_to=[reply] if reply else None,
                                 connection=_connection(es))
    if html_body:
        msg.attach_alternative(html_body, "text/html")
    for fname, data, ctype in attachments:
        msg.attach(fname, data, ctype)
    log = dict(client=request.client, kind=kind, ref=str(ref)[:60], member=member, to=to, subject=subject[:200],
               sent_by=getattr(request, "staff", None))
    try:
        msg.send()
    except ApiError:
        raise
    except Exception as e:                       # noqa: BLE001 - shown to the user in plain words
        err = friendly(e)
        EmailLog.objects.create(status="failed", error=err[:300], **log)
        es.last_error = err[:300]
        es.save(update_fields=["last_error"])
        raise ApiError(400, "email_failed", err)   # not 5xx: that would page the admins
    EmailLog.objects.create(status="sent", **log)
    if es.last_error:
        es.last_error = ""
        es.save(update_fields=["last_error"])
    return to


def wrap_html(gs, heading, paragraphs, rows=None, note=""):
    """A plain, phone-friendly email body in the receipt's colours."""
    e = html.escape
    tr = "".join(f'<tr><td style="padding:6px 0;color:#667085">{e(k)}</td>'
                 f'<td style="padding:6px 0;text-align:right;font-weight:600">{e(v)}</td></tr>' for k, v in (rows or []))
    paras = "".join(f'<p style="margin:0 0 12px">{e(p)}</p>' for p in paragraphs)
    contact = " · ".join(x for x in (gs.phone, gs.email) if x)
    return (f'<div style="font-family:Inter,Segoe UI,Arial,sans-serif;color:#172033;max-width:560px;margin:auto">'
            f'<div style="border-bottom:3px solid #6D5DF6;padding:8px 0 12px;margin-bottom:16px">'
            f'<div style="font-size:18px;font-weight:700">{e(gs.gym_name)}</div>'
            f'<div style="color:#667085;font-size:13px">{e(contact)}</div></div>'
            f'<div style="font-size:16px;font-weight:700;margin-bottom:10px">{e(heading)}</div>{paras}'
            + (f'<table style="width:100%;border-collapse:collapse;font-size:14px;margin:6px 0 14px">{tr}</table>' if tr else "")
            + (f'<p style="color:#667085;font-size:12px">{e(note)}</p>' if note else "")
            + '<p style="color:#98A2B3;font-size:11px;margin-top:20px">Sent by FitDesk Pro</p></div>')


def mark_verified(es):
    es.verified_at, es.last_error = timezone.now(), ""
    es.save(update_fields=["verified_at", "last_error"])
