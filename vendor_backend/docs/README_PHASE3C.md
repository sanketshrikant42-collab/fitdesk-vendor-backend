# Phase 3C: automation and reports

This phase adds a new Django app, `gym_auto`, in this folder only. Nothing in `GymERP` or `vendor_backend` was changed, and nothing is deployed.

## Before you run it

```
pip install -r requirements.txt     # adds openpyxl (Excel) and reportlab (PDF)
python manage.py migrate
```

## Your five answers (28 Oct 2026)

| Question | What was built |
|---|---|
| 1. Salary and attendance | See "How salary follows attendance" below. "Holiday" days are now **paid by default** for new gyms; existing gyms can switch it in Settings → Staff & payroll. Payroll now flags working days nobody marked, so they can be fixed before paying. |
| 2. Trainer share and GST | The owner chooses in Settings → Receipts & GST: the trainer's PT share is worked out on the **amount including GST** or the **amount before GST**. |
| 3. Who sees salaries | Chosen **per person** by the owner. When inviting someone, and later under People on the sign-in page, pick **Hidden**, **Can see** (salaries, payroll, trainers' PT earnings) or **Can see & pay** (also set salaries, pay salary, give advances). The old gym-wide "managers can see salaries" switch is gone; managers who had it keep "Can see". |
| 4. Codes | Staff codes are now `PPF-S001` and trainer codes `PPF-T001`. Members stay `PPF000001`. All use the gym's own prefix. Existing records keep their codes. |
| 5. Referral reward days | A gym setting the management decides (Settings → Referrals). 0 means off. |

### How salary follows attendance

1. **Daily rate** = monthly salary ÷ days in the month ("Calendar Days"), or ÷ 30 ("Fixed 30 Days").
2. **Paid days** = Present + ½ per Half Day + Paid Leave + Weekly Off + Holiday (when switched on) + each Sunday left unmarked.
3. **Not paid:** Absent, and any other day left unmarked. Unmarked days now show a warning on Payroll.
4. **Salary** = daily rate × paid days, rounded to whole rupees, minus advances and deductions. A trainer's unpaid PT commission is added on top.

For example, take a salary of ₹30,000 in a 30-day month. With 22 Present, 2 Half Days, 4 Sundays, 1 Holiday and 1 Absent, that's 28 paid days, so ₹28,000.

## What Phase 3C adds

### Today's reminders (Front desk → Today's reminders)

Every morning the server makes a "to send" list:

| Item | When |
|---|---|
| Renewal | 7, 3 and 1 days before the last day, and on the last day. The days can be changed. |
| Ended | 3 days after a membership ended. |
| Dues | Any balance due, repeated every 7 days until paid. |
| Birthday | On the day. Members born on 29 Feb are wished on 28 Feb in other years. |
| Not visiting | Active members not seen for 7 days or more. This is a call list where you record the result: Reached, Coming back, No answer and so on. |
| Owner summary | Yesterday's numbers, sent to the owner. Only people who can see bills and reports see it. |

How it behaves:
- **Send on WhatsApp** opens WhatsApp with the message ready and marks the item sent. **Undo** puts it back.
- Renewal and dues messages include the amount, the gym's UPI ID and a UPI payment link. **UPI QR** shows a code to scan at the counter.
- Members who have already renewed, frozen members and archived members are left out.
- Nothing is made twice, even if the job runs again. Items not sent in the last 14 days are flagged at the top of the list.
- You can change every message's wording in **Settings → WhatsApp message wording**. **Reset to default** brings back the built-in text.
- When the gym's Meta WhatsApp account is verified, the same list can be sent automatically.

**The morning job.** Until a schedule is set up, the list builds itself the first time anyone opens it each day, and **Check again now** re-runs it. On Render, add a Cron Job at 00:30 UTC (06:00 IST) that runs:

```
python manage.py run_daily_jobs
```

### Reports (Money → Reports)

| Report | What it shows |
|---|---|
| Daily summary | Collected (by payment mode), month so far, new joins, renewals, check-ins, expenses, enquiries, memberships ending soon, pending dues, plus the WhatsApp text to send. |
| Collections | By day or month, by payment mode and by plan. |
| Renewals | Memberships ending in the period, how many renewed, and a call list of those who haven't. |
| Trainers & PT | Clients, sessions, PT money collected, the trainer's share and the gym's share. Only people with salary access see this. |
| Income vs expenses | By month, with expenses by category. |
| Enquiries | By source, the conversion rate, and why enquiries were lost. |
| GST & Tally | GST invoices with CGST and SGST shown separately, plus a sales register laid out for Tally's Excel import. |

**Download Excel** saves any report. Each table is its own sheet, amounts are real numbers in ₹ format, and every sheet ends in a totals row.

### Receipts: one design everywhere, with the gym's logo (updated 28 Oct)

- The **PDF download** now looks the same as the on-screen and printed receipt. Both are built from one shared description of the receipt (`gym_core/receipt.py`), so they can't drift apart. They use the same Inter font, including the ₹ sign (the font files are in `gym_auto/fonts`, under the free OFL licence).
- **Gym logo:** the owner uploads it in **Settings → Gym logo** as a PNG, JPG or WEBP up to 2 MB. It's shrunk to 600 px and stored in the database, so every device and the PDF can use it. It shows at the top left of every receipt, beside the gym name, and in the app's menu. Owner only. **Remove** takes it off.
- Small changes made to both versions: amounts have thousands separators (₹ 3,300.00), dates read as 28 Oct 2026, and GST shows the CGST + SGST split.

### The app adjusts to each role

The test app only shows the pages a person's role allows. For example, a receptionist doesn't see Payroll or Reports.

## Tests

`python manage.py test` runs 185 tests, all passing.

## Receipt file names and diet plan editing (added 28 Oct)

- **Receipt PDFs save with a ready name:** `RCPT00003 - Heet Joshi.pdf`, i.e. the receipt number and then the member's name.
  - **Download PDF** saves it straight away.
  - **View PDF** opens it in a browser tab, and the browser's save button uses the same name.
  - The tab uses a private link that only works for 15 minutes, so the member's receipt can't be opened later by anyone holding the address.
- **Diet & nutrition** now has:
  - **+ New custom plan:** build a plan meal by meal. For each food you can set the amount, unit and a note for the member. Calories, protein, carbs and fat are worked out as you type. Save it as a draft or give it straight to the member.
  - **Customise** on any ready-made plan: opens it in the editor, so you can change it before giving it.
  - **Edit plan** on any plan: a draft is changed in place; an active plan saves as a new version with a "what changed" note. Old versions are kept.
  - **Our gym's templates:** save any plan as a template (**Save as template**), or make one from scratch, then **Give** it to any member or **Customise** it first. Owner and manager only.
  - **Food library:** the gym's foods with values per serving, used for the calorie numbers. Owner and manager only. It can be opened from inside the editor without losing the plan.

## Calories, protein, carbs and fat in diet plans (added 28 Oct)

- **Built-in food list:** 117 common Indian foods, with approximate values from ICMR-NIN IFCT 2017 and USDA. It covers every food in the 16 ready-made plans, so those plans now show complete totals. Names match whatever the case or punctuation, and other names work too ("kanda poha", "chaas", "dahi").
- **In the plan editor**, each food row shows kcal, protein, carbs and fat, with totals for each meal and for the day, plus the protein / carbs / fat split as a share of calories:
  - Picking a food fills in its usual amount and unit.
  - A hint under each row says where the numbers come from ("Vegetable poha: 100 g = 130 kcal · built-in list") or what's missing.
  - "Food list is per 100 g: use g" fixes a unit mismatch in one click.
  - **Type values** enters numbers by hand for anything not on the list. Hand-typed values are saved with the plan and marked "(typed)".
- **Plan details** show a table per meal with every food's values.
- **Food list** (owner and manager), with two tabs:
  - **Your foods**
  - **Built-in list**, where **Use own values** saves your gym's recipe in place of the built-in one, under all its other names too.

  Allergens are tick-boxes (dairy, gluten, peanuts, egg, fish, soy, tree nuts), so allergy warnings work.

## Diet plan PDF and sending it on WhatsApp (added 28 Oct)

- **Diet plan PDF** in the same style as the receipt: the gym's logo and details, the purple line, the member, the plan details, day totals (calories, protein, carbs, fat), every meal with each food's amount and values, the instructions, and a short note that values are approximate. It saves as `Diet plan - Sagar Mhaske - v1.pdf`.
- **Buttons:**
  - In the plan window: **View PDF**, **Download PDF** and **Send on WhatsApp**.
  - On each plan in the member's profile (Diet tab): **PDF** and **WhatsApp**.
- **Send on WhatsApp** opens the member's chat with the plan written out, plus a "Full plan with calories (PDF)" link. Any staff member who can see the plan can send it.
  - The link opens without signing in, works for 60 days, and always shows the version that was sent, even if the plan is changed later.
  - Set `PUBLIC_BASE_URL` (e.g. `https://your-app.onrender.com`) on the live server so links use the public address.
  - On a PC running the test server, the link points to that PC, so a member's phone can't open it. The app says so; for now use **Download PDF** and attach the file in WhatsApp Desktop or Web.
  - Attaching the PDF automatically needs WhatsApp's business service (Meta), which is waiting on your company documents.

## Plans with GST included, and changing or removing a member's diet plan (added 28 Oct)

### "Price includes GST" on plans (Plans → Add plan / Edit)

- Tick **Price includes GST** when the fee you type is what the member pays, GST included. The form shows the split straight away using the GST % in Settings, e.g. ₹1,500 = ₹1,271.19 + GST 18% ₹228.81 (CGST 9% + SGST 9%). With the box unticked it shows what the member would pay if GST is added on the bill.
- The box is greyed out until a GST % is set in **Settings → Receipts & GST**.
- On **New bill**, picking such a plan fixes "Add GST" to Yes with the note "This plan's price already includes GST". The total stays ₹1,500; the summary shows the price before GST and the GST inside it.
- The receipt (screen and PDF) shows "Fee (incl. GST)", "Price before GST" and "GST 18% included (CGST 9% + SGST 9%)". The GST & Tally report counts the GST inside the price, so returns stay correct.
- A discount comes off the inclusive price first, then the GST is worked out from what's left.
- Plans without the tick work as before (GST added on top only when "Add GST" is Yes).

### Changing or removing a member's diet plan (Diet & nutrition)

- **Each row** in the plan list now has **Edit**, **Change** and **Remove**.
- **Picking a member** on the right shows their current plan, with **Open**, **Edit** and **Remove**.
- **Change:** pick the new plan (Give, or Customise first). If the member already has a current plan, you choose **Replace current plan** or **Keep both**. Replacing marks the old plan Completed, so it stays in the member's history. The custom plan editor has the same choice as a tick-box, on by default.
- **Remove** (after a confirm) takes the plan away from the member. Nothing is deleted: it moves to the new **Removed** filter, where **Restore** brings it back.
- **Make active** on a draft and **Restore** both ask whether to replace the plan the member already has.
- Trainers can do all of this for their own clients. Receptionists still have no diet access.
- Fixed: messages on the Diet page ("Plan given…", "Pick a member first") were going to a hidden box on the Check-in page, so they never showed.

## Removing a duplicate member, and sending by email (added 28 Oct)

### Removing a member added twice or by mistake (owner)

Open the member → Overview → **Remove member…**. What you see depends on the entry:

- **Nothing linked** (no bills, visits or plans): **Delete permanently**.
- **Has bills or history:** it can't be deleted, so history is never lost. Choose one:
  - **Merge into the right entry:** pick the member to keep. Bills, memberships, visits, diet plans, progress, consent forms and referrals move to them, and any details missing there (email, birthday…) are filled in. Then the duplicate is deleted. Receipt numbers don't change. If the two have the same mobile number, the right member is picked for you.
  - **Archive:** hides the member from the list and reminders and keeps everything. Members → **Archived** shows them, and **Restore** brings them back.

**Duplicates are now caught when adding a member.** A mobile number typed with a 0 in front (09028644659), with +0, or with 0091 is now saved as +919028644659. Any member whose number ends in the same 10 digits is reported by name: "Sanket Shrikant (HMF000004) already has this mobile number."

### Sending by email

- **Settings → Email** (owner): choose Gmail, Outlook, Zoho or another provider, then enter:
  - the gym's email address;
  - an **app password** (for Gmail: Google Account → Security → 2-Step Verification → App passwords). The password is stored encrypted and never shown again.

  Then **Send a test email**. The badge shows whether email is working.
- **Email buttons:**
  - receipts: after saving a bill, in the bill window, and in the member's Bills tab;
  - diet plans: in the plan window and in the member's Diet tab;
  - Today's reminders: renewal, ended, dues and birthday reminders.
- **What gets sent:**
  - Receipts and diet plans go with the PDF attached (same file name as the download), plus a short summary in the email.
  - Reminders send the same wording as the WhatsApp message and are marked "sent".
- **The member's address:** the window fills in the member's email. If they have none, type it; **Save this email on the profile** (ticked by default) remembers it.
- Members see the gym's address and their replies go to the gym. Mistakes (wrong app password, mail server not reachable, address refused) are shown in plain words.
- Every email is logged (`GET email/log/`).
