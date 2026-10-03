# FitDesk Pro — Vendor License & Support Backend

This is **your** backend (SS Tech Solutions) — one central place to:
- Create clients (gym owners)
- Generate & manage license keys (active / expired / suspended / revoked)
- See every support query raised from inside FitDesk Pro, and get emailed
  the moment one comes in

It's a Django project. The admin panel *is* your dashboard — no separate
frontend needed.

## 1. Local setup (test it first)

```bash
python3 -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser   # this is YOUR admin login
python manage.py runserver
```

Open http://127.0.0.1:8000/admin/ and log in. You'll see **Clients**,
**Licenses**, and **Support queries**.

### Try it end to end
1. In the admin, add a Client (gym name, owner, phone).
2. Add a License for that client with an expiry date — the key is
   auto-generated (e.g. `FDPX-3BA2-A8FA-ECAC`). Copy it.
3. Test the API directly:
   ```bash
   curl -X POST http://127.0.0.1:8000/api/validate-license/ \
     -H "Content-Type: application/json" \
     -d '{"license_key": "FDPX-XXXX-XXXX-XXXX"}'
   ```
4. Try `PATCH`ing the License's status to `suspended` in the admin, then
   re-run the curl command — you'll see `"status": "suspended"` come back.

## 2. Turning on real email notifications

Right now, `EMAIL_BACKEND` in `settings.py` defaults to the **console
backend** — new support queries just print to your terminal. To actually get
emailed, set these environment variables (or edit the defaults in
`settings.py`):

```
ADMINS in settings.py must also list your name/email.

EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-sending-account@gmail.com
EMAIL_HOST_PASSWORD=your-app-password   # use a Gmail "App Password", not your real password
```

**Want instant phone alerts instead of/alongside email?** Telegram notifications
are already wired into the backend — you just need to turn them on:

1. Open Telegram, message **@BotFather**, send `/newbot`, and follow the
   prompts (name it anything, e.g. "FitDesk Support Alerts"). BotFather
   gives you a **token** that looks like `123456789:AAExampleTokenHere`.
2. Search for your new bot by the username you gave it and send it any
   message (e.g. "hi") — bots can't message you first.
3. In a browser, visit:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Find `"chat":{"id":987654321,...}` in the response — that number is your
   **chat_id**.
4. In `settings.py`, fill in:
   ```python
   TELEGRAM_BOT_TOKEN = '123456789:AAExampleTokenHere'
   TELEGRAM_CHAT_ID = '987654321'
   ```
5. Restart the server. Every new support query now pushes an instant
   Telegram message to you, in addition to email. Leave both blank to
   skip Telegram entirely — email keeps working on its own either way.

## 3. Deploying so gym owners' software can actually reach it

Any of these work well for a project this size and are cheap/free to start:
- **Render.com** — free/low-cost, easiest for Django, auto-deploys from GitHub
- **Railway.app** — similarly simple, generous free tier
- **PythonAnywhere** — beginner-friendly, Django-specific docs
- A **VPS** (DigitalOcean/Hetzner) if you want full control — more setup work

Whichever you pick:
1. Set `DEBUG = False` in settings.py for production.
2. Set `ALLOWED_HOSTS = ['your-domain.com']`.
3. Switch `DATABASES` from SQLite to PostgreSQL (all these platforms offer
   a free/cheap Postgres add-on) — SQLite is fine for local testing but
   not for a server handling concurrent requests from many gyms.
4. Once deployed, update `SERVER_URL` in the FitDesk Pro client's
   `services/license_client.py` to your real domain.

## Project structure
```
vendor_backend/
├── manage.py
├── requirements.txt
├── vendor_backend/          # Django project settings
└── licensing/
    ├── models.py               # Client, License, SupportQuery, WhatsAppSendLog,
    │                           # WhatsAppConfiguration, WhatsAppConnectSession
    ├── admin.py                # your dashboard — colored status badges, actions
    ├── views.py                # see API reference below for every endpoint
    ├── whatsapp_provider.py    # WhatsAppProvider interface + MetaCloudAPIProvider
    ├── meta_signup.py          # Embedded Signup code->token exchange
    ├── license_utils.py        # shared license+machine resolution for WhatsApp endpoints
    ├── crypto_utils.py         # encrypts/decrypts each gym's WhatsApp access token
    ├── phone_utils.py          # server-side phone validation for WhatsApp sends
    ├── ratelimit.py            # per-license rate limit for WhatsApp sends
    ├── templates/licensing/
    │   └── whatsapp_connect.html  # Embedded Signup landing page (opened in the
    │                              # gym owner's browser, not the desktop app)
    └── urls.py
```

## API reference

**POST /api/validate-license/**
```json
// request
{"license_key": "FDPX-XXXX-XXXX-XXXX", "machine_id": "optional"}
// response
{"valid": true, "status": "active", "expiry_date": "2026-09-30", "gym_name": "PowerFit Gym", "message": "License is active."}
```
`status` is one of: `active`, `expired`, `suspended`, `revoked`, `not_found`.

**POST /api/submit-query/**
```json
// request
{"license_key": "FDPX-XXXX-XXXX-XXXX", "subject": "...", "message": "..."}
// response
{"ok": true, "query_id": 1, "message": "Your query has been received..."}
```

**GET /api/support-queries/?license_key=FDPX-XXXX-XXXX-XXXX**
```json
// response
{"ok": true, "queries": [{"id": 1, "subject": "...", "message": "...", "status": "open", "created_at": "12 Sep 2026, 03:30 PM", "admin_notes": "", "resolved_at": null}]}
```

## WhatsApp — every gym connects their OWN Meta WhatsApp Business account

**There is no shared/global WhatsApp credential anywhere in this project.**
Each `Client` has at most one `WhatsAppConfiguration`, created only by that
gym owner completing their own Meta Embedded Signup from inside FitDesk Pro
(Settings → WhatsApp → Connect WhatsApp). Every send/status/disconnect call
below derives the `Client` strictly from the caller's `license_key` +
`machine_id` (`licensing/license_utils.py`) — never from anything else in
the request — so one gym's license can never touch another gym's WhatsApp
connection, template, or Meta credentials.

**POST /api/send-whatsapp-receipt/** — `multipart/form-data`, not JSON
```
license_key, machine_id, phone, message, message_type, document_reference,
test_mode ("true"|"false"), receipt_pdf (file), customer_name (optional)
```
```json
// response (success)
{"sent": true, "message": "WhatsApp receipt sent successfully", "provider": "whatsapp_cloud_api", "provider_message_id": "wamid...."}
// response (failure)
{"sent": false, "error_code": "invalid_license"|"machine_mismatch"|"invalid_phone"|"invalid_pdf"|"not_configured"|"provider_error"|"rate_limited"|"test_mode", "message": "..."}
```
`message_type` is one of `payment_receipt`, `renewal_receipt`, `invoice`,
`membership_confirmation`. `test_mode: true` validates the license, the
gym's WhatsApp connection, the phone number and the PDF, and (if
everything else checks out) makes one harmless read-only Meta call to
confirm the credentials are still valid — **it never sends a real
WhatsApp message** (`error_code: "test_mode"` on success, not a real
failure). Sends the PDF as the header of an approved WhatsApp message
**template** (`WhatsAppConfiguration.template_name`/`template_language`,
configurable per gym) — business-initiated messages outside an open
customer-service window require an approved template; a plain
document+caption message (the pre-Embedded-Signup approach) will not
reliably deliver. Rate-limited to 100 sends/hour per license key,
independently per gym. Every attempt is logged in the admin under
**Whatsapp send logs**.

**POST /api/whatsapp-status/**
```json
// request
{"license_key": "FDPX-XXXX-XXXX-XXXX", "machine_id": "..."}
// response
{"ok": true, "connected": true, "configured": true, "business_name": "PowerFit Gym", "phone_number": "+91 98765 43210", "message": "WhatsApp is connected"}
```
Backs FitDesk Pro Settings' connection status display and "Test
Connection" button. `ok: false` only means the license itself didn't
resolve; once it does, `connected`/`configured` describe that gym's own
WhatsApp connection. Never sends a real message. Always `HTTP 200`.

**POST /api/whatsapp/connect/start/**
```json
// request
{"license_key": "...", "machine_id": "..."}
// response
{"ok": true, "connect_url": "https://fitdesk-vendor-backend.onrender.com/whatsapp/connect/<token>/", "expires_in": 900}
```
The desktop app opens `connect_url` in the user's default browser (Meta's
Embedded Signup JS SDK needs a real web page, not a native widget). The
`<token>` is single-use and expires in 15 minutes, and is bound server-side
to exactly the `Client` resolved from `license_key`+`machine_id` — nothing
that page or the callback below receives can redirect the result to a
different gym.

**GET /whatsapp/connect/`<token>`/** — plain HTML, *not* under `/api/`. Hosts
the Embedded Signup button; on completion its JavaScript posts to the
callback below and shows "WhatsApp Connected" in the browser tab.

**POST /api/whatsapp/connect/callback/** — called by that page's JS, not by
the desktop app.
```json
// request
{"session_token": "...", "code": "...", "waba_id": "...", "phone_number_id": "..."}
// response
{"ok": true, "message": "WhatsApp connected."}
```
Exchanges `code` for an access token via Meta's OAuth token endpoint
(using this backend's own `META_APP_ID`/`META_APP_SECRET` — never a
per-gym secret), fetches the phone number's display name/number, and
saves all of it — token encrypted — onto the `Client` the `session_token`
was minted for. A missing/expired/already-used session token is rejected
outright; it can never attach WhatsApp assets to any Client, let alone
the wrong one.

**POST /api/whatsapp/disconnect/**
```json
// request
{"license_key": "...", "machine_id": "..."}
// response
{"ok": true, "message": "WhatsApp disconnected."}
```
Clears only the resolved Client's own `WhatsAppConfiguration` (token,
phone number id, WABA id, business name/number, connection status). Never
touches the Client, License, billing, or membership data, and never any
other Client's configuration.

### WhatsApp — environment variables (Render)
```
META_APP_ID                     # FitDesk Pro's own Meta App id (from the Meta developer console)
META_APP_SECRET                 # FitDesk Pro's own Meta App secret
META_CONFIGURATION_ID           # the Embedded Signup "configuration" created in Meta Business Manager
META_REDIRECT_URI               # only if Meta's current App setup requires one registered
META_API_VERSION                # defaults to v20.0 if unset
WHATSAPP_TOKEN_ENCRYPTION_KEY   # Fernet key encrypting every gym's stored access token -- generate with:
                                 #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
VENDOR_PUBLIC_BASE_URL          # this backend's own public URL, e.g. https://fitdesk-vendor-backend.onrender.com
                                 # (used to build the /whatsapp/connect/<token>/ link)
```
These are the vendor's OWN app-level Meta configuration -- **not** any
individual gym's WhatsApp credentials, which live encrypted in each
`WhatsAppConfiguration` row instead, created only through that gym's own
Embedded Signup. Leave all of these unset and every WhatsApp endpoint above
still works safely (`not_configured` / `connected: false` everywhere) --
gym owners just can't connect yet.

There used to be a single global `WHATSAPP_ACCESS_TOKEN` /
`WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_TEST_PHONE_NUMBER_ID` here from an
earlier, single-tenant version of this integration. **Those are gone.**
Using one shared Meta account for every gym is exactly what per-gym
Embedded Signup replaces — if they're still set in Render, they're now
inert and can be removed.

---

## Cloud Backup (`cloud_backup` app, Cloudflare R2)

FitDesk Pro desktops back up their local SQLite database every day, encrypt
it on the PC (AES-256-GCM) and upload it to the private R2 bucket
`fitdesk-pro-backups` under `gyms/GYM-<id>/backups/YYYY/MM/`. This backend never
receives the database itself. It authorizes each upload or download with the
license key and machine id, hands out a 15-minute presigned URL for **one exact
object key**, verifies what landed in R2, keeps the backup metadata, and applies
retention (latest 30 verified daily + 10 manual backups per gym).

R2 credentials exist only in this backend's environment. They are never sent
to the desktop app, returned by an API, or logged.

* Endpoints, licensing behavior, env vars, deployment and rollback:
  **`CLOUD_BACKUP_DEPLOY.md`**
* Live R2 check (throwaway `_selftest/` prefix): `python manage.py r2_selftest`
* Housekeeping (optional): `python manage.py cloud_backup_maintenance`
* Key check / rotation: `python manage.py check_backup_keys [--rewrap]` (run after every
  deploy and after any change to `BACKUP_KEY_ENCRYPTION_KEY`)
* Tests: `python manage.py test cloud_backup` (in-memory fake R2; the S3-protocol
  tests also run when the test-only `moto` package is installed)

### Cloud Backup — environment variables (Render)
```
R2_ACCOUNT_ID                 # Cloudflare account id
R2_ACCESS_KEY_ID              # bucket-scoped R2 API token (Object Read & Write)
R2_SECRET_ACCESS_KEY          # secret -- Render env only, never anywhere else
R2_BUCKET_NAME                # fitdesk-pro-backups
BACKUP_KEY_ENCRYPTION_KEY     # Fernet key encrypting every gym's backup key ("NEW,OLD" while rotating) -- generate with:
                              #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
                              # keep an offline copy: losing it makes cloud backups unrestorable on new PCs
# optional: R2_ENDPOINT, R2_OBJECT_PREFIX, CLOUD_BACKUP_RETENTION_DAILY (30),
#           CLOUD_BACKUP_RETENTION_MANUAL (10), CLOUD_BACKUP_MAX_FILE_MB (512),
#           CLOUD_BACKUP_PRESIGN_EXPIRY_SECONDS (900)
```
