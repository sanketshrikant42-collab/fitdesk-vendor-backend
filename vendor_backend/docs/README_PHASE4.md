# Phase 4: moving a gym from FitDesk v1.2.0

New app `gym_import`, in this folder only. The v1.2.0 database is only ever **read**: it's copied with SQLite's own backup method first, so it can be imported even while FitDesk is open. Nothing in `GymERP` or `vendor_backend` was changed, and nothing is deployed.

## Run it

```
cd D:\projects\FitDeskCloud
.venv\Scripts\activate
python manage.py migrate
python manage.py import_v120 "C:\ProgramData\FitDesk Pro\fitdesk.db" --owner-name "Owner name" --owner-phone 98xxxxxxxx
```

It creates a **new gym** with its own licence key and owner setup code. Then:

1. Open `http://127.0.0.1:8000/api/v1/test-console/`.
2. Create the owner with that licence key and setup code.
3. Open the test app.

| Option | What it does |
|---|---|
| `--dry-run` | Imports, checks and writes the report, then undoes everything. |
| `--replace` | Deletes the previous import of the same gym and imports fresh. Only a gym the importer created can be replaced; other gyms are never touched. |
| `--gym-name "…"` | Name for the new gym (default: the name in v1.2.0 settings). |
| `--report path.html` | Where to save the check report (default: next to the v1.2.0 file). |
| `--today YYYY-MM-DD` | Check membership status as of that day. |

Everything happens in one go: if anything is wrong (two members with one mobile number, a bill pointing at a missing member, a bad phone number), it stops with a plain message and **nothing is saved**.

## What moves

- **Members:** codes PPF000001… kept, fitness profiles, archived flag.
- **Plans:** including PT plans and trainer share.
- **Every receipt:** same receipt number, dates, fee, discount, GST, amount paid, payment mode, and the trainer and gym share. Each becomes a bill, a membership and a payment.
- **Trainers and staff:** their codes kept, staff attendance.
- **Payroll:** settlements (salary + PT commission), advances, trainer payouts.
- **Expenses.**
- **Enquiries and follow-ups:** codes ENQ-… kept, link to the member they became.
- **Diet:** food library, templates, diet plans with every version and food (calorie values filled in from the food list), share history.
- **Gym settings:** name, tagline, address, phone, email, GSTIN, GST %, receipt prefix and footer.
- **Numbering continues** after the highest imported number: next member PPF000011, next receipt RCPT00012. New staff and trainers get PPF-S001 and PPF-T001.

## Not moved (stays in v1.2.0)

- v1.2.0 logins. Everyone gets a new login by invite, owner first.
- Old activity log, message history and alert states.
- Trainer ↔ client links. A member's trainer now comes from their PT bill.
- Photos stored as files on the old PC.
- PT session log, if a gym used it. It's listed in the report.

## The check report

Saved as an HTML file next to the v1.2.0 file. It compares both sides directly, worked out separately, so a mistake would show up:

- **Counts** of every kind of record.
- **Money:**
  - billed, collected and dues;
  - collected by payment mode;
  - trainer share and gym share;
  - expenses, payroll and payouts.
- **Every receipt:** fee, discount, GST, paid and end date.
- **Every member:** active / expired / no plan, end date and dues.
- **Changed on the way in:**
  - phone numbers tidied;
  - impossible birthdays left blank (age kept);
  - "Working Days" salary basis saved as Calendar Days (same pay);
  - bills v1.2.0 called Paid while GST was still owed.
- **To clean up (your decision):**
  - test entries;
  - possible placeholder birthdays (1 Jan 2000);
  - shared emails;
  - staff photos.

There's a test that changes one payment after import and confirms the report catches it.

The report contains members' names and mobile numbers: keep it on the PC.

## Also changed

**"Not visiting" reminders:** v1.2.0 never recorded member check-ins, so for an imported gym "days not seen" counts from the import day. Otherwise every member would land on the call list on day one.

## First run: Sanket's own FitDesk (28 Oct 2026)

Source: `C:\ProgramData\FitDesk Pro\fitdesk.db` (gym "Sanket", GYM-00006). Result: **everything matches**.

| What | Count / amount |
|---|---|
| Members | 10 |
| Receipts | 11 |
| Plans | 5 |
| Trainers | 4 |
| Staff | 4 |
| Expenses | 2 |
| Enquiries | 2 |
| Diet plans | 4 |
| Billed | ₹48,500 |
| Collected | ₹41,000 |
| Dues | ₹7,500 |

What else the check showed:
- 8 changes made on the way in (listed above).
- 9 items to clean up.
- The encrypted `.fdb` cloud backup couldn't be used: its key stays on the gym's PC and on Render. The importer reads the plain `fitdesk.db` instead.

## Tests

`python manage.py test` runs 194 tests, all passing (9 for the importer).
