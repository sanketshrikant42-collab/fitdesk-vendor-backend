"""Phase 3A tests: v1.2.0 rule parity, billing, freeze, PT packs, family
plans, check-in, role permissions and gym isolation."""

import json
import secrets
from datetime import date, timedelta
from decimal import Decimal as D
from unittest import mock

from django.test import SimpleTestCase, TestCase

from gym_accounts.models import GymProfile, StaffDevice, StaffSession, StaffUser
from licensing.models import Client, License

from . import rules as R
from .models import Bill, GymSettings, Member, Membership, Payment, Plan, Trainer


class RulesMatchV120(SimpleTestCase):
    """Examples taken from v1.2.0's own code and comments."""

    def test_expiry_calendar_months(self):
        self.assertEqual(R.expiry_date(date(2026, 8, 21), 30, "Monthly"), date(2026, 9, 21))   # v1.2.0 comment
        self.assertEqual(R.expiry_date(date(2026, 1, 31), 30, "Monthly"), date(2026, 2, 28))   # clamped
        self.assertEqual(R.expiry_date(date(2028, 1, 31), 31, "x"), date(2028, 2, 29))
        self.assertEqual(R.expiry_date(date(2026, 10, 27), 90, "Quarterly"), date(2027, 1, 27))
        self.assertEqual(R.expiry_date(date(2026, 10, 27), 180, "Half Yearly"), date(2027, 4, 27))
        self.assertEqual(R.expiry_date(date(2026, 10, 27), 365, "Annual"), date(2027, 10, 27))
        self.assertEqual(R.expiry_date(date(2026, 10, 27), 28, "Monthly special"), date(2026, 11, 27))  # by name
        self.assertEqual(R.expiry_date(date(2026, 10, 27), 45, "45 day pack"), date(2026, 12, 11))     # plain days

    def test_renewal_starts_day_after_previous_end(self):
        self.assertEqual(R.renewal_start(date(2026, 9, 21)), date(2026, 9, 22))

    def test_gst_added_on_top_of_fee_minus_discount(self):
        t = R.bill_totals("4000", "250", True, "18")
        self.assertEqual((t["net"], t["gst_amount"], t["total"]), (D("3750.00"), D("675.00"), D("4425.00")))
        self.assertEqual(R.bill_totals("4000", "250", False, "18")["total"], D("3750.00"))
        with self.assertRaises(ValueError):
            R.bill_totals("1000", "1500", False, 0)

    def test_payment_status_and_remaining(self):
        self.assertEqual(R.payment_status("1500", "0"), "Pending")
        self.assertEqual(R.payment_status("1500", "700"), "Partially Paid")
        self.assertEqual(R.payment_status("1500", "1500"), "Paid")
        self.assertEqual(R.remaining("1500", "2000"), D("0.00"))
        # FIX vs v1.2.0: total 4425 incl. GST, 3750 paid -> v1.2.0 said "Paid"; really 675 is still due.
        self.assertEqual(R.payment_status("4425", "3750"), "Partially Paid")
        self.assertEqual(R.remaining("4425", "3750"), D("675.00"))

    def test_trainer_share_v120_example(self):
        # v1.2.0 docstring: PT fee 7000, share 50%, paid 7000 -> trainer 3500, gym 3500
        self.assertEqual(R.trainer_gym_share("7000", True, "50", "7000", "7000"), (D("3500.00"), D("3500.00")))
        self.assertEqual(R.trainer_gym_share("3000", True, "50", "7000", "7000"), (D("1500.00"), D("1500.00")))
        self.assertEqual(R.trainer_gym_share("1500", False, "50", "1500", "1500"), (D("0.00"), D("1500.00")))
        # v1.2.0 applies % to the GST-inclusive amount; optional setting uses the pre-GST part.
        self.assertEqual(R.trainer_gym_share("5900", True, "40", "5000", "5900", True)[0], D("2360.00"))
        self.assertEqual(R.trainer_gym_share("5900", True, "40", "5000", "5900", False)[0], D("2000.00"))

    def test_codes(self):
        self.assertEqual(R.code_prefix("P&P Fitness"), "PPF")
        self.assertEqual(R.code_prefix("Momentum"), "MOM")
        self.assertEqual(R.code_prefix("Gold's Gym Kothrud Pune West"), "GSGK")
        self.assertEqual(R.member_code("PPF", 1), "PPF000001")
        self.assertEqual(R.receipt_no("RCPT", 74), "RCPT00074")

    def test_links(self):
        self.assertTrue(R.whatsapp_link("+91 98765 43210", "Hi there").startswith("https://wa.me/919876543210?text=Hi%20there"))
        link = R.upi_link("ppfitness@okaxis", "P&P Fitness", "675", "RCPT00001 Rohit")
        self.assertEqual(link, "upi://pay?pa=ppfitness%40okaxis&pn=P%26P%20Fitness&am=675.00&cu=INR&tn=RCPT00001%20Rohit")
        self.assertEqual(R.upi_link("", "x", 1, "n"), "")


class Base(TestCase):
    def make_gym(self, name="P&P Fitness", gst="18", upi="ppfitness@okaxis"):
        c = Client.objects.create(gym_name=name, owner_name="Owner", phone="9000000000")
        License.objects.create(client=c, expiry_date=date.today() + timedelta(days=300))
        GymProfile.for_client(c)
        GymSettings.objects.create(client=c, gym_name=name, gst_percent=D(gst), upi_id=upi)
        return c

    def user(self, client, role, phone=None, name=None):
        u = StaffUser(client=client, name=name or role.title(), phone=phone or f"+9198{secrets.randbelow(10**8):08d}", role=role)
        u.set_password("Strong-pass-2026")
        u.save()
        d = StaffDevice.objects.create(client=client, installation_id="i-" + secrets.token_hex(6), secret_hash="x" * 64,
                                       name=f"{role} device", platform="windows", registered_by=u, status="approved")
        _, access, _ = StaffSession.start(u, d)
        return u, access

    def req(self, method, url, tok, data=None):
        h = {"HTTP_AUTHORIZATION": f"Bearer {tok}"}
        if method == "GET":
            r = self.client.get("/api/v1/" + url, data or {}, **h)
        else:
            r = self.client.post("/api/v1/" + url, json.dumps(data or {}), content_type="application/json", **h)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.content.decode()

    def setUp(self):
        self.gym = self.make_gym()
        self.owner_u, self.owner = self.user(self.gym, "owner")
        self.mgr_u, self.mgr = self.user(self.gym, "manager")
        self.rec_u, self.rec = self.user(self.gym, "receptionist")
        self.tr_u, self.tr = self.user(self.gym, "trainer", name="Rahul Verma")
        s, b = self.req("POST", "plans/", self.owner, {"name": "Monthly", "duration_days": 30, "fee": "1500"})
        self.monthly = b["plan"]["id"]
        s, b = self.req("POST", "plans/", self.owner, {"name": "Quarterly", "duration_days": 90, "fee": "4000",
                                                       "freeze_max_days": 15, "freeze_max_times": 2})
        self.quarterly = b["plan"]["id"]
        s, b = self.req("POST", "trainers/", self.owner, {"name": "Rahul Verma", "phone": "9000000001", "staff_user_id": self.tr_u.id})
        self.assertEqual(s, 201, b)
        self.trainer = b["trainer"]["id"]

    def member(self, tok=None, name="Rohit Kulkarni", phone=None, **kw):
        s, b = self.req("POST", "members/", tok or self.rec, {"name": name, "phone": phone or f"98{secrets.randbelow(10**8):08d}",
                                                             "gender": "Male", **kw})
        self.assertEqual(s, 201, b)
        return b["member"]

    def bill(self, tok, member_id, plan_id, **kw):
        return self.req("POST", "bills/", tok, {"member_id": member_id, "plan_id": plan_id, **kw})


class MemberTests(Base):
    def test_codes_use_gym_prefix_and_count_up(self):
        a, b = self.member(), self.member(name="Sneha More")
        self.assertEqual((a["code"], b["code"]), ("PPF000001", "PPF000002"))
        other = self.make_gym("Momentum Gym")
        _, otok = self.user(other, "owner")
        self.assertEqual(self.member(otok)["code"], "MG000001")

    def test_duplicate_phone_and_roles(self):
        self.member(phone="9876543210")
        s, b = self.req("POST", "members/", self.rec, {"name": "X", "phone": "+91 98765 43210", "gender": "Male"})
        self.assertEqual((s, b["error"]), (409, "phone_taken"))
        s, b = self.req("POST", "members/", self.tr, {"name": "X", "phone": "9876500000", "gender": "Male"})
        self.assertEqual((s, b["error"]), (403, "permission_denied"))
        m = self.member(name="Y")
        s, b = self.req("POST", f"members/{m['id']}/archive/", self.rec)
        self.assertEqual(s, 403)                                   # delete is owner-only
        self.assertEqual(self.req("POST", f"members/{m['id']}/archive/", self.owner)[0], 200)

    def test_edit_conflict_detected(self):
        m = self.member()
        s, b = self.req("POST", f"members/{m['id']}/update/", self.rec, {"email": "a@b.com", "version": 1})
        self.assertEqual((s, b["member"]["version"]), (200, 2))
        s, b = self.req("POST", f"members/{m['id']}/update/", self.mgr, {"email": "c@d.com", "version": 1})
        self.assertEqual((s, b["error"]), (409, "edit_conflict"))

    def test_fitness_profile(self):
        m = self.member(fitness={"current_weight_kg": "77", "target_weight_kg": "68", "fitness_goal": "Weight loss"})
        self.assertEqual(m["fitness"]["current_weight_kg"], "77.0")


class BillingTests(Base):
    def test_new_bill_full_payment(self):
        m = self.member()
        s, b = self.bill(self.rec, m["id"], self.monthly, start_date="2026-08-21", amount_paid="1500", payment_mode="UPI",
                         reference_no="UTR123")
        self.assertEqual(s, 201, b)
        bill = b["bill"]
        self.assertEqual((bill["receipt_no"], bill["total"], bill["paid"], bill["payment_status"]),
                         ("RCPT00001", "1500.00", "1500.00", "Paid"))
        self.assertEqual((bill["start_date"], bill["end_date"]), ("2026-08-21", "2026-09-21"))
        self.assertIn("wa.me/91", bill["whatsapp"]["link"])
        self.assertIn("RCPT00001", bill["whatsapp"]["text"])
        self.assertIsNone(bill["upi"])                                 # nothing due
        s, b2 = self.bill(self.rec, self.member(name="B")["id"], self.monthly)
        self.assertEqual(b2["bill"]["receipt_no"], "RCPT00002")

    def test_gst_partial_then_collect_dues(self):
        m = self.member()
        s, b = self.bill(self.mgr, m["id"], self.quarterly, discount="250", apply_gst=True, amount_paid="3750")
        bill = b["bill"]
        self.assertEqual((bill["gst_amount"], bill["total"], bill["remaining"], bill["payment_status"]),
                         ("675.00", "4425.00", "675.00", "Partially Paid"))
        self.assertTrue(bill["upi"]["link"].startswith("upi://pay?pa=ppfitness%40okaxis"))
        self.assertIn("am=675.00", bill["upi"]["link"])
        s, b = self.req("POST", f"bills/{bill['id']}/payments/", self.rec, {"mode": "UPI", "reference_no": "UTR9"})
        self.assertEqual((s, b["bill"]["payment_status"], b["bill"]["remaining"]), (200, "Paid", "0.00"))
        s, b = self.req("POST", f"bills/{bill['id']}/payments/", self.rec, {"amount": "1"})
        self.assertEqual((s, b["error"]), (400, "already_paid"))

    def test_receptionist_cannot_discount_or_cancel(self):
        m = self.member()
        s, b = self.bill(self.rec, m["id"], self.monthly, discount="100")
        self.assertEqual((s, b["error"]), (403, "permission_denied"))
        s, b = self.bill(self.rec, m["id"], self.monthly, fee="999")
        self.assertEqual(s, 403)
        s, b = self.bill(self.rec, m["id"], self.monthly, amount_paid="1500")
        s, c = self.req("POST", f"bills/{b['bill']['id']}/cancel/", self.rec, {"reason": "mistake"})
        self.assertEqual(s, 403)
        s, c = self.req("POST", f"bills/{b['bill']['id']}/cancel/", self.owner, {"reason": "mistake"})
        self.assertEqual((s, c["bill"]["status"]), (200, "cancelled"))
        s, mm = self.req("GET", f"members/{m['id']}/", self.owner)
        self.assertEqual(mm["member"]["status"], "no_plan")

    def test_overpay_and_gst_not_set_up(self):
        m = self.member()
        s, b = self.bill(self.rec, m["id"], self.monthly, amount_paid="2000")
        self.assertEqual((s, b["error"]), (400, "overpaid"))
        GymSettings.objects.filter(client=self.gym).update(gst_percent=0)
        s, b = self.bill(self.rec, m["id"], self.monthly, apply_gst=True)
        self.assertEqual((s, b["error"]), (400, "gst_not_set_up"))

    def test_retry_with_same_client_ref_does_not_bill_twice(self):
        m = self.member()
        s1, b1 = self.bill(self.rec, m["id"], self.monthly, amount_paid="1500", client_ref="app-123")
        s2, b2 = self.bill(self.rec, m["id"], self.monthly, amount_paid="1500", client_ref="app-123")
        self.assertEqual((s1, s2, b2["created"]), (201, 200, False))
        self.assertEqual(b1["bill"]["receipt_no"], b2["bill"]["receipt_no"])
        self.assertEqual(Bill.objects.count(), 1)

    def test_renewal_starts_after_previous_end(self):
        m = self.member()
        self.bill(self.rec, m["id"], self.monthly, start_date="2026-08-21", amount_paid="1500")
        s, b = self.bill(self.rec, m["id"], self.monthly, kind="renewal", amount_paid="1500")
        self.assertEqual((b["bill"]["start_date"], b["bill"]["end_date"]), ("2026-09-22", "2026-10-22"))

    def test_pt_plan_needs_trainer_and_splits_per_payment(self):
        s, p = self.req("POST", "plans/", self.owner, {"name": "PT Monthly", "duration_days": 30, "fee": "7000",
                                                       "is_personal_training": True, "trainer_share_percent": "50"})
        m = self.member()
        s, b = self.bill(self.rec, m["id"], p["plan"]["id"], amount_paid="3000")
        self.assertEqual((s, b["error"]), (400, "trainer_required"))
        s, b = self.bill(self.rec, m["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="3000")
        self.req("POST", f"bills/{b['bill']['id']}/payments/", self.rec, {})
        s, d = self.req("GET", f"bills/{b['bill']['id']}/", self.owner)
        # FIX vs v1.2.0: the later payment also pays the trainer his share.
        self.assertEqual((d["bill"]["trainer_share"], d["bill"]["gym_share"]), ("3500.00", "3500.00"))
        self.assertEqual([x["trainer_share"] for x in d["bill"]["payments"]], ["1500.00", "2000.00"])

    def test_family_plan_covers_each_member(self):
        s, p = self.req("POST", "plans/", self.owner, {"name": "Couple Monthly", "duration_days": 30, "fee": "2600", "members_covered": 2})
        a, b = self.member(name="Anjali"), self.member(name="Karan")
        s, r = self.bill(self.rec, a["id"], p["plan"]["id"])
        self.assertEqual((s, r["error"]), (400, "wrong_member_count"))
        s, r = self.bill(self.rec, a["id"], p["plan"]["id"], member_ids=[a["id"], b["id"]], amount_paid="2600")
        self.assertEqual(s, 201, r)
        self.assertEqual(len(r["bill"]["covers"]), 2)
        for x in (a, b):
            self.assertEqual(self.req("GET", f"members/{x['id']}/", self.rec)[1]["member"]["status"], "active")

    def test_quote_and_receipt(self):
        m = self.member()
        s, q = self.req("POST", "bills/quote/", self.rec, {"member_id": m["id"], "plan_id": self.quarterly, "discount": "250",
                                                           "apply_gst": True, "amount_paid": "4425"})
        self.assertEqual((q["quote"]["total"], q["quote"]["payment_status"]), ("4425.00", "Paid"))
        s, b = self.bill(self.rec, m["id"], self.monthly, amount_paid="1500")
        s, html = self.req("GET", f"bills/{b['bill']['id']}/receipt/", self.rec)
        self.assertEqual(s, 200)
        self.assertIn("RCPT00001", html)
        self.assertIn("P&amp;P Fitness", html)


class FreezeAndSessionTests(Base):
    def test_freeze_extends_and_blocks_checkin(self):
        m = self.member()
        today = date.today()
        s, b = self.bill(self.rec, m["id"], self.quarterly, start_date=today.isoformat(), amount_paid="4000")
        ms = b["bill"]
        mid = Membership.objects.get(bill_id=ms["id"]).id
        orig_end = R.expiry_date(today, 90, "Quarterly")
        s, r = self.req("POST", f"memberships/{mid}/freeze/", self.rec, {"days": 10, "reason": "Travel"})
        self.assertEqual(s, 200, r)
        self.assertEqual(r["member"]["status"], "frozen")
        self.assertEqual(r["member"]["end_date"], (orig_end + timedelta(days=10)).isoformat())
        s, c = self.req("POST", "checkins/", self.rec, {"member_id": m["id"]})
        self.assertEqual((s, c["error"]), (409, "not_allowed_in"))
        s, c = self.req("POST", "checkins/", self.rec, {"member_id": m["id"], "override_reason": "came anyway"})
        self.assertEqual(s, 403)
        s, r = self.req("POST", f"memberships/{mid}/freeze/", self.rec, {"days": 6, "start_date": (today + timedelta(days=20)).isoformat()})
        self.assertEqual((s, r["error"]), (400, "freeze_limit"))   # 10 + 6 > 15
        fid = r and Membership.objects.get(pk=mid).freezes.first().id
        s, r = self.req("POST", f"memberships/{mid}/freezes/{fid}/end/", self.rec, {"date": (today + timedelta(days=3)).isoformat()})
        self.assertEqual(r["member"]["end_date"], (orig_end + timedelta(days=3)).isoformat())

    def test_plan_without_freeze(self):
        m = self.member()
        s, b = self.bill(self.rec, m["id"], self.monthly, amount_paid="1500")
        mid = Membership.objects.get(bill_id=b["bill"]["id"]).id
        s, r = self.req("POST", f"memberships/{mid}/freeze/", self.rec, {"days": 5})
        self.assertEqual((s, r["error"]), (400, "freeze_not_allowed"))

    def test_session_pack_counts_down(self):
        s, p = self.req("POST", "plans/", self.owner, {"name": "PT 2 sessions", "duration_days": 30, "fee": "1000",
                                                       "is_personal_training": True, "trainer_share_percent": "40", "pt_sessions": 2})
        m = self.member()
        s, b = self.bill(self.rec, m["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="1000")
        mid = Membership.objects.get(bill_id=b["bill"]["id"]).id
        for left in (1, 0):
            s, r = self.req("POST", f"memberships/{mid}/sessions/", self.tr, {})
            self.assertEqual(s, 200, r)
            self.assertEqual(r["member"]["memberships"][0]["sessions"]["left"], left)
        s, r = self.req("POST", f"memberships/{mid}/sessions/", self.tr, {})
        self.assertEqual((s, r["error"]), (400, "no_sessions_left"))


class CheckInTests(Base):
    def test_once_per_day_and_expired_refused(self):
        m = self.member()
        self.bill(self.rec, m["id"], self.monthly, amount_paid="1500")
        s, c = self.req("POST", "checkins/", self.rec, {"member_id": m["id"]})
        self.assertEqual((s, c["created"]), (201, True))
        s, c = self.req("POST", "checkins/", self.rec, {"member_id": m["id"]})
        self.assertEqual((s, c["already_checked_in"]), (200, True))
        old = self.member(name="Vikas")
        self.bill(self.rec, old["id"], self.monthly, start_date="2025-01-01", amount_paid="1500")
        s, c = self.req("POST", "checkins/", self.rec, {"member_id": old["id"]})
        self.assertEqual((s, c["member_status"]), (409, "expired"))
        s, c = self.req("POST", "checkins/", self.mgr, {"member_id": old["id"], "override_reason": "renewing at desk"})
        self.assertEqual(s, 201)
        s, lst = self.req("GET", "checkins/", self.rec)
        self.assertEqual(lst["total"], 2)
        s, look = self.req("GET", "checkins/lookup/", self.rec, {"q": "PPF000001"})
        self.assertTrue(look["results"][0]["checked_in_today"])

    def test_trainer_sees_and_checks_in_only_own_clients(self):
        s, p = self.req("POST", "plans/", self.owner, {"name": "PT", "duration_days": 30, "fee": "5000",
                                                       "is_personal_training": True, "trainer_share_percent": "40"})
        mine, other = self.member(name="Mine"), self.member(name="Other")
        self.bill(self.rec, mine["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="5000")
        self.bill(self.rec, other["id"], self.monthly, amount_paid="1500")
        s, lst = self.req("GET", "members/", self.tr)
        self.assertEqual([x["name"] for x in lst["members"]], ["Mine"])
        self.assertEqual(self.req("GET", f"members/{other['id']}/", self.tr)[0], 404)
        self.assertEqual(self.req("POST", "checkins/", self.tr, {"member_id": other["id"]})[0], 404)
        s, c = self.req("POST", "checkins/", self.tr, {"member_id": mine["id"]})
        self.assertEqual((s, c["checkin"]["source"]), (201, "trainer"))
        s, d = self.req("GET", "dashboard/", self.tr)
        self.assertNotIn("collected_today", d["dashboard"])        # trainers never see money


class IsolationTests(Base):
    def test_other_gym_sees_nothing(self):
        m = self.member()
        s, b = self.bill(self.rec, m["id"], self.monthly, amount_paid="1500")
        other = self.make_gym("Other Gym")
        _, otok = self.user(other, "owner")
        self.assertEqual(self.req("GET", f"members/{m['id']}/", otok)[0], 404)
        self.assertEqual(self.req("GET", f"bills/{b['bill']['id']}/", otok)[0], 404)
        self.assertEqual(self.req("GET", f"bills/{b['bill']['id']}/receipt/", otok)[0], 404)
        self.assertEqual(self.req("POST", f"bills/{b['bill']['id']}/payments/", otok, {})[0], 404)
        self.assertEqual(self.req("POST", "checkins/", otok, {"member_id": m["id"]})[0], 404)
        self.assertEqual(self.req("POST", "bills/", otok, {"member_id": m["id"], "plan_id": self.monthly})[0], 404)
        self.assertEqual(self.req("GET", "members/", otok)[1]["members"], [])
        self.assertEqual(self.req("GET", "bills/", otok)[1]["bills"], [])
        self.assertEqual(self.req("GET", "plans/", otok)[1]["plans"], [])
        mid = Membership.objects.first().id
        self.assertEqual(self.req("POST", f"memberships/{mid}/freeze/", otok, {"days": 2})[0], 404)

    def test_expired_licence_blocks_data(self):
        License.objects.filter(client=self.gym).update(expiry_date=date.today() - timedelta(days=1))
        s, b = self.req("GET", "members/", self.rec)
        self.assertEqual((s, b["error"]), (403, "licence_inactive"))

class ProfileTests(Base):
    """Profile screens: trainer profile, member visit history."""

    def setUp(self):
        super().setUp()
        s, p = self.req("POST", "plans/", self.owner, {"name": "PT 1 month", "duration_days": 30, "fee": "1000",
                                                       "is_personal_training": True, "trainer_share_percent": "40", "pt_sessions": 4})
        self.m = self.member()
        s, b = self.bill(self.rec, self.m["id"], p["plan"]["id"], trainer_id=self.trainer, amount_paid="1000")
        self.ms = Membership.objects.get(bill_id=b["bill"]["id"]).id
        self.req("POST", f"memberships/{self.ms}/sessions/", self.tr, {})
        self.req("POST", "checkins/", self.rec, {"member_id": self.m["id"]})

    def test_trainer_profile_owner_sees_clients_and_money(self):
        s, b = self.req("GET", f"trainers/{self.trainer}/", self.owner)
        self.assertEqual(s, 200, b)
        t = b["trainer"]
        self.assertEqual([c["member"]["id"] for c in t["clients"]], [self.m["id"]])
        self.assertEqual(t["clients"][0]["sessions"]["used"], 1)
        self.assertEqual(t["sessions"]["this_month"], 1)
        self.assertTrue(t["money_visible"])
        self.assertEqual(t["earnings"]["this_month"], "400.00")
        self.assertEqual(len(t["earnings"]["by_month"]), 6)

    def test_trainer_profile_roles(self):
        s, b = self.req("GET", f"trainers/{self.trainer}/", self.tr)          # the trainer themself
        self.assertEqual(s, 200, b)
        self.assertTrue(b["trainer"]["money_visible"])
        s, other = self.req("POST", "trainers/", self.owner, {"name": "Sana Khan", "phone": "9000000002"})
        self.assertEqual(self.req("GET", f"trainers/{other['trainer']['id']}/", self.tr)[0], 404)   # not their own
        s, b = self.req("GET", f"trainers/{self.trainer}/", self.rec)         # receptionist: view, no money
        self.assertEqual(s, 200)
        self.assertFalse(b["trainer"]["money_visible"])
        self.assertNotIn("earnings", b["trainer"])

    def test_trainer_profile_other_gym_404(self):
        g2 = self.make_gym("Iron House")
        _, o2 = self.user(g2, "owner")
        self.assertEqual(self.req("GET", f"trainers/{self.trainer}/", o2)[0], 404)

    def test_member_detail_has_recent_visits(self):
        s, b = self.req("GET", f"members/{self.m['id']}/", self.rec)
        self.assertEqual(len(b["member"]["visits"]["recent"]), 1)
        self.assertEqual(b["member"]["visits"]["recent"][0]["source"], "front_desk")
