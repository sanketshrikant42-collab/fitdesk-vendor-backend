# Cloud Backup — backend deployment runbook

The Cloud Backup module is the new `cloud_backup` Django app inside **this existing
project** (same Render service, same Postgres database, same license/device
authentication). No new service, no new database.

> Nothing in this runbook has been executed against production. Every step below is
> yours to run, in this order.

## What changes

| Area | Change |
|---|---|
| New app | `cloud_backup/` — models, R2 storage wrapper, API views, admin, tests, 2 management commands |
| URLs | `vendor_backend/urls.py`: `path('api/backups/', include('cloud_backup.urls'))` |
| Settings | `INSTALLED_APPS += ['cloud_backup']`, R2/backup env vars, `LOGGING` (sends `cloud_backup` INFO logs to stdout → Render logs; keeps botocore at WARNING) |
| Requirements | `+ boto3` |
| Migrations | `licensing/0006_cloud_backup_feature` (data only: adds the `cloud_backup` feature to **premium** + **legacy_full_access**), `cloud_backup/0001_initial` (2 new tables). Purely additive — no existing table is altered. Side effect: the signed `features` list returned by `/api/validate-license/` for Premium/Legacy licenses now also contains `cloud_backup` (the desktop's Phase 4 FeatureManager isn't built yet, so nothing reads it). |
| Commands | `r2_selftest`, `check_backup_keys [--rewrap]`, `cloud_backup_maintenance` |

## Environment variables to add in Render (Dashboard → fitdesk-vendor-backend → Environment)

**Required**

| Key | Value |
|---|---|
| `R2_ACCOUNT_ID` | your Cloudflare account id |
| `R2_ACCESS_KEY_ID` | the bucket-scoped R2 API token's Access Key ID |
| `R2_SECRET_ACCESS_KEY` | the token's Secret Access Key — add as a **secret**, never anywhere else |
| `R2_BUCKET_NAME` | `fitdesk-pro-backups` |
| `SECRET_KEY` | Django's own secret key. **Required since this change:** on Render the app refuses to start without it, or with the public fallback that's committed in `settings.py`. If it's already set, keep it. If not, generate one with `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Changing it only logs you out of /admin. |
| `BACKUP_KEY_ENCRYPTION_KEY` | generate once (below). **Also store it in your password manager — losing it makes every gym's cloud backups unrestorable on a new PC.** |

Generate `BACKUP_KEY_ENCRYPTION_KEY` on your own PC (never paste it into chat):

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Which values are secret:
* **Secret:** `R2_SECRET_ACCESS_KEY`, `BACKUP_KEY_ENCRYPTION_KEY` and `SECRET_KEY`. Anyone holding them
  can read or write the bucket, or decrypt gym keys.
* **Sensitive (treat as secret):** `R2_ACCESS_KEY_ID`. It identifies the token, but is
  useless without the secret.
* **Not secret:** `R2_ACCOUNT_ID` and `R2_BUCKET_NAME`.
* Render has no separate "secret" flag for plain env vars, and every value is hidden in
  the dashboard by default. Just never put them in git, chat or screenshots.

Does adding or changing them deploy? On saving, Render asks you to pick **"Save,
rebuild, and deploy"**, **"Save and deploy"** or **"Save only"**
([Render changelog](https://render.com/changelog/specify-deploy-behavior-when-modifying-environment-variables)).
Pick **Save only** in step B. The running (old) code ignores these variables anyway,
so even an accidental redeploy is harmless.

**Optional** (defaults are fine)

| Key | Default | Purpose |
|---|---|---|
| `R2_ENDPOINT` | `https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com` | only for an EU-jurisdiction bucket (`https://<id>.eu.r2.cloudflarestorage.com`) |
| `R2_OBJECT_PREFIX` | *(empty)* | e.g. `staging` for a test deployment sharing the bucket |
| `CLOUD_BACKUP_RETENTION_DAILY` | `30` | verified daily backups kept per gym |
| `CLOUD_BACKUP_RETENTION_MANUAL` | `10` | verified manual backups kept per gym |
| `CLOUD_BACKUP_MAX_FILE_MB` | `512` | upload size cap |
| `CLOUD_BACKUP_PRESIGN_EXPIRY_SECONDS` | `900` | lifetime of upload/download URLs |

The R2 token needs **Object Read & Write** on `fitdesk-pro-backups` only. The bucket
stays private; no public access and no CORS rules are needed (the desktop app isn't a
browser).

If these variables are missing, nothing breaks: every `/api/backups/*` endpoint answers
`503 storage_not_configured` / `key_service_not_configured`, and desktops keep backups
local-only and retry later.

## What must be backed up (key management)

Being able to restore a gym's cloud backups onto a **new** PC depends on **both** of these:

1. **`BACKUP_KEY_ENCRYPTION_KEY`**, the Render env var. Keep an offline copy in your
   password manager.
2. **The `cloud_backup_gymbackupkey` table** in the Render Postgres database, which holds
   each gym's key encrypted with (1). It's covered by your normal Postgres backups.

If you lose either one, a replacement PC can't decrypt that gym's existing cloud
backups. PCs that have already cached their gym key can still restore. R2 holds only
ciphertext and never any key. Nothing else uses `BACKUP_KEY_ENCRYPTION_KEY`: it is
read only in `cloud_backup/keys.py`.

**Changing the key.** Never just replace the value:

1. Set it to `NEW_KEY,OLD_KEY` (comma-separated, newest first).
2. Run `python manage.py check_backup_keys --rewrap`.
3. When that reports `unreadable=0`, the old key may be removed.

If someone does replace the value outright, nothing is regenerated or deleted. Gym
keys just become unreadable, `/api/backups/key/` returns `500 key_unreadable`, and
`check_backup_keys` exits with an error. So the mistake is loud, and putting the old
key back fixes it.

## Render free tier: where to run commands

The free web-service plan has **no Shell** and **no pre-deploy command**
([Render docs](https://render.com/docs/free), [deploys](https://render.com/docs/deploys)).
So the one-off commands below run **on your own PC**, inside the backend venv, against
production:

```
cd D:\projects\vendor_backend\vendor_backend
venv\Scripts\activate            (or your usual venv)
set DATABASE_URL=<Render Postgres *External* Database URL>
set R2_ACCOUNT_ID=...   set R2_ACCESS_KEY_ID=...   set R2_SECRET_ACCESS_KEY=...
set R2_BUCKET_NAME=fitdesk-pro-backups   set BACKUP_KEY_ENCRYPTION_KEY=...
python manage.py migrate
python manage.py check_backup_keys
python manage.py r2_selftest
```

Use `set` in a terminal session, or a local `.env` file, which `.gitignore` already
ignores, and delete it afterwards. Never commit these values or paste them into chat.
If you're on a paid plan, the Render Shell works the same way.

## Production sequence (after your approval)

| Step | What | Who |
|---|---|---|
| A | Render Dashboard → Postgres → create/download a backup | **Manual (you)** |
| B | Render → fitdesk-vendor-backend → Environment: add the required variables (and confirm `SECRET_KEY` exists). Choose **"Save only"** so nothing deploys yet | **Manual (you)** |
| C | `git switch -c feature/cloud-backup` in **both** repos | Claude, on approval (or you) |
| D | Backend: `git add` **only** the files listed in "What changes" (not `vendor_backend.zip`, `venv/` or `db.sqlite3`), then commit | Claude, on approval |
| E | Desktop: `git add` only the Cloud Backup files, then commit | Claude, on approval |
| F | `git push -u origin feature/cloud-backup` (both repos). A feature branch does **not** deploy | Claude, on approval |
| G | Merge to `main` on GitHub (PR) | **Manual (you)** |
| H | Render auto-deploys `main` (watch the deploy log for `pip install` + start) | Automatic |
| I | `python manage.py migrate` against production (see "Render free tier" above). Run it immediately after H, because the new endpoints answer 500 until the tables exist. Existing endpoints aren't affected either way | **Manual (you)** |
| J | `python manage.py check_backup_keys`, then `python manage.py r2_selftest` | **Manual (you)** |
| K | `curl` status check (below) with a Premium/Legacy license → `entitled: true, configured: true` | Manual, or Claude can run it |
| L | `installer\build_installer.bat` on your Windows PC (runs `build_exe.ps1` + `verify_build.py` + Inno Setup) | **Manual (you)**, or Claude via the desktop app's computer control if you allow it |
| M | Install `FitDeskPro_Setup_v1.1.0.exe` on a **test** Windows PC/VM with a test license | **Manual (you)** |
| N | Settings › Backup & Cloud › Backup Now → "Backup Complete"; file appears under `C:\ProgramData\FitDesk Pro\Backups\YYYY\MM\` | Manual |
| O | Status turns to "Up to date · 1 in cloud"; object visible in R2 under `gyms/GYM-xxxxx/backups/…`; `schtasks /Query /TN "\FitDesk Pro\Backup Agent" /V` shows the task (Users group, hourly) | Manual |
| P | Restore that backup (This PC and FitDesk Cloud tabs) → restart → data back, *Safety* backup listed. Optionally restore onto a second PC | Manual |
| Q | Only then release 1.1.0 to customers (P&P Fitness first) | **Manual (you)** |

Smoke test for step K:
```
curl -s -X POST https://fitdesk-vendor-backend.onrender.com/api/backups/status/ ^
     -H "Content-Type: application/json" ^
     -d "{\"license_key\": \"FDPX-....\", \"machine_id\": \"<an already-registered machine id>\"}"
```
Expect `"ok": true, "entitled": true, "configured": true` for a Premium or Legacy Full
Access license.

**Optional:** `python manage.py cloud_backup_maintenance` retries failed R2 deletions,
re-applies retention and expires abandoned uploads. Retention already runs after every
upload, so this isn't required. Running it on a schedule would need a Render Cron Job,
which is a separate service, so run it by hand if ever needed.

## Rollback

* Code: redeploy the previous commit. The two new tables are simply unused.
* Migrations: `python manage.py migrate cloud_backup zero` drops the two new tables
  (backup **metadata** only, never the R2 objects). `licensing.0006` is data-only and
  its reverse is a no-op.

## API (all `POST`, JSON body always includes `license_key` + `machine_id`)

| Endpoint | Purpose | Entitlement needed |
|---|---|---|
| `/api/backups/status/` | entitled/configured, count, last backup, retention | active license |
| `/api/backups/key/` | this gym's backup encryption key (for a new/replacement PC) | active license |
| `/api/backups/request-upload/` | registers metadata, returns a 15-min presigned PUT for one exact key | `cloud_backup` |
| `/api/backups/complete-upload/` | server checks size + signed SHA-256 metadata + ETag(MD5), marks available, applies retention | `cloud_backup` |
| `/api/backups/list/` | this gym's available cloud backups | active license |
| `/api/backups/request-download/` | 15-min presigned GET + checksum for one backup | active license |
| `/api/backups/verify/` | `deep:true` streams the object and recomputes SHA-256 server-side | active license |
| `/api/backups/delete/` | deletes one backup (refuses the last remaining one) | active license |

Authorization is the same front door as the WhatsApp endpoints
(`licensing.license_utils.resolve_active_client_and_license`): the gym is **always**
derived from the validated license + device. Any `gym_id`/`client_id` in a request
body is ignored, and another gym's `backup_id` returns `404`.

## Licensing behavior

* Feature code `cloud_backup` (Plan/Feature/override system). Premium and Legacy Full
  Access include it; Basic/Standard get it per license via a feature override.
* License **expired / suspended / revoked**: all backup endpoints refuse (same as every
  other license-gated endpoint), but **nothing is deleted**. Backups are kept as-is until
  you decide otherwise. Renewal restores access immediately.
* Feature **removed** from a still-active license: new uploads are refused; the gym can
  still list, download (restore) and delete what's already stored.
* Local backups on the desktop never depend on any of this.

## Known limitations

* The backend holds each gym's backup key, Fernet-encrypted with
  `BACKUP_KEY_ENCRYPTION_KEY`. That's what makes restoring onto a replacement PC
  possible. It also means this isn't zero-knowledge: someone holding both the Render
  environment and the database could decrypt backups. Access to R2 alone can't.
* Deleting a `Client` in the admin cascades its backup metadata but leaves its R2
  objects behind. Delete the `gyms/GYM-xxxxx/` prefix in the Cloudflare dashboard if
  you ever remove a gym.
* `requirements.txt` was already unpinned before this change. Only the new `boto3` is
  pinned (`1.43.108`). Your local venv has **Django 5.2.17**, but an unpinned build on
  Render installs the newest Django the runtime allows. The Cloud Backup tests pass on
  both Django 5.2.17 (Python 3.10) and 6.1.1 (Python 3.13). Pinning everything to
  exactly what's in production today is recommended (see the review report).
