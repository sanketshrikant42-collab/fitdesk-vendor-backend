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
    ├── models.py            # Client, License, SupportQuery
    ├── admin.py             # your dashboard — colored status badges, actions
    ├── views.py             # /api/validate-license/, /api/submit-query/
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
