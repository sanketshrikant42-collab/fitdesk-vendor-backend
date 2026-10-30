# Phase 1: live server check and hardening (29 Oct 2026)

## 1. What's live today (read-only check)

| Item | Finding |
|---|---|
| Code on Render | GitHub `sanketshrikant42-collab/fitdesk-vendor-backend`, branch `main` = commit `fb85ac2` ("feat: add cloud backup backend"), pushed 4 Oct 2026. Local `D:\projects\vendor_backend` is on the same commit, with nothing uncommitted in the app code. |
| Live address | `https://fitdesk-vendor-backend.onrender.com`. The admin login loads, debug mode is **off** (plain "Not Found" pages), `/api/v1/` doesn't exist yet. Security headers: X-Frame-Options DENY, nosniff, Referrer-Policy same-origin. **No HSTS.** |
| **Free web plan** | The server was **asleep** and took about **30 seconds** to wake up (29 Oct, 20:28 IST). The desktop app's licence check gives up after **10 seconds**, so after a quiet period the first check fails and the app falls back to its 5-day offline grace. That's the "Internet Connection Required" risk in the licensing audit. |
| FitDeskCloud vs live | `licensing/` and `cloud_backup/` are **identical**. Deploying the new system **only adds**: 5 new apps, new tables, routes under `/api/v1/`, and three new packages. Nothing the desktop app uses changes shape. |
| Settings | Admin alerts go to the placeholder `you@example.com`. Telegram is blank in code. Package versions aren't pinned, so every Render rebuild installs the newest Django. |
| Licence endpoints | No limit on wrong-key attempts. `validate-license` answers without a `machine_id`, which gives a signed licence without binding any device. |

**Not checked yet (needs the Render dashboard):**
- database plan and whether backups are on;
- Python version;
- list of environment variable **names**;
- whether the production database has every migration applied.

## 2. Fixes made in the copy (`D:\projects\FitDeskCloud`, not deployed)

| Fix | Detail |
|---|---|
| HTTPS on Render | When `RENDER=true`: http → https redirect, secure cookies, HSTS. HSTS starts at 1 day; raise `SECURE_HSTS_SECONDS` to 31536000 later. |
| Key guessing | 20 wrong licence keys per network address per 15 min on `validate-license`, `devices/activate`, `submit-query` and `support-queries`, then "wait 15 minutes" (HTTP 429). Correct keys are never counted. Counts are stored in the database, so they're right across workers. |
| `machine_id` required | `validate-license` without `machine_id` → 400 `machine_id_required`. Every FitDesk build already sends it, and all old licensing tests pass. |
| Admin alerts | `ADMIN_EMAILS`, `SERVER_EMAIL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` now come from Render environment variables. |
| Pinned packages | `requirements.txt` lists exact versions (Django 6.1.2 …). `.python-version` = 3.13. |
| Health check | `GET /healthz/` → `{"ok": true}` after a database check. Use it as Render's health check path, so a broken deploy never replaces a working one. |
| Test pages | Only with `DEBUG` (your PC) or `ENABLE_TEST_CONSOLE=True` (the staging copy). Never on the live server. |
| Progress photos | Render's disk is wiped on every deploy. With `MEDIA_STORAGE=r2`, photos go to Cloudflare R2 under `media/`. Without it, photo upload is refused on Render with a clear message, rather than lost. |

Tests: 201, all passing (7 new).

Render deploy check (`check --deploy` with `RENDER=true`): only the two subdomain/preload HSTS notes remain. They're expected, because `onrender.com` isn't our domain.

## 3. Plan

1. **Dashboard facts:** Sanket reads out what section 1 couldn't check. Plan names and variable **names** only, never values.
2. **Upgrade** (Sanket approves the cost; prices from render.com/pricing, 29 Oct 2026):
   - Web service Free → "Less than 1 CPU" (512 MB): **$7/month**. Always on, no 30-second wake-up.
   - Database → smallest paid plan, **$6/month**, with backups, if it isn't paid already.
   - Daily cron job (`run_daily_jobs`, 1 minute a day): a few cents a month.
3. **Staging copy** (free web service + its own database):
   - New Render service from a new branch `fitdesk-cloud`.
   - `ENABLE_TEST_CONSOLE=True`, its own `SECRET_KEY`, and **no** production keys.
   - Test the whole app online there, including import and email.
4. **Production deploy** (Sanket approves the day):
   - back up Postgres;
   - merge to `main`;
   - manual deploy (Auto-Deploy stays off);
   - `migrate`, which only creates new tables;
   - check `/healthz/`;
   - check P&P's live licence;
   - add the cron job and set `PUBLIC_BASE_URL`.
5. **Rollback:** redeploy `fb85ac2` in Render. The new tables are left unused and don't affect licensing or backups.
