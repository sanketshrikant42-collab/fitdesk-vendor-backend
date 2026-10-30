# FitDesk Pro API v1 — staff sign-in and Team & Access

Base URL: `https://<backend>/api/v1/`. JSON in, JSON out.

- **Success:** `{"ok": true, ...}`
- **Error:** `{"ok": false, "error": "<code>", "message": "<text to show the user>", ...extra}`

Signed-in calls send `Authorization: Bearer <access_token>`.

## The `device` object (sent by the app on sign-in calls)

```json
{"installation_id": "random 8-64 chars, made on first launch",
 "device_secret": "random 32-200 chars, kept in secure storage",
 "name": "Reception PC", "platform": "windows|macos|android|ios|web|linux", "app_version": "2.0.0"}
```

Generate both values once per install and keep them in the OS secure store
(Windows Credential Manager, Keychain, Android Keystore). Losing them makes the
install a new device, which then needs approval again.

## Sign-in (no token)

| Endpoint | Body | Result |
|---|---|---|
| `POST auth/owner-setup/` | `license_key, setup_code, name, phone, password, device` | 201 signed in (owner, device approved). If the owner already exists, the same call with a fresh setup code and the owner's own phone + password approves this new device (lost/changed PC) |
| `POST auth/join/` | `invite_code, password, name?, device` | 201 `status: "device_pending"` (or `signed_in`) |
| `POST auth/login/` | `phone, password, gym_code?, device` | 200 signed in |
| `POST auth/refresh/` | `refresh_token, device` | 200 new `access_token` + `refresh_token` (old refresh stops working) |
| `POST auth/device-status/` | `device` | 200 `devices: [{gym, status, ...}]`, for the waiting screen to poll |
| `POST auth/pin-users/` | `device` (shared) | 200 staff on this gym who have a PIN |
| `POST auth/pin-switch/` | `device, user_id, pin` | 200 signed in as that person |
| `POST auth/logout/` | — (Bearer) | 200 |

The **"signed in" body** contains:
- `access_token`, `refresh_token`, `access_expires_at`, `refresh_expires_at`
- `user` — `id, name, phone, role, status, permissions{module: [actions]}, scopes, overrides, must_change_password, has_pin`
- `device`
- `gym` — `id, name, gym_code`
- `licence` — `status, expiry_date, plan, features`

**Error codes the app should handle:**

| Code | HTTP | What it means |
|---|---|---|
| `device_pending` | 403 | Show the "Waiting for approval" screen and poll `device-status` |
| `device_blocked` | 403 | Rejected or removed |
| `device_personal` | 403 | Another person's device that isn't shared |
| `device_mismatch` | 403 | Secret doesn't match; reinstall |
| `bad_credentials` | 401 | Wrong number or password |
| `choose_gym` | 409 | Same number at several gyms; `gyms` list attached, resend with `gym_code` |
| `user_disabled` | 403 | Owner turned off this person's access |
| `licence_inactive` | 403 | Licence expired, suspended or revoked; `licence_status` attached. Full lock, as today |
| `too_many_attempts` | 429 | Rate limit hit |
| `weak_password` | 400 | Password fails validation |
| `bad_invite_code` | 400 | Wrong, used or expired invite code |
| `bad_setup_code` | 400 | Wrong, used or expired owner setup code |

## Signed-in user

| Endpoint | Body | Notes |
|---|---|---|
| `GET me/` | — | user + permissions, device, gym, licence, `server_time` |
| `POST me/password/` | `current_password, new_password` | ends all other sessions; returns new tokens |
| `POST me/pin/` | `password, pin` (`""` removes) | 4-6 digits |

Any signed-in call can return:
- `401 session_ended` or `401 token_expired` → refresh, then sign in again if that fails
- `403 device_not_approved`, `user_disabled` or `licence_inactive`
- `403 permission_denied` or `owner_only`

## Team & Access (owner only)

| Endpoint | Notes |
|---|---|
| `GET team/summary/` | `users{used,limit}`, `devices{used,limit,owner_free_in_use}`, `pending_devices`, `pending_invites`, `licence`, `gym_code` |
| `GET team/users/?q=&status=&page=` | each with their devices |
| `GET team/users/<id>/` | with permissions + overrides |
| `POST team/users/<id>/update/` | `name?, role?, linked_record?, email?` |
| `POST team/users/<id>/disable/` · `enable/` | disable ends all their sessions |
| `POST team/users/<id>/reset-password/` | `temporary_password`; they must change it at next sign-in |
| `POST team/users/<id>/sign-out-everywhere/` | |
| `GET/POST team/users/<id>/permissions/` | POST `{"overrides":[{"module","action","allowed"}]}` replaces; `[]` = role default |
| `GET/POST team/invites/` | POST `name, phone, role (manager|receptionist|trainer), linked_record?, salary_access? (none|view|manage)` → `invite_code`, `share_text` |
| `POST team/users/<id>/salary-access/` | `{"access": "none"|"view"|"manage"}`: view = see salaries, payroll, PT earnings; manage = also set salaries, pay, advances. Users carry `salary_access` |
| `POST team/invites/<id>/cancel/` | |
| `GET team/devices/?status=` | removed devices hidden unless `status=removed` |
| `POST team/devices/<id>/approve/` · `reject/` · `remove/` | `409 device_limit_reached` when full |
| `POST team/devices/<id>/rename/` | `name` |
| `POST team/devices/<id>/set-shared/` | `shared: true|false` |
| `GET team/activity/?page=` | who, what, when, which device |

Another gym's ids always answer `404 not_found`.

**Modules:** `dashboard, members, attendance, billing, plans, receipts, enquiries,
trainers, diet, staff, salaries, expenses, reports, settings, team, licence, support`.

**Actions:** `view, add, edit, delete, export`. For billing, `delete` means refund or cancel.

---

# Phase 3A — gym data (`gym_core`)

Same rules as above: `Authorization: Bearer <access_token>`, JSON in/out, another gym's ids → `404 not_found`, role checks → `403 permission_denied` (with `module`, `action`). Money is sent and returned as strings (`"1500.00"`); dates as `YYYY-MM-DD`; members, bills, memberships and check-ins have UUID ids.

| Endpoint | Permission | Notes |
|---|---|---|
| `GET/POST gym/settings/` | GET any role · POST settings.edit | gym name, GSTIN, `gst_percent`, `upi_id`, `upi_payee_name`, prefixes, footer, `whatsapp_receipt_template`, `trainer_share_includes_gst` |
| `GET dashboard/` | dashboard.view | money keys only for billing.view |
| `GET/POST plans/` | view: plans or billing · add: plans.add | fields: `name, duration_days, fee, discount_percent, is_personal_training, trainer_share_percent, pt_sessions, members_covered, freeze_max_days, freeze_max_times` |
| `POST plans/<id>/update/` · `archive/` | plans.edit · plans.delete | |
| `GET/POST trainers/` · `POST trainers/<id>/update/` | trainers.view/add/edit | `staff_user_id` links a trainer login |
| `GET trainers/?status=active|inactive` | trainers.view | each trainer has `staff` (`department`, `designation`) when they are a staff member, else `null` (freelance) |
| `POST trainers/<id>/remove/` | trainers.delete (owner) | `result: "deleted"` if never used, else `"inactive"` (old bills keep the name). Inactive trainers can't be picked on new bills (`400 trainer_inactive`) |
| `GET trainers/<id>/` | trainers.view (a trainer login: own record only) | profile: current clients with sessions left, past client count, sessions logged, linked staff record; `earnings` (this month, last 6 months, all time) only for the owner, managers when `managers_see_salaries` is on, or the trainer themself (`money_visible`) |
| `GET members/?q=&status=&page=` | members.view | status: active, expiring, expired, frozen, dues, no_plan, upcoming; trainers get own clients only |
| `POST members/` | members.add | `name, phone, gender, email?, date_of_birth?, address?, emergency_contact?, biometric_id?, fitness?{…}` → server code |
| `GET members/<id>/` | members.view | memberships, freezes, sessions, visits (with the last 20 check-ins in `visits.recent`), dues, bills (if billing.view) |
| `POST members/<id>/update/` | members.edit | send `version`; stale → `409 edit_conflict` with `current` |
| `POST members/<id>/fitness/` · `archive/` | members.edit · members.delete | |
| `POST bills/quote/` | billing.view | same body as create; returns totals without saving |
| `POST bills/` | billing.add (+billing.edit for discount or changed fee) | `member_id, plan_id, kind (new/renewal), start_date?, member_ids[] (couple/family), trainer_id (PT), discount, apply_gst, amount_paid, payment_mode, reference_no, client_ref` |
| `GET bills/?q=&member_id=&from=&to=&payment_status=` | billing.view | |
| `GET bills/<id>/` | billing.view | includes `whatsapp.link` and `upi.link` (when a balance is due) |
| `POST bills/<id>/payments/` | billing.add | `amount?` (blank = full balance), `mode, reference_no` |
| `POST bills/<id>/cancel/` | billing.delete | `reason` |
| `GET bills/<id>/receipt/` | billing.view | printable HTML |
| `POST memberships/<id>/freeze/` | members.edit | `days, start_date?, reason?` |
| `POST memberships/<id>/freezes/<fid>/end/` | members.edit | `date?` — unused days are returned |
| `POST memberships/<id>/sessions/` | attendance.add | logs one PT session |
| `GET checkins/lookup/?q=` | attendance.add | name / mobile / code / fingerprint ID → status, dues, already-in |
| `POST checkins/` | attendance.add | `member_id, client_ref?, override_reason?` (override needs attendance.edit). Second check-in same day → 200 `already_checked_in` |
| `GET checkins/?date=` | attendance.view | |

Plans: `gst_inclusive: true` means the fee already includes GST at the Settings GST %. Billing such a plan ignores `apply_gst`: total = fee − discount, GST = total − total × 100 / (100 + GST%). Bills and quotes return `gst_inclusive`; quotes also return `taxable` (price before GST). `GET plans/` returns `gst_percent` for live previews. Saving a GST-inclusive plan when GST % is 0 gives `gst_not_set_up`.

Errors to handle: `trainer_inactive`, `wrong_member_count`, `trainer_required`, `gst_not_set_up`, `overpaid`, `already_paid`, `freeze_not_allowed`, `freeze_limit`, `freeze_overlap`, `no_sessions_left`, `not_allowed_in` (with `member_status`), `phone_taken`, `biometric_taken`, `edit_conflict`.

---

# Phase 3B — operations (`gym_ops`)

| Endpoint | Permission | Notes |
|---|---|---|
| `GET/POST staff/` · `GET/POST staff/<id>/?month=YYYY-MM` | staff.view / add / edit | salary fields only for the owner or people with salary access. Detail also returns `attendance` for the month (a row per day + counts) and, for salary viewers, that month's `payroll`, advances and salary paid |
| `GET/POST staff-attendance/?date=` | GET staff.view · POST attendance.edit | POST `{rows:[{staff_id, date, status, check_in, check_out, remarks}]}` |
| `GET payroll/?month=YYYY-MM` | owner (salary visibility) | every active staff member's calculation |
| `GET staff/<id>/payroll/?month=&deduction=` | owner | earned, paid days, advances, PT commission, total, settled |
| `POST staff/<id>/payroll/pay/` | staff.edit | `month, mode, reference_no (required unless Cash), deduction?, remarks?` → once per month |
| `POST staff/<id>/advances/` | staff.edit | `amount, date?, mode?` |
| `GET/POST expenses/?from=&to=&category=&mode=&status=&q=` | expenses.view / add | totals included |
| `POST expenses/<id>/update/` · `delete/` | expenses.edit · delete | |
| `GET/POST enquiries/?view=open|due_today|overdue|mine|Converted|Lost` | enquiries.view / add | `referred_by_member_id` sets a referral; stats included |
| `GET/POST enquiries/<id>/` | enquiries.view / edit | detail with follow-ups and WhatsApp link |
| `POST enquiries/<id>/followup/` · `lost/` · `reopen/` · `convert/` · `archive/` | enquiries.edit (archive: delete) | convert `{member_id}` or creates the member; returns `referral_reward` |
| `GET members/<id>/referrals/` | members.view | |
| `GET/POST diet/foods/` | GET diet.view · POST owner/manager | `id` to update; macro warning returned |
| `GET diet/starters/?goal=&diet_type=` | diet.view | the 16 v1.2.0 starter plans |
| `GET/POST diet/templates/` | owner/manager to save | `from_starter` copies a starter |
| `POST diet/calculate/` | diet.view | live nutrition for `meals` |
| `GET/POST diet/plans/` | diet.view / add | POST `{member_id, starter_key | template_id | (name, meals, …), replace_active?}`. `replace_active: true` marks the member's other Active plans Completed; the reply has `replaced` (count). GET `?view=Active|Draft|Completed|Archived|review_due|all`; with no view, removed (Archived) plans are left out unless `member_id` is given |
| `GET/POST diet/plans/<id>/?version=` | diet.view / edit | draft → edit in place; active → new version; `{status}` alone changes status (`Archived` = removed from the member, `Active` = restore). `replace_active: true` with a change to Active replaces the member's other Active plans |
| `POST diet/plans/<id>/share/` | diet.view | WhatsApp link |
| `GET/POST members/<id>/progress/` | members.view · POST members.edit or trainer (own client) | JSON or multipart with `photo` (JPG/PNG/WEBP ≤ 3 MB) |
| `GET progress/<id>/photo/` | members.view | streams the photo |
| `GET/POST members/<id>/consent/` | members.view / edit | `{signed_name, signature_png (data URL), health_answers{}}` |
| `GET/POST support/` | support.view / add | `{category, subject, message}` |

`gym/settings/` gains: `holidays_paid` (default on for new gyms), `referral_reward_days`, `enquiry_whatsapp_template`, `consent_text`. `managers_see_salaries` is no longer used (salary access is per person).

Payroll rows include `not_marked`: working days so far with no attendance (they earn nothing). Staff codes: `PPF-S001`; trainer codes: `PPF-T001`.

---

# Phase 3C — automation & reports (`gym_auto`)

| Endpoint | Permission | Notes |
|---|---|---|
| `GET reminders/?date=&kind=&status=` | receipts.view | the day's list; opening today's list runs the morning job if needed. `counts`, `older_pending`, `call_outcomes`. Owner-summary items only for reports.view + billing.view |
| `GET reminders/older/` | receipts.view | unsent items from the last 14 days |
| `POST reminders/run/` | receipts.add | `{date?}` (not in the future); safe to repeat → `created` |
| `POST reminders/<id>/sent/` · `skip/` · `reopen/` · `call/` | receipts.add | call: `{outcome, notes}`; outcome from `call_outcomes` |
| `GET/POST reminders/settings/` | GET receipts.view · POST settings.edit | switches, `renewal_days` ("7,3,1,0,-3"), `dues_every_days`, `inactive_after_days`, `owner_summary_phone`, `tpl_*` (blank = default; `defaults` and `placeholders` returned) |
| `GET summary/?date=` | reports.view + billing.view | day's numbers + `text` + `whatsapp_link` to the owner |
| `GET reports/<name>/?from=&to=` | reports.view + data permission | name: `collections` (`&group=day|month`), `renewals`, `trainers` (salary access), `profit`, `enquiries`, `gst`. Shape: `summary[]`, `tables[{name, columns[{key,label,type}], rows[]}]`, `notes[]` |
| `GET reports/<name>/export/` | reports.export | same, as an .xlsx file |
| `GET bills/<id>/receipt.pdf` | billing.view | PDF receipt, same design as `bills/<id>/receipt/` (shared `gym_core/receipt.py`), with the gym logo |
| `GET bills/<id>/receipt-link/` | billing.view | `url` / `download_url` (signed, 15 minutes, no sign-in) and `filename` "RCPT00003 - Name.pdf". `receipt.pdf?download=1` downloads with the same name |
| `GET diet/plans/<id>/pdf/?version=&download=1` | diet.view (trainer: own clients) | diet plan PDF "Diet plan - Name - v2.pdf" |
| `GET diet/plans/<id>/pdf-link/` | diet.view | 15-minute link (`url`, `download_url`, `filename`) |
| `POST diet/plans/<id>/share/` | diet.view | `{include_pdf?}` → `whatsapp {text, link}` with a 60-day member link to that version's PDF; `pdf {url, filename, local_only}`. Uses `PUBLIC_BASE_URL` if set |
| `GET gym/logo.png` | any signed-in staff | the gym logo (404 if none) |
| `POST gym/settings/logo/` · `gym/settings/logo/remove/` | settings.edit | multipart `logo` (PNG/JPG/WEBP ≤ 2 MB; stored as PNG ≤ 600 px). `gym/settings/` returns `has_logo`, `logo_updated_at` |

Reminder `kind`: `renewal, expired, dues, birthday, inactive, owner_summary`. `status`: `pending, sent, skipped, done`. Each item has `text`, `phone`, `whatsapp_link`, `amount`, `upi_link`, `days`, `meta`.


## Members: delete / merge (owner, `members.delete`)

| Endpoint | Notes |
|---|---|
| `GET members/<id>/records/` | `records` (counts of bills, memberships, check_ins, diet_plans, progress, consent_forms, referral_rewards; only non-zero) and `same_phone[]` (other members whose number ends in the same 10 digits) |
| `POST members/<id>/delete/` | only when nothing is linked, else `409 member_has_records` with `records` |
| `POST members/<id>/merge/` | `{into_id}`: moves everything to `into_id`, fills blank fields, deletes `<id>`. Returns `member`, `moved` |

Adding or editing a member: phones are normalised (`09…`, `+0…`, `0091…` → `+91…`) and any member with the same last 10 digits gives `409 phone_taken` with `member {id, name, code}`. Emails are validated (`bad_email`).

## Email

| Endpoint | Permission | Notes |
|---|---|---|
| `GET/POST email/settings/` | GET receipts.view · POST settings.edit | `{mode: own|fitdesk, provider: gmail|outlook|zoho|custom, sender_email, password, clear_password, from_name, reply_to, smtp_host, smtp_port, security: tls|ssl, username}`. Returns `has_password` (never the password), `ready`, `verified_at`, `last_error` |
| `POST email/test/` | settings.edit | `{to?}`: sends a test email and marks the settings verified |
| `POST bills/<id>/email/` | billing.view | `{to?, save_to_member?}`: receipt with its PDF attached |
| `POST diet/plans/<id>/email/` | diet.view (trainer: own clients) | diet plan with its PDF attached |
| `POST reminders/<id>/email/` | receipts.view | sends the reminder text and marks it sent (`channel: email`) |
| `GET email/log/?member_id=` | receipts.view | last 50 emails |

Errors: `email_not_set_up`, `no_email`, `bad_email`, `email_failed` (400, with a plain-English reason). With no `to`, the member's saved email is used. In `fitdesk` mode the server's `EMAIL_*` env vars are used (`DEFAULT_FROM_EMAIL` as sender, the gym's email as Reply-To).
