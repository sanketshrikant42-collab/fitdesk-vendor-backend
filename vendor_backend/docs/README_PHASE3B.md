# Phase 3B — operations on the server

New Django app `gym_ops`, in this folder only. Rules were read from `GymERP` (v1.2.0); nothing in `GymERP` or `vendor_backend` was changed. Nothing is deployed.

## What's in it

| Area | What it does |
|---|---|
| **Staff** | Staff records with v1.2.0's code format (PPF000001…), designation, department, salary, salary basis. Department "Trainer" also creates a trainer record (as decided for v1.2.0). Can be linked to a login and a trainer record. |
| **Staff attendance** | Daily grid: Present, Half Day, Absent, Paid Leave, Weekly Off, Holiday, check-in/out. Saved in one go. Owner and manager mark it; no future dates. |
| **Payroll** | Same calculation as v1.2.0 (below). Salary advances, deductions, PT commission paid together with salary, once per month, with a reference required for non-cash. |
| **Expenses** | v1.2.0 categories and payment modes, Paid/Pending, filters and totals. Owner and manager only; delete is owner-only. |
| **Enquiries** | v1.2.0 pipeline: New → Contacted → Follow-up → Trial → Converted / Lost, with a follow-up log, due/overdue lists, an "assigned to me" list, source stats, conversion % and a WhatsApp follow-up link. Convert creates the member (or links an existing one). |
| **Referral rewards** (new) | An enquiry can be "referred by" a member. When it converts, the referrer gets free days (set in settings, e.g. 15) added to their running membership. If they have none, the reward is kept as pending. |
| **Diet & nutrition** | Same 16 starter plans and the same nutrition maths as v1.2.0 (its code was copied over unchanged). Food library, templates, draft → active, a new version on every revision (history kept), review-due list, safety flags, WhatsApp share. Trainers only see and plan for their own clients and can't edit the food library. |
| **Progress tracking** (new) | Weight, body fat, chest, waist, hips, arms, thighs and a photo per entry, with a weight series for a chart. The latest weight also updates the member's fitness profile. Photos are only ever shown to signed-in staff of the same gym. |
| **Consent form** (new) | Health questions and a drawn signature. The exact text the member agreed to is stored with it. The gym can set its own text. |
| **Support** | Tickets go into your existing vendor admin (Support queries), tagged with the person, role and device, plus a Telegram alert if you've set one up. |

## Payroll: same as v1.2.0

- Daily rate = salary ÷ days in the month ("Calendar Days") or ÷ 30 ("Fixed 30 Days").
- Paid days = Present + ½ Half Day + Paid Leave + Weekly Off + every Sunday so far (since joining) with nothing marked.
- Earned is rounded to whole rupees. Net = earned − advances − deduction, never below 0. Total = net + PT commission not yet paid.
- A month still running counts only the days so far; a mid-month joiner counts from their joining day.

Tested on worked examples. For instance, ₹31,000 with 20 present, 2 half days, 1 paid leave and 4 Sundays gives ₹26,000 earned. With a ₹2,000 advance, a ₹500 deduction and ₹2,000 PT commission due, the total paid is ₹25,500.

## Decisions for you

1. **Holidays:** v1.2.0's payroll does **not** pay "Holiday" days, although its attendance screen says holidays aren't deducted. Kept as v1.2.0 by default; one switch in settings pays them. **Which do you want?**
2. **Who sees salaries:** only the owner, by default. A setting lets managers see them too.
3. **Staff codes** use the same prefix as member codes (PPF000001 for both), as in v1.2.0. They're separate lists, so nothing clashes, but you may prefer a different look for staff (e.g. PPF-S001).
4. **Referral reward days:** 0 (off) until you set a number.
5. Still open from 3A: should the trainer's share be worked out on the amount **including GST** (as v1.2.0 does) or before GST?

## Who can do what (server-checked)

| | Owner | Manager | Receptionist | Trainer |
|---|---|---|---|---|
| Staff list | ✓ | view (no salaries*) | – | – |
| Mark staff attendance | ✓ | ✓ | – | – |
| Payroll, advances | ✓ | –* | – | – |
| Expenses | ✓ | add, view | – | – |
| Delete expense | ✓ | – | – | – |
| Enquiries | ✓ | ✓ | add, follow up, convert | – |
| Diet plans | ✓ | ✓ | – | own clients |
| Food library, templates | ✓ | ✓ | – | – |
| Progress, consent | ✓ | ✓ | ✓ | progress for own clients |
| Support tickets | ✓ (all) | own | own | own |

\* unless the owner turns on "Managers can see salaries".

## Try it

The server needs `python manage.py migrate` first. Then sign in on the Phase 2 console as before and open:

- `http://127.0.0.1:8000/api/v1/test-console/ops/`: Staff, Staff attendance, Payroll, Expenses, Enquiries & referrals, Diet, Progress, Consent form, Support, Phase 3B settings.

The **Acting as** box switches between owner and staff. To try a referral, first make a member with a running plan in the gym console, then put that member's code in "Referred by".

## Tests

`python manage.py test` runs 131 tests: 110 earlier plus 21 for Phase 3B. They cover:
- payroll worked examples against v1.2.0's rules: full month, fixed 30 days, month in progress, mid-month joiner, marked Sundays, holidays, rounding
- salary visibility, attendance permissions, paying payroll with PT commission only once
- expense roles and totals
- enquiry pipeline, conversion and referral reward (applied and pending)
- diet: starter → revise → history, trainer only own clients, saved nutrition unaffected by later library edits
- progress photos, consent, support
- every endpoint refusing another gym's data

## Before production

Progress photos are saved to a local `media/` folder in this copy. Before any gym uses photos for real, they'll be stored in your Cloudflare R2 bucket instead.
