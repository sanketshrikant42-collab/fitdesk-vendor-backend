from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from gym_accounts.models import GymProfile, OwnerSetupCode
from licensing.models import AuditLog, Client, Device, License, Plan, SupportQuery

from . import services as S


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_superuser("sanket", "s@example.com", "pw-Strong-1")
        self.gym = Client.objects.create(gym_name="Iron Core Gym", owner_name="Rahul More", phone="9876543210", city="Pune")
        self.lic = License.objects.create(client=self.gym, expiry_date=S.today() + timedelta(days=6))
        self.client.login(username="sanket", password="pw-Strong-1")


class AccessTests(Base):
    def test_signed_out_goes_to_login(self):
        self.client.logout()
        r = self.client.get("/control/gyms/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/control/login/?next=/control/gyms/"))

    def test_only_superusers(self):
        self.client.logout()
        User.objects.create_user("staffer", password="pw-Strong-2", is_staff=True)
        r = self.client.post("/control/login/", {"username": "staffer", "password": "pw-Strong-2"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "wrong")
        self.assertEqual(self.client.get("/control/").status_code, 302)

    def test_login_and_lockout(self):
        self.client.logout()
        r = self.client.post("/control/login/", {"username": "sanket", "password": "pw-Strong-1", "next": "/control/gyms/"})
        self.assertEqual(r["Location"], "/control/gyms/")
        self.client.logout()
        for _ in range(5):
            self.client.post("/control/login/", {"username": "sanket", "password": "nope"})
        r = self.client.post("/control/login/", {"username": "sanket", "password": "pw-Strong-1"})
        self.assertContains(r, "Too many wrong tries")

    def test_next_cannot_leave_the_panel(self):
        self.client.logout()
        r = self.client.post("/control/login/", {"username": "sanket", "password": "pw-Strong-1", "next": "https://evil.example/"})
        self.assertEqual(r["Location"], "/control/")

    def test_changes_need_post(self):
        self.assertEqual(self.client.get(f"/control/gyms/{self.gym.id}/renew/").status_code, 405)


class PageTests(Base):
    def test_today_lists_renewal_and_support(self):
        SupportQuery.objects.create(client=self.gym, subject="Receipt not opening", message="help")
        r = self.client.get("/control/")
        self.assertContains(r, "Iron Core Gym")
        self.assertContains(r, "licence ends in 6 days")
        self.assertContains(r, "Receipt not opening")

    def test_test_gyms_stay_off_today(self):
        t = Client.objects.create(gym_name="Sanket test gym", owner_name="S", phone="9000000000")
        License.objects.create(client=t, expiry_date=S.today() + timedelta(days=2))
        self.assertNotContains(self.client.get("/control/"), "Sanket test gym")

    def test_gyms_list_search_and_filter(self):
        other = Client.objects.create(gym_name="Shakti Fitness", owner_name="Neha", phone="9123456789")
        License.objects.create(client=other, expiry_date=S.today() + timedelta(days=300))
        r = self.client.get("/control/gyms/?q=shakti")
        self.assertContains(r, "Shakti Fitness")
        self.assertNotContains(r, "Iron Core Gym")
        r = self.client.get("/control/gyms/?f=soon")
        self.assertContains(r, "Iron Core Gym")
        self.assertNotContains(r, "Shakti Fitness")
        self.assertContains(self.client.get(f"/control/gyms/?q={self.lic.key[-4:]}"), "Iron Core Gym")

    def test_times_in_india(self):
        from django.utils import timezone as tz
        from datetime import datetime
        AuditLog.record("licence_renewed", license=self.lic, months=1,
                        new_expiry=self.lic.expiry_date.isoformat())
        AuditLog.objects.update(created_at=datetime(2026, 11, 25, 9, 16, tzinfo=__import__("datetime").timezone.utc))
        self.assertContains(self.client.get(f"/control/gyms/{self.gym.id}/"), "2:46")

    def test_gym_detail(self):
        r = self.client.get(f"/control/gyms/{self.gym.id}/")
        self.assertContains(r, "Rahul More")
        self.assertContains(r, self.lic.key)          # in data-key for "Show & copy"
        self.assertEqual(self.client.get("/control/gyms/99999/").status_code, 404)


class ActionTests(Base):
    def test_renew_counts_from_end_date(self):
        end = self.lic.expiry_date
        r = self.client.post(f"/control/gyms/{self.gym.id}/renew/", {"months": "12"})
        self.assertEqual(r.status_code, 302)
        self.lic.refresh_from_db()
        self.assertEqual(self.lic.expiry_date, date(end.year + 1, end.month, end.day) if not (end.month == 2 and end.day == 29) else date(end.year + 1, 2, 28))
        self.assertTrue(AuditLog.objects.filter(action="licence_renewed", client=self.gym).exists())

    def test_renew_expired_counts_from_today(self):
        self.lic.expiry_date = S.today() - timedelta(days=40)
        self.lic.save()
        self.assertEqual(S.renew_to(self.lic, 1).month, (S.today().month % 12) + 1)

    def test_renew_month_end(self):
        self.lic.expiry_date = date(2027, 1, 31)
        self.assertEqual(S.renew_to(self.lic, 1), date(2027, 2, 28))

    def test_bad_months_refused(self):
        end = self.lic.expiry_date
        self.client.post(f"/control/gyms/{self.gym.id}/renew/", {"months": "500"})
        self.lic.refresh_from_db()
        self.assertEqual(self.lic.expiry_date, end)

    def test_setup_code_shown_once_and_works(self):
        r = self.client.post(f"/control/gyms/{self.gym.id}/setup-code/", follow=True)
        code = r.context["reveal"]["code"]
        self.assertContains(r, code)
        self.assertTrue(OwnerSetupCode.objects.filter(client=self.gym, code_hint=code[-4:]).exists())
        self.assertNotContains(self.client.get(f"/control/gyms/{self.gym.id}/"), code)    # never again

    def test_suspend_needs_name_and_locks(self):
        self.client.post(f"/control/gyms/{self.gym.id}/status/", {"to": "suspended", "confirm": "iron"})
        self.lic.refresh_from_db()
        self.assertEqual(self.lic.status, "active")
        self.client.post(f"/control/gyms/{self.gym.id}/status/", {"to": "suspended", "confirm": "Iron Core Gym"})
        self.lic.refresh_from_db()
        self.assertEqual(self.lic.effective_status, "suspended")
        self.client.post(f"/control/gyms/{self.gym.id}/status/", {"to": "active", "confirm": "Iron Core Gym"})
        self.lic.refresh_from_db()
        self.assertEqual(self.lic.effective_status, "active")
        self.assertEqual(self.client.post(f"/control/gyms/{self.gym.id}/status/", {"to": "revoked", "confirm": "Iron Core Gym"}).status_code, 404)

    def test_edit_details(self):
        self.client.post(f"/control/gyms/{self.gym.id}/edit/", {"gym_name": "Iron Core", "owner_name": "Rahul", "phone": "9876543210", "city": "Pune"})
        self.gym.refresh_from_db()
        self.assertEqual(self.gym.gym_name, "Iron Core")
        self.client.post(f"/control/gyms/{self.gym.id}/edit/", {"gym_name": "", "owner_name": "x", "phone": "1"})
        self.gym.refresh_from_db()
        self.assertEqual(self.gym.gym_name, "Iron Core")

    def test_plan_and_pc_limit(self):
        plan = Plan.objects.get(plan_id="standard")
        self.client.post(f"/control/gyms/{self.gym.id}/licence/", {"plan": plan.pk, "max_devices": "3"})
        self.lic.refresh_from_db()
        self.assertEqual((self.lic.plan_id, self.lic.max_devices), (plan.pk, 3))

    def test_block_and_allow_desktop_pc(self):
        d = Device.objects.create(license=self.lic, fingerprint="abc", device_name="FRONTDESK")
        self.client.post(f"/control/gyms/{self.gym.id}/devices/{d.id}/", {"to": "blocked"})
        d.refresh_from_db()
        self.assertEqual(d.status, "blocked")
        self.assertEqual(self.lic.get_or_activate_device("abc")[1], "device_blocked")
        self.client.post(f"/control/gyms/{self.gym.id}/devices/{d.id}/", {"to": "active"})
        d.refresh_from_db()
        self.assertEqual(d.status, "active")
        other = Client.objects.create(gym_name="Other", owner_name="O", phone="9")
        self.assertEqual(self.client.post(f"/control/gyms/{other.id}/devices/{d.id}/", {"to": "blocked"}).status_code, 404)


class AddGymTests(Base):
    def test_three_steps(self):
        r = self.client.post("/control/gyms/new/", {"step": "1", "gym_name": "Muscle Factory", "owner_name": "Amit",
                                                    "phone": "98200 12345", "city": "Mumbai"})
        self.assertEqual(r["Location"], "/control/gyms/new/?step=2")
        plan = Plan.objects.get(plan_id="premium")
        r = self.client.post("/control/gyms/new/", {"step": "2", "plan": plan.pk, "months": "12", "max_devices": "2", "price": "18000"})
        c = Client.objects.get(gym_name="Muscle Factory")
        self.assertEqual(r["Location"], f"/control/gyms/new/done/{c.id}/")
        lic = c.current_license
        self.assertEqual((lic.plan_id, lic.max_devices, lic.effective_status), (plan.pk, 2, "active"))
        self.assertGreater((lic.expiry_date - S.today()).days, 360)
        self.assertTrue(GymProfile.objects.filter(client=c).exists())
        r = self.client.get(r["Location"])
        code = r.context["code"]
        self.assertTrue(code and OwnerSetupCode.objects.get(client=c).code_hint == code[-4:])
        self.assertContains(r, lic.key)
        self.assertIn("wa.me/919820012345", r.context["wa"])
        self.assertNotContains(self.client.get(f"/control/gyms/new/done/{c.id}/"), code)   # shown once
        self.assertTrue(AuditLog.objects.filter(action="gym_created", client=c).exists())

    def test_step1_needs_name_and_mobile(self):
        r = self.client.post("/control/gyms/new/", {"step": "1", "gym_name": "X", "owner_name": "", "phone": "12"})
        self.assertContains(r, "10-digit mobile")
        self.assertFalse(Client.objects.filter(gym_name="X").exists())

    def test_step2_without_step1_restarts(self):
        self.assertEqual(self.client.get("/control/gyms/new/?step=2")["Location"], "/control/gyms/new/")


class MoneyTests(Base):
    def pay(self, **kw):
        data = {"client": self.gym.id, "amount": "25,000", "mode": "UPI", "months": "12", "reference": "4471",
                "paid_on": S.today().isoformat(), "send": "on"}
        data.update(kw)
        return self.client.post("/control/money/", data, follow=True)

    def test_record_payment_renews_and_makes_invoice(self):
        from .models import VendorPayment
        end = self.lic.expiry_date
        r = self.pay()
        p = VendorPayment.objects.get()
        self.lic.refresh_from_db()
        self.assertEqual(p.amount, 25000)
        self.assertEqual(p.period_from, end)
        self.assertEqual(p.period_to, self.lic.expiry_date)
        self.assertEqual(self.lic.expiry_date, S.renew_to(License(expiry_date=end), 12))
        self.assertTrue(p.invoice_no.startswith(f"CT/{S.fy_label(S.today())}/0001"))
        self.assertContains(r, "Send on WhatsApp")
        self.assertIn("wa.me/919876543210", r.context["reveal"]["wa"])
        self.assertTrue(AuditLog.objects.filter(action="payment_recorded").exists())
        # second payment gets the next number
        self.pay(months="0", amount="500")
        self.assertTrue(VendorPayment.objects.filter(invoice_no__endswith="/0002").exists())

    def test_bad_payment_refused(self):
        from .models import VendorPayment
        self.pay(amount="0")
        self.pay(mode="Bitcoin")
        self.pay(paid_on=(S.today() + timedelta(days=3)).isoformat())
        self.pay(months="7")
        self.assertEqual(VendorPayment.objects.count(), 0)

    def test_invoice_pdf_and_public_link(self):
        from .models import VendorPayment
        self.pay()
        p = VendorPayment.objects.get()
        r = self.client.get(f"/control/payments/{p.id}/invoice.pdf")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        wa = self.client.get("/control/money/").context  # reveal already used
        from urllib.parse import unquote
        self.client.logout()
        self.assertEqual(self.client.get(f"/control/payments/{p.id}/invoice.pdf").status_code, 302)   # needs sign-in
        from gym_core.models import ShortLink
        code = ShortLink.objects.get(kind="i").code
        r = self.client.get(f"/i/{code}")                                       # owner's link: no sign-in
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn("Invoice CT-", r["Content-Disposition"])
        self.assertEqual(self.client.get("/i/zzzzzzz").status_code, 404)

    def test_money_page_and_reminder(self):
        from .models import GymBilling
        GymBilling.objects.create(client=self.gym, yearly_price=25000)
        r = self.client.get("/control/money/")
        self.assertContains(r, "Iron Core Gym")
        self.assertEqual(r.context["m"]["due"], 25000)
        r = self.client.post(f"/control/money/remind/{self.gym.id}/")
        self.assertTrue(r["Location"].startswith("https://wa.me/919876543210?text="))
        self.assertIn("%E2%82%B925%2C000", r["Location"])                       # ₹25,000
        self.assertTrue(AuditLog.objects.filter(action="renewal_reminder_sent").exists())
        self.assertIsNotNone(self.client.get("/control/money/").context["m"]["renewals"][0]["reminded"])

    def test_rupees(self):
        from .invoice import rupees
        self.assertEqual(rupees(125000), "₹1,25,000")
        self.assertEqual(rupees("18000.50"), "₹18,000.50")
        self.assertEqual(rupees(999), "₹999")

    def test_add_gym_with_first_payment(self):
        from .models import GymBilling, VendorPayment
        self.client.post("/control/gyms/new/", {"step": "1", "gym_name": "Shakti", "owner_name": "Neha", "phone": "9123456789"})
        plan = Plan.objects.get(plan_id="premium")
        self.client.post("/control/gyms/new/", {"step": "2", "plan": plan.pk, "months": "12", "max_devices": "1",
                                                "price": "18000", "launch_offer": "on", "paid_now": "on", "mode": "Cash"})
        c = Client.objects.get(gym_name="Shakti")
        self.assertTrue(GymBilling.objects.get(client=c).launch_offer)
        p = VendorPayment.objects.get(client=c)
        self.assertEqual((p.amount, p.mode, p.months, p.period_to), (18000, "Cash", 12, c.current_license.expiry_date))
        self.assertEqual(c.current_license.expiry_date, p.period_to)          # first payment doesn't add another year
        self.assertEqual(self.client.get("/control/money/").context["m"]["launch_used"], 1)

    def test_price_saved_on_gym(self):
        from .models import GymBilling
        self.client.post(f"/control/gyms/{self.gym.id}/licence/", {"max_devices": "1", "yearly_price": "25000", "gstin": "27abcde1234f1z5"})
        b = GymBilling.objects.get(client=self.gym)
        self.assertEqual((b.yearly_price, b.gstin, b.launch_offer), (25000, "27ABCDE1234F1Z5", False))


class SupportTests(Base):
    def setUp(self):
        super().setUp()
        self.q = SupportQuery.objects.create(client=self.gym, subject="Second PC", message="How to add a second PC?")

    def test_list_redirects_to_first_and_reply_reaches_gym(self):
        r = self.client.get("/control/support/")
        self.assertEqual(r["Location"], f"/control/support/{self.q.id}/?tab=open")
        self.assertContains(self.client.get(r["Location"]), "How to add a second PC?")
        self.client.post(f"/control/support/{self.q.id}/act/", {"act": "reply", "text": "Use Team & devices.", "close": "on"})
        self.q.refresh_from_db()
        self.assertIn("Crestline: Use Team & devices.", self.q.admin_notes)
        self.assertEqual(self.q.status, "resolved")
        self.client.post(f"/control/support/{self.q.id}/act/", {"act": "reopen"})
        self.client.post(f"/control/support/{self.q.id}/act/", {"act": "reply", "text": "Second answer"})
        self.q.refresh_from_db()
        self.assertEqual(self.q.status, "open")
        self.assertIn("Use Team & devices.", self.q.admin_notes)            # earlier reply kept
        self.assertIn("Second answer", self.q.admin_notes)

    def test_call_back_and_private_note(self):
        from .models import SupportMeta
        self.client.post(f"/control/support/{self.q.id}/act/", {"act": "callback"})
        self.client.post(f"/control/support/{self.q.id}/act/", {"act": "note", "note": "Called, busy"})
        m = SupportMeta.objects.get(query=self.q)
        self.assertTrue(m.call_back)
        self.assertEqual(m.private_note, "Called, busy")
        self.assertEqual(self.client.get("/control/support/?tab=call")["Location"], f"/control/support/{self.q.id}/?tab=call")
        self.q.refresh_from_db()
        self.assertEqual(self.q.admin_notes, "")                               # private note never goes to the gym

    def test_bad_action(self):
        self.assertEqual(self.client.post(f"/control/support/{self.q.id}/act/", {"act": "delete"}).status_code, 404)


class HealthTests(Base):
    def test_page_and_tools(self):
        from unittest import mock
        r = self.client.get("/control/health/")
        self.assertContains(r, "Iron Core Gym")
        with mock.patch("django.core.management.call_command") as cc:
            r = self.client.post("/control/health/tool/", {"tool": "reminders"}, follow=True)
        cc.assert_called_once()
        self.assertContains(r, "reminder lists made")
        with mock.patch("django.core.management.call_command", side_effect=RuntimeError("R2 not set")):
            r = self.client.post("/control/health/tool/", {"tool": "backup"}, follow=True)
        self.assertContains(r, "R2 not set")
        self.assertEqual(self.client.post("/control/health/tool/", {"tool": "rm"}).status_code, 404)

    def test_backup_warning_for_desktop_gym(self):
        Device.objects.create(license=self.lic, fingerprint="pc1", device_name="PC")
        h = S.health_summary()
        self.assertEqual(h["late_n"], 1)
        self.assertContains(self.client.get("/control/"), "has never sent a cloud backup")


class NightlyGapTests(Base):
    def test_only_days_after_first_run_count(self):
        from gym_auto.models import DailyRun
        self.assertEqual(S.health_summary()["problems"], [])             # never ran: no false alarms
        DailyRun.objects.create(client=self.gym, date=S.today() - timedelta(days=3))
        DailyRun.objects.create(client=self.gym, date=S.today() - timedelta(days=1))
        texts = [p["text"] for p in S.health_summary()["problems"]]
        self.assertEqual(len(texts), 1)
        self.assertIn((S.today() - timedelta(days=2)).strftime("%d %b"), texts[0])
