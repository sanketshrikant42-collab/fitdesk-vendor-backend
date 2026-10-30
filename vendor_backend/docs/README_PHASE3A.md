# Phase 3A — core gym data on the server

New Django app `gym_core`, in this folder only. `GymERP` (v1.2.0) was read to copy
its rules exactly; nothing in it or in `vendor_backend` was changed. Nothing is deployed.

## What's in it

| Area | What it does |
|---|---|
| **Gym settings** | Gym name, address, GSTIN, GST %, **UPI ID**, receipt prefix, member code prefix, receipt footer and WhatsApp text. Stored once on the server and used by every device. |
| **Plans** | Duration, fee, PT and trainer share (as in v1.2.0), plus new: **couple/family size**, **PT session pack size**, **freeze days and times allowed**. |
| **Trainers** | Trainer records (TRN-001…); can be linked to a trainer login so the trainer sees only their own clients. |
| **Members** | Server-issued codes (PPF000001…), unique mobile per gym, fingerprint (eSSL) ID, fitness profile, archive. Two people editing the same member at once → the second is told and shown the latest. |
| **Billing** | New and renewal bills, discount, GST, partial payment, dues collection, cancel (owner), printable receipt, **tap-to-send WhatsApp link**, **UPI pay link** for the balance. Retrying a save never bills twice. |
| **Freeze / pause** | Within the plan's limits; end date moves forward; unused days are taken back if the member returns early. |
| **PT session packs** | Each session is logged and counted down. |
| **Couple / family plans** | One bill, one receipt, a membership for each person covered. |
| **Member check-in** | Front desk and trainer phones. Once per member per day; expired or frozen members are refused unless an owner/manager lets them in with a reason. |
| **Dashboard** | Members, active, expiring in 7 days, check-ins today; money figures only for roles that can see billing. |

## Same rules as v1.2.0 (tested against its own examples)

- Expiry follows calendar months for 30/90/180/365-day plans (21 Aug → 21 Sep), otherwise plain days.
- A renewal starts the day after the previous membership ends.
- GST is added on top of (fee − discount). A PT plan needs a trainer.
- The trainer gets share % of each amount received.
- Codes: `PPF000001`, receipts `RCPT00001`, trainers `TRN-001`.
- The WhatsApp receipt text is v1.2.0's default.

## Differences from v1.2.0

1. **Paid status:** v1.2.0 marked a bill "Paid" once the fee *before GST* was covered, even with GST still owed. Now a bill is Paid only when nothing is due.
2. **Collecting dues later:** v1.2.0's "Mark as paid" never gave the trainer their share of the late money. Each payment is now its own row with its own trainer share.
3. **Member code prefix:** v1.2.0 always used `PPF` (P&P's initials), in every gym. Each gym now gets its own prefix from its name, or one set in Settings. P&P stays `PPF`.
4. **Trainer share and GST:** v1.2.0 calculates the trainer's % on the amount *including* GST. This is kept as the default so P&P's numbers match. Settings can switch it to the amount before GST. **Your decision.**
5. **Dates:** "today" uses the gym's time zone (India), not the server's UTC clock.
6. **Plan discount % and "GST inclusive":** in v1.2.0 these are saved on plans but never used at billing. Here too the plan discount % is only shown as a "final fee"; staff enter the discount on the bill.

## Who can do what (server-checked)

| | Owner | Manager | Receptionist | Trainer |
|---|---|---|---|---|
| Members: add/edit | ✓ | ✓ | ✓ | view own clients |
| Members: archive | ✓ | – | – | – |
| Bills: create, collect dues | ✓ | ✓ | ✓ | – |
| Discount / change fee | ✓ | ✓ | – | – |
| Cancel bill | ✓ | – | – | – |
| Plans: add/edit | ✓ | ✓ | view | – |
| Check-in | ✓ | ✓ | ✓ | own clients |
| Let in without active plan | ✓ | ✓ | – | – |
| Freeze membership | ✓ | ✓ | ✓ | – |
| Gym settings | ✓ | view | view | view |

Two permission areas were added to Team & Access: **Membership plans** and **Support**.

## Try it

```powershell
cd D:\projects\FitDeskCloud
.venv\Scripts\activate
python manage.py migrate
$env:DEBUG="True"
python manage.py runserver
```

1. Open `http://127.0.0.1:8000/api/v1/test-console/` and sign in an owner (and a receptionist) as before.
2. Open `http://127.0.0.1:8000/api/v1/test-console/gym/`.
3. Use the **Acting as** box (bottom left) to switch between owner and staff.
4. Suggested order: Gym settings (GST %, UPI ID) → Plans → Members → New bill → Bills & dues → Front desk.

The UPI QR image needs internet (it loads a small QR library).

## Tests

`python manage.py test` runs 110 tests: 83 earlier plus 27 for Phase 3A. They cover:
- v1.2.0 rule examples
- GST and partial payments
- dues and trainer shares
- retries that must not double-bill
- renewals, couple plans, freezes, session packs
- check-in rules and trainer-only-own-clients
- every endpoint refusing another gym's data
- an expired licence locking everything

## Not in 3A (coming in 3B / 3C)

- **3B:** staff, payroll, staff attendance, expenses, enquiries, referrals, diet, progress and consent forms.
- **3C:** daily reminder lists, birthday wishes, owner summary, inactive-member alerts, reports, GST/Tally export.
- **Receipt as a PDF file:** for now the receipt is a printable page (Print → Save as PDF). A PDF download comes with 3C reports.
