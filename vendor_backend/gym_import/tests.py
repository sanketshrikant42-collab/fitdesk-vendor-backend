import os
import sqlite3
import tempfile
from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from gym_core.models import Bill, Counter, GymSettings, Member, Payment
from gym_ops.models import DietPlan, Enquiry, Expense, PayrollSettlement, Staff

from .importer import Importer, ImportProblem
from .models import ImportRun
from .reconcile import reconcile
from .source import Source

SCHEMA = os.path.join(os.path.dirname(__file__), "v120_schema.sql")
TODAY = date(2026, 10, 28)


def make_v120(path, extra=None):
    """A small made-up v1.2.0 database: 4 members, 3 plans (one PT), a
    partly paid bill, a GST bill, staff + payroll, expenses, an enquiry and
    a diet plan."""
    db = sqlite3.connect(path)
    db.executescript(open(SCHEMA).read())
    x = db.execute
    x("INSERT INTO app_settings (id, setting_key, setting_value, gym_name, phone, email, tagline, gst_percent) "
      "VALUES (1,'gym_name','x','Iron Den','9822000000','den@example.com','Lift more','18')")
    x("INSERT INTO app_settings (id, setting_key, setting_value) VALUES (2,'receipt_prefix','RCPT')")
    for row in [(1, 'Monthly', 30, 1500, 0, 0), (2, 'Quarterly', 90, 4000, 0, 0), (3, 'PT', 30, 7000, 1, 50)]:
        x("INSERT INTO membership_plans (id, plan_name, duration_days, original_fee, status, is_personal_training, "
          "trainer_share_percent, discount_percent, gst_inclusive) VALUES (?,?,?,?, 'Active', ?, ?, 0, 0)", row)
    x("INSERT INTO trainers (id, trainer_code, full_name, phone, gender, status) VALUES (1,'TR000001','Vikram','9123456701','Male','Active')")
    x("INSERT INTO staff (id, employee_code, full_name, phone, gender, designation, department, joining_date, monthly_salary, "
      "salary_basis, status, trainer_id) VALUES (1,'SF000001','Vikram','9123456701','Male','Trainer','Trainer','2026-09-01',"
      "12000,'Working Days','Active',1)")
    members = [(1, 'PPF000001', 'Asha Rao', '9876500001', 'Female', 30, '1996-05-10'),
               (2, 'PPF000002', 'Bilal Khan', '09876500002', 'Male', 28, '2000-01-01'),      # trunk 0, placeholder DOB
               (3, 'PPF000003', 'Test Member', '9876500003', 'Male', 25, None),
               (4, 'PPF000004', 'Chitra Iyer', '9876500004', 'Female', 40, None)]
    for m in members:
        x("INSERT INTO members (id, member_code, full_name, phone, gender, age, date_of_birth, status, is_archived, created_at) "
          "VALUES (?,?,?,?,?,?,?, 'Active', 0, '2026-09-01 10:00:00')", m)
    tx = [  # id, receipt, member, plan, fee, paid, disc, remaining, status, join, expiry, trainer, tshare, gshare, mode, gst, gstamt
        (1, 'RCPT00001', 1, 1, 1500, 1500, 0, 0, 'Paid', '2026-10-10', '2026-11-10', None, 0, 1500, 'Cash', 0, 0),
        (2, 'RCPT00002', 2, 2, 4000, 2000, 0, 2000, 'Partially Paid', '2026-09-01', '2026-12-01', None, 0, 2000, 'UPI', 0, 0),
        (3, 'RCPT00003', 4, 3, 7000, 7000, 0, 1260, 'Paid', '2026-10-01', '2026-11-01', 1, 3500, 3500, 'Cash', 1, 1260),
        (4, 'RCPT00004', 3, 1, 1500, 1500, 100, 0, 'Paid', '2026-08-01', '2026-09-01', None, 0, 1500, 'Cash', 0, 0),
    ]
    for t in tx:
        x("INSERT INTO membership_transactions (id, receipt_no, member_id, plan_id, transaction_type, original_fee, amount_paid, "
          "discount, remaining_amount, payment_status, joining_date, expiry_date, payment_date, status, trainer_id, trainer_share, "
          "gym_share, payment_mode, reference_no, is_gst_applied, gst_percent, gst_amount, created_at) "
          "VALUES (?,?,?,?,'New',?,?,?,?,?,?,?,?, 'Active',?,?,?,?,'',?,18,?, '2026-09-01 10:00:00')",
          (t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], t[9], t[10], t[9], t[11], t[12], t[13], t[14], t[15], t[16]))
    x("INSERT INTO staff_attendance (staff_id, attendance_date, status) VALUES (1,'2026-10-01','Present')")
    x("INSERT INTO salary_records (id, staff_id, salary_month, basic_salary, calendar_days, earned_salary, advance_amount, "
      "deduction_amount, net_salary, status) VALUES (1,1,'2026-09',12000,30,12000,0,0,12000,'Paid')")
    x("INSERT INTO payroll_settlements (id, staff_id, salary_record_id, trainer_id, salary_month, salary_amount, pt_commission_amount, "
      "total_amount, payment_date, payment_mode, status) VALUES (1,1,1,1,'2026-09',12000,0,12000,'2026-10-01','Cash','Paid')")
    x("INSERT INTO expenses (expense_date, category, expense_title, amount, payment_mode, status) "
      "VALUES ('2026-10-01','Rent','October rent',30000,'Cash','Paid')")
    x("INSERT INTO enquiries (id, enquiry_code, enquiry_date, full_name, phone, source, status, priority, interested_plan_id, "
      "converted_member_id, is_archived) VALUES (1,'ENQ-000007','2026-10-01','Asha Rao','9876500001','Walk-in','Converted','Hot',1,1,0)")
    x("INSERT INTO diet_plans (id, member_id, name, goal, diet_type, status, current_version, source_type) "
      "VALUES (1,1,'Cut','Weight Loss','Vegetarian','Active',1,'scratch')")
    x("INSERT INTO diet_plan_versions (id, plan_id, version_no, name, instructions) VALUES (1,1,1,'Cut','Drink water')")
    x("INSERT INTO diet_meals (id, plan_version_id, name, meal_time, sort_order) VALUES (1,1,'Breakfast','08:00',0)")
    x("INSERT INTO diet_meal_items (meal_id, food_name, portion, sort_order) VALUES (1,'Vegetable poha','1 bowl (150 g)',0)")
    if extra:
        extra(db)
    db.commit()
    db.close()


class ImportTests(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "fitdesk.db")

    def run_import(self, *args, **kw):
        out = StringIO()
        call_command("import_v120", self.path, "--today", TODAY.isoformat(), *args, stdout=out, **kw)
        return out.getvalue()

    def test_full_import_matches_to_the_rupee(self):
        make_v120(self.path)
        out = self.run_import()
        self.assertIn("All checks match.", out)
        run = ImportRun.objects.get()
        gym = run.client
        self.assertEqual(GymSettings.objects.get(client=gym).gym_name, "Iron Den")
        self.assertEqual(Member.objects.filter(client=gym).count(), 4)
        bilal = Member.objects.get(client=gym, code="PPF000002")
        self.assertEqual(bilal.phone, "+919876500002")                     # trunk 0 fixed
        self.assertIsNone(bilal.date_of_birth)                             # 1 Jan 2000 vs age 28 → placeholder
        self.assertEqual(bilal.age, 28)
        b3 = Bill.objects.get(client=gym, receipt_no="RCPT00003")
        self.assertEqual((b3.total, b3.gst_amount, b3.trainer.name), (8260, 1260, "Vikram"))
        pay = Payment.objects.get(bill=b3)
        self.assertEqual((pay.amount, pay.trainer_share, pay.gym_share), (7000, 3500, 3500))
        self.assertEqual(Bill.objects.get(client=gym, receipt_no="RCPT00004").discount, 100)
        self.assertEqual(Staff.objects.get(client=gym).salary_basis, "Calendar Days")
        self.assertEqual(PayrollSettlement.objects.get(staff__client=gym).total, 12000)
        self.assertEqual(Expense.objects.get(client=gym).amount, 30000)
        e = Enquiry.objects.get(client=gym)
        self.assertEqual((e.code, e.converted_member.code, e.interested_plan.name), ("ENQ-000007", "PPF000001", "Monthly"))
        dp = DietPlan.objects.get(client=gym)
        self.assertEqual(dp.versions.get().meals[0]["items"][0]["food_name"], "Vegetable poha")
        self.assertIsNotNone(dp.versions.get().meals[0]["items"][0]["kcal"])   # values filled from the food list
        counters = dict(Counter.objects.filter(client=gym).values_list("key", "value"))
        self.assertEqual((counters["member"], counters["receipt"], counters["enquiry"]), (4, 4, 7))
        self.assertTrue(os.path.exists(os.path.join(self.dir, f"import_check_{TODAY:%Y-%m-%d}.html")))
        html = open(os.path.join(self.dir, f"import_check_{TODAY:%Y-%m-%d}.html"), encoding="utf-8").read()
        self.assertIn("Test Member looks like a test entry", html)
        self.assertIn("v1.2.0 said “Paid”, now “Partially Paid”", html)    # GST left unpaid in v1.2.0

    def test_source_file_is_never_changed(self):
        make_v120(self.path)
        before = open(self.path, "rb").read()
        self.run_import()
        self.assertEqual(open(self.path, "rb").read(), before)

    def test_new_numbers_continue_after_imported_ones(self):
        make_v120(self.path)
        self.run_import()
        gym = ImportRun.objects.get().client
        from gym_core.services import next_number
        self.assertEqual(next_number(gym, "member"), 5)
        self.assertEqual(next_number(gym, "receipt"), 5)

    def test_rerun_needs_replace_and_replace_starts_fresh(self):
        make_v120(self.path)
        self.run_import()
        with self.assertRaises(CommandError):
            self.run_import()
        first = ImportRun.objects.get().client_id
        self.run_import("--replace")
        self.assertEqual(Member.objects.filter(client_id=first).count(), 0)
        self.assertEqual(ImportRun.objects.filter(ok=True).exclude(client=None).count(), 1)
        self.assertEqual(Member.objects.filter(client=ImportRun.objects.first().client).count(), 4)

    def test_dry_run_keeps_nothing(self):
        make_v120(self.path)
        out = self.run_import("--dry-run")
        self.assertIn("DRY RUN", out)
        self.assertEqual(Member.objects.count(), 0)
        self.assertEqual(ImportRun.objects.count(), 0)

    def test_duplicate_phone_stops_with_nothing_saved(self):
        make_v120(self.path, extra=lambda db: db.execute(
            "INSERT INTO members (id, member_code, full_name, phone, gender, age, status, is_archived) "
            "VALUES (9,'PPF000009','Asha R','+919876500001','Female',30,'Active',0)"))
        with self.assertRaisesMessage(CommandError, "same mobile number"):
            self.run_import()
        self.assertEqual(Member.objects.count(), 0)

    def test_check_catches_a_wrong_amount(self):
        """The check is real: if a payment came across wrong, it says so."""
        make_v120(self.path)
        src = Source(self.path)
        imp = Importer(src, today=TODAY)
        imp.run()
        Payment.objects.filter(bill__receipt_no="RCPT00002", bill__client=imp.client).update(amount=1999)
        check = reconcile(src, imp, TODAY)
        bad = {r[1] for r in check.problems}
        self.assertIn("Total collected", bad)
        self.assertIn("Dues still to collect", bad)
        self.assertTrue(any(i.startswith("RCPT00002") for i in bad))
        self.assertTrue(any(i.startswith("PPF000002") for i in bad))
        src.close()

    def test_not_a_fitdesk_file(self):
        sqlite3.connect(self.path).execute("CREATE TABLE x (id int)").connection.commit()
        with self.assertRaisesMessage(CommandError, "doesn't look like a FitDesk v1.2.0 database"):
            self.run_import()

    def test_not_visiting_counts_from_import_day(self):
        make_v120(self.path)
        self.run_import()
        gym = ImportRun.objects.get().client
        from gym_auto.models import Reminder
        from gym_auto.services import generate
        from gym_core.services import today_for
        t = today_for(gym)
        generate(gym, t)
        self.assertFalse(Reminder.objects.filter(client=gym, kind="inactive").exists())
        generate(gym, t + timedelta(days=8))
        self.assertTrue(Reminder.objects.filter(client=gym, kind="inactive").exists() or
                        not Member.objects.filter(client=gym, memberships__end_date__gte=t + timedelta(days=8)).exists())
