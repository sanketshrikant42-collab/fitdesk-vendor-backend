"""Phase 3B tests: payroll parity with v1.2.0, staff/attendance/expenses
permissions, enquiries + referral rewards, diet plans, progress, consent,
support, and gym isolation."""

import json
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
import tempfile

from gym_core.models import GymSettings, Membership
from gym_core.tests import Base
from licensing.models import SupportQuery

from .models import Expense, ReferralReward, Staff
from .rules import payroll

OCT = "2026-10"     # 31 days; Sundays 4, 11, 18, 25


def att(spec):
    """{'Present': [1,2,..], ...} -> {date: status}"""
    out = {}
    for status, days in spec.items():
        for d in days:
            out[date(2026, 10, d)] = status
    return out


class PayrollRuleMatchesV120(SimpleTestCase):
    WORKDAYS = [d for d in range(1, 32) if date(2026, 10, d).weekday() != 6]   # 27 non-Sundays

    def test_full_past_month(self):
        a = att({"Present": self.WORKDAYS[:20], "Half Day": self.WORKDAYS[20:22], "Paid Leave": self.WORKDAYS[22:23],
                 "Absent": self.WORKDAYS[23:25]})
        r = payroll("31000", "Calendar Days", OCT, date(2026, 11, 5), date(2025, 1, 1), a, advance=2000, deduction=500,
                    pt_earned=3000, pt_paid=1000)
        self.assertEqual(r["weekly_off_inferred"], 4)            # every unmarked Sunday is paid
        self.assertEqual(r["paid_days"], 26)                     # 20 + 1 + 1 + 4
        self.assertEqual((r["earned"], r["net"], r["pt_pending"], r["total"]), (26000, 23500, 2000.0, 25500))

    def test_fixed_30_days(self):
        a = att({"Present": self.WORKDAYS[:22]})
        r = payroll("30000", "Fixed 30 Days", OCT, date(2026, 11, 5), date(2025, 1, 1), a)
        self.assertEqual((r["divisor"], r["earned"]), (30, 26000))   # 22 + 4 Sundays at 1000/day

    def test_month_in_progress_counts_only_elapsed_sundays(self):
        r = payroll("31000", "Calendar Days", OCT, date(2026, 10, 15), date(2025, 1, 1), att({"Present": [1, 2, 3]}))
        self.assertEqual((r["elapsed_days"], r["weekly_off_inferred"], r["earned"]), (15, 2, 5000))

    def test_mid_month_joiner(self):
        r = payroll("31000", "Calendar Days", OCT, date(2026, 11, 2), date(2026, 10, 15), att({"Present": [15, 16]}))
        self.assertEqual(r["weekly_off_inferred"], 2)            # only Sundays 18 and 25
        r = payroll("31000", "Calendar Days", OCT, date(2026, 11, 2), date(2026, 12, 1), {})
        self.assertEqual(r["earned"], 0)                         # joined after this month

    def test_future_month_and_marked_sunday(self):
        self.assertEqual(payroll("31000", "Calendar Days", "2026-12", date(2026, 10, 27), None, {})["earned"], 0)
        r = payroll("31000", "Calendar Days", OCT, date(2026, 11, 5), None, att({"Absent": [4]}))
        self.assertEqual(r["weekly_off_inferred"], 3)            # an explicitly marked Sunday isn't inferred

    def test_holidays_v120_unpaid_unless_switched_on(self):
        a = att({"Holiday": [2]})
        self.assertEqual(payroll("31000", "Calendar Days", OCT, date(2026, 11, 5), None, a)["paid_days"], 4)
        self.assertEqual(payroll("31000", "Calendar Days", OCT, date(2026, 11, 5), None, a, holidays_paid=True)["paid_days"], 5)

    def test_whole_rupee_rounding_and_floor(self):
        r = payroll("10000", "Calendar Days", OCT, date(2026, 11, 5), None, att({"Present": self.WORKDAYS[:22]}))
        self.assertEqual(r["earned"], 8387)                      # 10000/31*26 = 8387.09
        self.assertEqual(payroll("10000", "Calendar Days", OCT, date(2026, 11, 5), None, {}, advance=99999)["net"], 0)


class StaffPayrollApi(Base):
    def setUp(self):
        super().setUp()
        s, b = self.req("POST", "staff/", self.owner, {"name": "Priya Sharma", "phone": "9100000001", "designation": "Receptionist",
                                                       "department": "Front Desk", "joining_date": "2025-01-01",
                                                       "monthly_salary": "31000"})
        self.assertEqual(s, 201, b)
        self.priya = b["staff"]

    def test_codes_and_trainer_department_auto_enrol(self):
        self.assertEqual(self.priya["code"], "PPF-S001")                 # staff codes differ from members (PPF000001)
        s, b = self.req("POST", "staff/", self.owner, {"name": "Sana Khan", "phone": "9100000002", "designation": "Trainer",
                                                       "department": "Trainer", "monthly_salary": "20000"})
        self.assertEqual(b["staff"]["code"], "PPF-S002")
        self.assertIsNotNone(b["staff"]["trainer"])              # v1.2.0: Trainer department -> trainer record
        t = self.req("GET", "trainers/", self.owner)[1]["trainers"]
        self.assertIn("PPF-T002", [x["code"] for x in t])         # trainer codes carry the gym prefix too

    def test_salary_visibility(self):
        s, b = self.req("GET", "staff/", self.mgr)
        self.assertEqual(s, 200)
        self.assertNotIn("monthly_salary", b["staff"][0])
        self.assertEqual(self.req("GET", f"staff/{self.priya['id']}/payroll/", self.mgr)[0], 403)
        # The owner chooses per person (28 Oct 2026): "view" lets this manager see salaries...
        s, b = self.req("POST", f"team/users/{self.mgr_u.id}/salary-access/", self.owner, {"access": "view"})
        self.assertEqual((s, b["user"]["salary_access"]), (200, "view"))
        self.assertIn("monthly_salary", self.req("GET", "staff/", self.mgr)[1]["staff"][0])
        self.assertEqual(self.req("GET", f"staff/{self.priya['id']}/payroll/", self.mgr)[0], 200)
        self.assertEqual(self.req("GET", "staff/", self.rec)[0], 403)              # receptionists never see staff
        # ...but not change or pay them
        self.assertEqual(self.req("POST", f"staff/{self.priya['id']}/", self.mgr, {"monthly_salary": "50000"})[0], 403)
        self.assertEqual(self.req("POST", f"staff/{self.priya['id']}/advances/", self.mgr, {"amount": "500"})[0], 403)
        # "manage" also lets them set salaries and give advances
        self.req("POST", f"team/users/{self.mgr_u.id}/salary-access/", self.owner, {"access": "manage"})
        self.assertEqual(self.req("POST", f"staff/{self.priya['id']}/", self.mgr, {"monthly_salary": "32000"})[0], 200)
        self.assertEqual(self.req("POST", f"staff/{self.priya['id']}/advances/", self.mgr, {"amount": "500"})[0], 201)
        # only the owner can hand out salary access
        self.assertEqual(self.req("POST", f"team/users/{self.rec_u.id}/salary-access/", self.mgr, {"access": "view"})[0], 403)
        self.req("POST", f"team/users/{self.mgr_u.id}/salary-access/", self.owner, {"access": "none"})
        self.assertNotIn("monthly_salary", self.req("GET", "staff/", self.mgr)[1]["staff"][0])

    def test_attendance_marking_permissions(self):
        today = date.today()
        rows = [{"staff_id": self.priya["id"], "date": today.isoformat(), "status": "Present", "check_in": "06:00"}]
        self.assertEqual(self.req("POST", "staff-attendance/", self.rec, {"rows": rows})[0], 403)
        s, b = self.req("POST", "staff-attendance/", self.mgr, {"rows": rows})
        self.assertEqual((s, b["saved"]), (200, 1))
        s, b = self.req("POST", "staff-attendance/", self.mgr, {"rows": [{**rows[0], "date": (today + timedelta(days=2)).isoformat()}]})
        self.assertEqual((s, b["error"]), (400, "future_date"))
        s, b = self.req("GET", "staff-attendance/", self.mgr, {"date": today.isoformat()})
        self.assertEqual(b["rows"][0]["status"], "Present")

    def test_pay_salary_with_pt_commission_once(self):
        s, b = self.req("POST", "staff/", self.owner, {"name": "Rahul Verma", "phone": "9100000003", "designation": "Trainer",
                                                       "joining_date": "2025-01-01", "monthly_salary": "31000",
                                                       "trainer_id": self.trainer})
        rahul = b["staff"]
        s, p = self.req("POST", "plans/", self.owner, {"name": "PT", "duration_days": 30, "fee": "5000",
                                                       "is_personal_training": True, "trainer_share_percent": "40"})
        m = self.member()
        self.bill(self.rec, m["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="5000")
        month = date.today().strftime("%Y-%m")
        s, calc = self.req("GET", f"staff/{rahul['id']}/payroll/", self.owner, {"month": month})
        self.assertEqual(calc["payroll"]["pt_earned"], 2000.0)
        self.req("POST", f"staff/{rahul['id']}/advances/", self.owner, {"amount": "1000"})
        s, b = self.req("POST", f"staff/{rahul['id']}/payroll/pay/", self.owner, {"month": month, "mode": "UPI"})
        self.assertEqual((s, b["error"]), (400, "reference_required"))       # v1.2.0 rule
        s, b = self.req("POST", f"staff/{rahul['id']}/payroll/pay/", self.owner, {"month": month, "mode": "UPI", "reference_no": "UTR1"})
        self.assertEqual(s, 200, b)
        self.assertEqual(b["settlement"]["pt_commission"], "2000.00")
        s, b = self.req("POST", f"staff/{rahul['id']}/payroll/pay/", self.owner, {"month": month, "mode": "Cash"})
        self.assertEqual((s, b["error"]), (409, "already_settled"))
        s, calc = self.req("GET", f"staff/{rahul['id']}/payroll/", self.owner, {"month": month})
        self.assertEqual((calc["payroll"]["pt_paid"], calc["payroll"]["pt_pending"]), (2000.0, 0))
        self.assertIsNotNone(calc["payroll"]["settled"])


class ExpenseApi(Base):
    def test_roles_and_totals(self):
        self.assertEqual(self.req("GET", "expenses/", self.rec)[0], 403)
        self.assertEqual(self.req("GET", "expenses/", self.tr)[0], 403)
        s, b = self.req("POST", "expenses/", self.mgr, {"category": "Rent", "title": "October rent", "amount": "45000", "mode": "Bank Transfer"})
        self.assertEqual(s, 201, b)
        self.req("POST", "expenses/", self.mgr, {"category": "Electricity", "title": "Bill", "amount": "8200", "status": "Pending"})
        s, b = self.req("POST", "expenses/", self.mgr, {"category": "Snacks", "title": "x", "amount": "1"})
        self.assertEqual((s, b["error"]), (400, "bad_category"))
        s, b = self.req("GET", "expenses/", self.mgr)
        self.assertEqual(b["totals"], {"total": "53200.00", "paid": "45000.00", "pending": "8200.00", "count": 2})
        eid = b["expenses"][0]["id"]
        self.assertEqual(self.req("POST", f"expenses/{eid}/delete/", self.mgr)[0], 403)   # delete: owner only
        self.assertEqual(self.req("POST", f"expenses/{eid}/delete/", self.owner)[0], 200)
        self.assertEqual(self.req("GET", "expenses/", self.owner)[1]["totals"]["count"], 1)


class EnquiryApi(Base):
    def test_pipeline_convert_and_referral_reward(self):
        GymSettings.objects.filter(client=self.gym).update(referral_reward_days=15)
        referrer = self.member(name="Rohit Kulkarni")
        today = date.today()
        self.bill(self.rec, referrer["id"], self.monthly, start_date=today.isoformat(), amount_paid="1500")
        before = Membership.objects.get(member_id=referrer["id"]).end_date
        s, b = self.req("POST", "enquiries/", self.rec, {"name": "Neha Patil", "phone": "9700000001",
                                                         "referred_by_member_id": referrer["id"]})
        self.assertEqual(s, 201, b)
        e = b["enquiry"]
        self.assertEqual((e["code"], e["status"], e["source"]), ("ENQ-000001", "New", "Referral"))
        self.assertIn("wa.me/919700000001", e["whatsapp"]["link"])
        s, b = self.req("POST", f"enquiries/{e['id']}/followup/", self.rec, {"mode": "Call", "notes": "asked timings",
                                                                             "next_follow_up_date": (today + timedelta(days=2)).isoformat()})
        self.assertEqual(b["enquiry"]["status"], "Contacted")                   # v1.2.0: New -> Contacted
        s, b = self.req("POST", f"enquiries/{e['id']}/convert/", self.rec, {"gender": "Female"})
        self.assertEqual(s, 200, b)
        self.assertEqual((b["enquiry"]["status"], b["referral_reward"]), ("Converted", {"days": 15, "status": "applied"}))
        self.assertEqual(Membership.objects.get(member_id=referrer["id"]).end_date, before + timedelta(days=15))
        s, b = self.req("POST", f"enquiries/{e['id']}/followup/", self.rec, {"mode": "Call"})
        self.assertEqual((s, b["error"]), (400, "enquiry_closed"))
        s, refs = self.req("GET", f"members/{referrer['id']}/referrals/", self.rec)
        self.assertEqual(refs["referrals"][0]["reward"]["days"], 15)

    def test_reward_pending_without_running_membership(self):
        GymSettings.objects.filter(client=self.gym).update(referral_reward_days=10)
        referrer = self.member(name="No plan")
        s, b = self.req("POST", "enquiries/", self.rec, {"name": "A", "phone": "9700000002", "referred_by_member_id": referrer["id"]})
        s, b = self.req("POST", f"enquiries/{b['enquiry']['id']}/convert/", self.rec, {})
        self.assertEqual(b["referral_reward"]["status"], "pending")
        self.assertEqual(ReferralReward.objects.get().status, "pending")

    def test_lost_needs_reason_and_trainer_has_no_access(self):
        s, b = self.req("POST", "enquiries/", self.rec, {"name": "B", "phone": "9700000003"})
        eid = b["enquiry"]["id"]
        self.assertEqual(self.req("POST", f"enquiries/{eid}/lost/", self.rec, {})[1]["error"], "bad_reason")
        s, b = self.req("POST", f"enquiries/{eid}/lost/", self.rec, {"reason": "Too far"})
        self.assertEqual(b["enquiry"]["status"], "Lost")
        self.assertEqual(self.req("POST", f"enquiries/{eid}/reopen/", self.rec, {})[1]["enquiry"]["status"], "Follow-up")
        self.assertEqual(self.req("GET", "enquiries/", self.tr)[0], 403)
        s, st = self.req("GET", "enquiries/", self.rec)
        self.assertEqual(st["stats"]["open"], 1)


class DietApi(Base):
    def _pt_client(self):
        s, p = self.req("POST", "plans/", self.owner, {"name": "PT", "duration_days": 30, "fee": "5000",
                                                       "is_personal_training": True, "trainer_share_percent": "40"})
        mine, other = self.member(name="Mine"), self.member(name="Other")
        self.bill(self.rec, mine["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="5000")
        return mine, other

    def test_starter_assign_revise_and_trainer_scope(self):
        mine, other = self._pt_client()
        s, st = self.req("GET", "diet/starters/", self.tr, {"goal": "Weight Loss", "diet_type": "Vegetarian"})
        key = st["starters"][0]["key"]
        s, b = self.req("POST", "diet/plans/", self.tr, {"member_id": other["id"], "starter_key": key})
        self.assertEqual(s, 404)                                                  # not his client
        s, b = self.req("POST", "diet/plans/", self.tr, {"member_id": mine["id"], "starter_key": key})
        self.assertEqual(s, 201, b)
        plan = b["plan"]
        self.assertEqual((plan["status"], plan["current_version"], plan["source_type"]), ("Active", 1, "starter"))
        self.assertTrue(plan["version"]["meals"])
        self.assertEqual(plan["trainer"]["name"], "Rahul Verma")
        meals = plan["version"]["meals"][:1]
        s, b = self.req("POST", f"diet/plans/{plan['id']}/", self.tr, {"name": plan["name"], "meals": meals, "change_note": "lighter"})
        self.assertEqual((b["plan"]["current_version"], b["plan"]["versions"]), (2, [1, 2]))
        s, b = self.req("GET", f"diet/plans/{plan['id']}/", self.tr, {"version": "1"})
        self.assertGreater(len(b["plan"]["version"]["meals"]), 1)                 # history kept
        s, b = self.req("POST", f"diet/plans/{plan['id']}/", self.tr, {"status": "Draft"})
        self.assertEqual((s, b["error"]), (400, "bad_transition"))
        s, b = self.req("POST", f"diet/plans/{plan['id']}/share/", self.tr)
        self.assertIn("wa.me/", b["whatsapp"]["link"])
        self.assertEqual(self.req("GET", "diet/plans/", self.tr)[1]["pagination"]["total"], 1)

    def test_food_library_and_nutrition_snapshot(self):
        self.assertEqual(self.req("POST", "diet/foods/", self.tr, {"name": "Oats"})[0], 403)   # trainers can't edit library
        s, b = self.req("POST", "diet/foods/", self.mgr, {"name": "Oats", "serving_qty": 100, "serving_unit": "g", "kcal": 389,
                                                          "protein_g": 17, "carbs_g": 66, "fat_g": 7})
        self.assertEqual(s, 201, b)
        m = self.member()
        meals = [{"name": "Breakfast", "time": "08:00", "items": [{"food_name": "Oats", "portion": "50 g"}]}]
        s, c = self.req("POST", "diet/calculate/", self.mgr, {"meals": meals})
        self.assertEqual(c["nutrition"]["day"]["kcal"], 194.5)                    # same maths as v1.2.0
        s, b = self.req("POST", "diet/plans/", self.mgr, {"member_id": m["id"], "name": "Plan A", "meals": meals})
        pid = b["plan"]["id"]
        self.assertEqual(b["plan"]["status"], "Draft")
        self.req("POST", "diet/foods/", self.mgr, {"id": 1, "name": "Oats", "kcal": 999})
        s, b = self.req("GET", f"diet/plans/{pid}/", self.mgr)
        self.assertEqual(b["plan"]["nutrition"]["day"]["kcal"], 194.5)            # saved plan doesn't change

    def test_template_from_starter_then_assign(self):
        m = self.member()
        st = self.req("GET", "diet/starters/", self.mgr)[1]["starters"][3]["key"]
        s, t = self.req("POST", "diet/templates/", self.mgr, {"from_starter": st, "name": "Our WL plan"})
        s, b = self.req("POST", "diet/plans/", self.mgr, {"member_id": m["id"], "template_id": t["template"]["id"]})
        self.assertEqual((s, b["plan"]["source_type"], b["plan"]["name"]), (201, "template", "Our WL plan"))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProgressConsentSupportApi(Base):
    def test_progress_updates_weight_and_photo(self):
        m = self.member(fitness={"current_weight_kg": "80", "target_weight_kg": "70"})
        s, b = self.req("POST", f"members/{m['id']}/progress/", self.rec, {"weight_kg": "78.4", "waist_cm": "90"})
        self.assertEqual(s, 201, b)
        self.assertEqual(self.req("POST", f"members/{m['id']}/progress/", self.rec, {})[1]["error"], "empty_entry")
        photo = SimpleUploadedFile("p.jpg", b"\xff\xd8\xff" + b"0" * 100, content_type="image/jpeg")
        r = self.client.post(f"/api/v1/members/{m['id']}/progress/", {"weight_kg": "77.9", "photo": photo},
                             HTTP_AUTHORIZATION=f"Bearer {self.rec}")
        self.assertEqual(r.status_code, 201, r.content)
        eid = r.json()["entry"]["id"]
        r = self.client.get(f"/api/v1/progress/{eid}/photo/", HTTP_AUTHORIZATION=f"Bearer {self.rec}")
        self.assertEqual(r.status_code, 200)
        s, b = self.req("GET", f"members/{m['id']}/progress/", self.rec)
        self.assertEqual([x["weight_kg"] for x in b["weight_series"]], ["78.4", "77.9"])
        self.assertEqual(self.req("GET", f"members/{m['id']}/", self.rec)[1]["member"]["fitness"]["current_weight_kg"], "77.9")
        other = self.make_gym("Other")
        _, otok = self.user(other, "owner")
        r = self.client.get(f"/api/v1/progress/{eid}/photo/", HTTP_AUTHORIZATION=f"Bearer {otok}")
        self.assertEqual(r.status_code, 404)

    @override_settings(MEDIA_EPHEMERAL=True)
    def test_photo_refused_where_storage_would_be_wiped(self):
        m = self.member()
        photo = SimpleUploadedFile("p.jpg", b"\xff\xd8\xff" + b"0" * 100, content_type="image/jpeg")
        r = self.client.post(f"/api/v1/members/{m['id']}/progress/", {"weight_kg": "77.9", "photo": photo},
                             HTTP_AUTHORIZATION=f"Bearer {self.rec}")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "photos_not_set_up"))

    def test_consent_and_support(self):
        m = self.member()
        s, b = self.req("POST", f"members/{m['id']}/consent/", self.rec, {"signed_name": "Rohit", "signature_png": "nope"})
        self.assertEqual((s, b["error"]), (400, "bad_signature"))
        s, b = self.req("POST", f"members/{m['id']}/consent/", self.rec, {"signed_name": "Rohit K",
                        "signature_png": "data:image/png;base64,iVBORw0KGgo=", "health_answers": {"heart_condition": False}})
        self.assertEqual(s, 201, b)
        s, b = self.req("GET", f"members/{m['id']}/consent/", self.rec)
        self.assertEqual(b["signed"][0]["signed_name"], "Rohit K")
        s, b = self.req("POST", "support/", self.rec, {"subject": "Printer", "message": "Receipt not printing", "category": "Technical Issue"})
        self.assertEqual(s, 201, b)
        q = SupportQuery.objects.get()
        self.assertEqual(q.client, self.gym)
        self.assertIn("Receptionist", q.message.title())


class IsolationOps(Base):
    def test_other_gym_sees_nothing(self):
        s, st = self.req("POST", "staff/", self.owner, {"name": "S", "phone": "9100000009", "designation": "Cleaner"})
        s, ex = self.req("POST", "expenses/", self.owner, {"category": "Rent", "title": "R", "amount": "10"})
        s, en = self.req("POST", "enquiries/", self.owner, {"name": "E", "phone": "9700000009"})
        m = self.member()
        s, dp = self.req("POST", "diet/plans/", self.owner, {"member_id": m["id"], "name": "P", "meals": []})
        other = self.make_gym("Other Gym")
        _, o = self.user(other, "owner")
        self.assertEqual(self.req("GET", f"staff/{st['staff']['id']}/", o)[0], 404)
        self.assertEqual(self.req("GET", f"staff/{st['staff']['id']}/payroll/", o)[0], 404)
        self.assertEqual(self.req("POST", f"expenses/{ex['expense']['id']}/update/", o, {"amount": "1"})[0], 404)
        self.assertEqual(self.req("GET", f"enquiries/{en['enquiry']['id']}/", o)[0], 404)
        self.assertEqual(self.req("GET", f"diet/plans/{dp['plan']['id']}/", o)[0], 404)
        self.assertEqual(self.req("GET", f"members/{m['id']}/progress/", o)[0], 404)
        self.assertEqual(self.req("POST", "staff-attendance/", o, {"rows": [{"staff_id": st["staff"]["id"], "status": "Present"}]})[0], 404)
        self.assertEqual(self.req("GET", "staff/", o)[1]["staff"], [])
        self.assertEqual(self.req("GET", "expenses/", o)[1]["expenses"], [])
        self.assertEqual(self.req("GET", "enquiries/", o)[1]["enquiries"], [])
        self.assertEqual(self.req("GET", "diet/plans/", o)[1]["plans"], [])

class StaffProfileApi(Base):
    def test_month_attendance_and_payroll_on_profile(self):
        s, b = self.req("POST", "staff/", self.owner, {"name": "Priya Sharma", "phone": "9100000001", "designation": "Receptionist",
                                                       "joining_date": "2025-01-01", "monthly_salary": "31000"})
        sid = b["staff"]["id"]
        self.req("POST", "staff-attendance/", self.owner, {"rows": [
            {"staff_id": sid, "date": "2026-05-04", "status": "Present", "check_in": "09:00", "check_out": "18:00"},
            {"staff_id": sid, "date": "2026-05-05", "status": "Half Day"}]})
        s, b = self.req("GET", f"staff/{sid}/", self.owner, {"month": "2026-05"})
        self.assertEqual(s, 200, b)
        a = b["staff"]["attendance"]
        self.assertEqual((len(a["days"]), a["marked"], a["counts"]["Present"], a["counts"]["Half Day"]), (31, 2, 1, 1))
        self.assertEqual(a["days"][3]["check_in"], "09:00")
        self.assertEqual(b["staff"]["payroll"]["month"], "2026-05")
        s, b = self.req("GET", f"staff/{sid}/", self.mgr, {"month": "2026-05"})        # manager: attendance yes, payroll no
        self.assertEqual(s, 200)
        self.assertIn("attendance", b["staff"])
        self.assertNotIn("payroll", b["staff"])
        self.assertEqual(self.req("GET", f"staff/{sid}/", self.owner, {"month": "2026-13"})[0], 400)

class StaffTrainerSync(Base):
    """Bug seen in testing: a failed "Add staff" (mobile already used) still
    created a trainer, so a receptionist showed up under Trainers."""

    def staff(self, **kw):
        body = {"name": "Rohit", "phone": "8421819132", "designation": "Receptionist", "department": "Front Desk",
                "joining_date": "2025-01-01"}
        body.update(kw)
        return self.req("POST", "staff/", self.owner, body)

    def trainer_names(self, status="active"):
        return [t["name"] for t in self.req("GET", "trainers/", self.owner, {"status": status})[1]["trainers"]]

    def test_failed_staff_save_leaves_no_trainer(self):
        self.assertEqual(self.staff()[0], 201)
        s, b = self.staff(name="Rohit", designation="Trainer", department="Trainer")   # same mobile
        self.assertEqual((s, b["error"]), (409, "phone_taken"))
        self.assertNotIn("Rohit", self.trainer_names(""))

    def test_department_change_keeps_trainers_in_step(self):
        s, b = self.staff(name="Yash", phone="9028644654", designation="General Trainer", department="Trainer")
        sid = b["staff"]["id"]
        self.assertIn("Yash", self.trainer_names())
        self.req("POST", f"staff/{sid}/", self.owner, {"department": "Front Desk", "designation": "Receptionist"})
        self.assertNotIn("Yash", self.trainer_names())
        self.assertIn("Yash", self.trainer_names("inactive"))
        self.req("POST", f"staff/{sid}/", self.owner, {"department": "Trainer"})
        self.assertIn("Yash", self.trainer_names())
        self.req("POST", f"staff/{sid}/", self.owner, {"status": "inactive"})                 # left the gym
        self.assertNotIn("Yash", self.trainer_names())

    def test_trainer_listing_shows_staff_link(self):
        self.staff(name="Yash", phone="9028644654", designation="General Trainer", department="Trainer")
        t = [t for t in self.req("GET", "trainers/", self.owner)[1]["trainers"] if t["name"] == "Yash"][0]
        self.assertEqual(t["staff"]["department"], "Trainer")

    def test_remove_trainer(self):
        s, b = self.req("POST", "trainers/", self.owner, {"name": "Freelance Fiona", "phone": "9000000099"})
        tid = b["trainer"]["id"]
        self.assertEqual(self.req("POST", f"trainers/{tid}/remove/", self.mgr, {})[0], 403)   # owner only
        s, b = self.req("POST", f"trainers/{tid}/remove/", self.owner, {})
        self.assertEqual((s, b["result"]), (200, "deleted"))
        # a trainer with a bill is only marked inactive
        s, p = self.req("POST", "plans/", self.owner, {"name": "PT", "duration_days": 30, "fee": "1000",
                                                       "is_personal_training": True, "trainer_share_percent": "40"})
        m = self.member()
        self.bill(self.rec, m["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="1000")
        s, b = self.req("POST", f"trainers/{self.trainer}/remove/", self.owner, {})
        self.assertEqual(b["result"], "inactive")
        s, b = self.bill(self.rec, m["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="1000")
        self.assertEqual((s, b["error"]), (400, "trainer_inactive"))

    def test_fix_orphan_trainers_command(self):
        from io import StringIO
        from django.core.management import call_command
        from gym_core.models import Trainer
        self.staff()
        Trainer.objects.create(client=self.gym, code="TRN-099", name="Rohit", phone="+918421819132")   # the stray one
        out = StringIO()
        call_command("fix_orphan_trainers", stdout=out)
        self.assertIn("TRN-099", out.getvalue())
        self.assertTrue(Trainer.objects.filter(code="TRN-099").exists())                 # dry run keeps it
        call_command("fix_orphan_trainers", "--apply", stdout=StringIO())
        self.assertFalse(Trainer.objects.filter(code="TRN-099").exists())
        self.assertTrue(Trainer.objects.filter(pk=self.trainer).exists())                # the real trainer stays


class SalaryAccessOnInvite(Base):
    def test_invite_carries_salary_access(self):
        from gym_accounts.models import Invite
        s, b = self.req("POST", "team/invites/", self.owner, {"name": "Meena", "phone": "9123456780", "role": "manager",
                                                              "salary_access": "view"})
        self.assertEqual((s, b["invite"]["salary_access"]), (201, "view"))
        dev = {"installation_id": "inst-meena-01", "device_secret": "s" * 40, "name": "Meena phone", "platform": "android"}
        s, j = self.client.post("/api/v1/auth/join/", {"invite_code": b["invite_code"], "password": "Strong-pass-2026",
                                                       "device": dev}, content_type="application/json").status_code, None
        self.assertEqual(s, 201)
        from gym_accounts.models import StaffUser
        from gym_accounts.permissions import salary_access
        self.assertEqual(salary_access(StaffUser.objects.get(phone__endswith="9123456780")), "view")
        s, b = self.req("POST", "team/invites/", self.owner, {"name": "X", "phone": "9123456781", "role": "trainer",
                                                              "salary_access": "everything"})
        self.assertEqual((s, b["error"]), (400, "bad_salary_access"))

    def test_payroll_flags_unmarked_days(self):
        from gym_ops.rules import payroll
        r = payroll("30000", "Fixed 30 Days", "2026-10", date(2026, 10, 7), date(2026, 1, 1),
                    {date(2026, 10, 1): "Present", date(2026, 10, 2): "Present"})
        # 1-7 Oct 2026: Thu..Wed. Marked 1, 2. Sunday 4th inferred. Not marked: 3, 5, 6, 7.
        self.assertEqual((r["weekly_off_inferred"], r["not_marked"], r["paid_days"]), (1, 4, 3))


class BuiltinFoodsAndManualValues(Base):
    def test_every_starter_plan_is_fully_counted_from_the_builtin_list(self):
        from gym_ops import diet_starter_plans as SP
        m = self.member()
        for sp in SP.list_starter_plans(None, None):
            s, b = self.req("POST", "diet/plans/", self.owner, {"member_id": m["id"], "starter_key": sp["key"]})
            self.assertEqual(s, 201, b)
            n = b["plan"]["nutrition"]
            self.assertTrue(n["complete"], (sp["key"], n["counted"], n["items"]))
            self.assertGreater(n["day"]["kcal"], 800)

    def test_name_matching_manual_values_and_gym_override(self):
        meals = [{"name": "Breakfast", "items": [
            {"food_name": "kanda POHA", "quantity": 150, "unit": "g"},                  # alias, any case
            {"food_name": "Grandma's laddoo", "quantity": 1, "unit": "pc"},             # not in any list
            {"food_name": "Protein bar", "manual": True, "kcal": "200", "protein_g": "20", "carbs_g": "20", "fat_g": "6"}]}]
        s, b = self.req("POST", "diet/calculate/", self.owner, {"meals": meals})
        self.assertEqual(s, 200, b)
        rows = b["nutrition"]["meals"][0]["items"]
        self.assertEqual((rows[0]["calc"]["status"], rows[0]["calc"]["kcal"], rows[0]["food"]["builtin"]), ("ok", 195.0, True))
        self.assertEqual(rows[1]["calc"]["status"], "no_food")
        self.assertEqual((rows[2]["calc"]["status"], rows[2]["calc"]["protein_g"]), ("ok", 20.0))
        self.assertEqual(b["nutrition"]["day"]["kcal"], 395.0)
        # the gym's own poha replaces the built-in one
        s, f = self.req("POST", "diet/foods/", self.owner, {"name": "Vegetable poha", "serving_qty": 100, "serving_unit": "g",
                                                            "kcal": 160, "protein_g": 3, "carbs_g": 26, "fat_g": 5, "allergens": "P"})
        self.assertEqual(s, 201, f)
        s, b = self.req("POST", "diet/calculate/", self.owner, {"meals": meals})
        self.assertEqual(b["nutrition"]["meals"][0]["items"][0]["calc"]["kcal"], 240.0)
        foods = self.req("GET", "diet/foods/", self.owner, {"builtin": "1"})[1]
        self.assertEqual([x["name"] for x in foods["foods"]].count("Vegetable poha"), 1)
        self.assertIn("D", foods["allergen_tags"])
        # typed values are saved with the plan
        m = self.member()
        s, p = self.req("POST", "diet/plans/", self.owner, {"member_id": m["id"], "name": "Custom", "meals": meals, "status": "Active"})
        self.assertEqual(s, 201, p)
        item = p["plan"]["version"]["meals"][0]["items"][2]
        self.assertEqual((item["manual"], item["kcal"]), (True, 200.0))
        bad = [{"name": "X", "items": [{"food_name": "Y", "manual": True, "kcal": "lots"}]}]
        self.assertEqual(self.req("POST", "diet/calculate/", self.owner, {"meals": bad})[1]["error"], "bad_meals")
