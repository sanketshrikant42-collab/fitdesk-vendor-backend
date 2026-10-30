# FitDeskCloud — multi-user, multi-device backend (Phase 2)

This folder is a **separate copy** of the vendor backend with one new Django
app, `gym_accounts`, added. Nothing in `D:\projects\GymERP` or
`D:\projects\vendor_backend` was changed, and nothing has been deployed.

It implements the approved proposal *FitDesk Pro Everywhere* (26 Oct 2026),
pages 03–07: one licence per gym, separate logins per staff member, owner-set
roles, and owner approval for every device.

## What changed compared with `vendor_backend`

| File | Change |
|---|---|
| `gym_accounts/` | **New app** (models, API, admin, tests, migration `0001`) |
| `vendor_backend/settings.py` | `'gym_accounts'` added to `INSTALLED_APPS` (one line) |
| `vendor_backend/urls.py` | `path('api/v1/', include('gym_accounts.urls'))` added |
| `README_MULTIUSER.md`, `API_V1.md` | New docs |

`licensing/` and `cloud_backup/` are byte-for-byte the originals. The new app only
*reads* `Client` and `License`. It adds no field to them, and staff devices are a
separate table, so the installed v1.x desktop app and `/api/validate-license/`
behave exactly as before. A test checks this.

No new dependencies: plain Django, the same style as the existing apps.

## What's in it

- **Owner setup.** You issue a one-time *owner setup code* in Django admin
  (Gym staff & access → Owner setup codes → Add). The gym owner enters licence
  key + setup code in the new app to create the single Owner login. The licence
  key alone is not enough (closes audit finding H2).
  If the owner later changes PC or loses their phone, a fresh setup code plus the
  owner's own mobile and password approves the new device (owner recovery).
- **Invites.** The owner invites a person (name, mobile, role). The API returns an
  8-character code (e.g. `K7QM-4XPA`, valid 48 h, single use) and a ready
  WhatsApp message text.
- **Roles and permissions.** Owner / Manager / Receptionist / Trainer with the
  defaults from page 03 (`gym_accounts/permissions.py`). The owner can turn
  individual actions on or off per person. *Team & access* and *Licence* can
  never be given to anyone but the owner. Trainers are scoped to their own
  clients (applied by the Phase 3 data endpoints).
- **Devices.** Each app install has a random installation id and secret, not a MAC
  hash, so it works on phones and survives a network-card change. Every new device
  waits for owner approval. Devices are personal by default; the owner can mark
  one *shared* (front desk), which allows PIN switching between staff.
- **Limits.** Per gym, editable in admin (*Gym access settings*): `max_users`
  (default 5, owner and pending invites count), `max_devices` (default 3), and
  `owner_free_devices` (default 1, so the owner's own phone is free). Approvals lock
  the gym row, so two approvals at the same moment can't exceed the limit.
- **Sessions.** 30-minute access token plus a 30-day rotating refresh token, stored
  only as hashes. Every request re-checks the user, the device and the licence, so
  disabling a person, removing a device or an expired/suspended licence takes
  effect on the very next call.
- **Rate limits** (stored in the database): 5 wrong passwords per number per 15 min,
  30 per IP; 10 bad invite codes per IP per 15 min; 10 bad setup codes per IP per hour;
  5 wrong PINs per device per 15 min.
- **Activity log.** Every invite, approval, removal, permission change and sign-in,
  with who did it and on which device (the owner's *Activity log* tab).

## Run it locally (Windows, inside `D:\projects\FitDeskCloud`)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate          # creates a fresh local db.sqlite3 here only
python manage.py test             # 82 tests: 53 original + 30 new
python manage.py createsuperuser
python manage.py runserver
```

This folder has its own `db.sqlite3` (created by `migrate`). It never touches
your production Postgres unless you set `DATABASE_URL` yourself.

## Tests (all passing)

`gym_accounts/tests.py`, 30 tests covering the Phase 2 acceptance criteria:
- the full invite → join → device waiting → approve → sign-in flow
- **gym isolation**: gym 1's owner gets "not found" for every gym 2 user and device,
  and lists only show their own gym
- **revocation on the next request**: disable user, remove device, change password,
  logout, licence expired or suspended
- user and device limits, including the free owner device
- personal vs shared devices and PIN switching
- owner protection, and override rules (Team & access can't be granted)
- refresh-token rotation, rate limiting, wrong device secret
- the legacy `/api/validate-license/` response is unchanged and staff devices
  don't use licence device slots

## Not done yet (next phases)

- **Sending the invite on WhatsApp from the server.** For now the API returns
  `share_text`, and the app opens WhatsApp with it. It can later go through the
  existing per-gym WhatsApp provider.
- **Push notifications** for "new device wants access". The app polls
  `team/summary` for now.
- **Gym data** (members, billing, attendance and the rest) is Phase 3.
- **Flutter apps** are Phase 5.
- **Phase 1 production check.** Someone with Render access has to confirm the live
  deploy commit, `showmigrations` and env vars before any of this goes live.

## Later: merging into the real backend (only with your approval)

Because the change is one new app plus two lines, merging is: copy `gym_accounts/`
into the real backend repo, add the same two lines, push, then run
`python manage.py migrate gym_accounts`. That only creates new tables. Back up the
Render Postgres first, as in your existing runbook.
