"""Builds the demo gym: an invented gym ("Pulse Fitness Club") with a few
months of believable activity, all dated relative to today so renewals,
dues, birthdays and follow-ups always look current.

Everything here is made up. Names are common Indian first/last names
combined at random; phone numbers are in the 90000 xxxxx range.
"""
import random
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from gym_accounts.models import GymProfile, StaffUser
from gym_accounts.permissions import ROLE_OWNER
from gym_core import rules as R
from gym_core.models import (Bill, CheckIn, Counter, FitnessProfile, GymSettings, Member, Membership, Payment, Plan,
                             Trainer)
from gym_import.importer import IST, stamp, wipe_gym
from gym_ops.models import Enquiry, Expense, ProgressEntry, Staff, StaffAttendance
from licensing.models import Client, License

from .models import DemoGym

GYM_NAME = "Pulse Fitness Club"
PREFIX = "PFC"
D = Decimal

FIRST_M = ["Aarav", "Rohit", "Amit", "Vikram", "Sagar", "Nikhil", "Pranav", "Kunal", "Rahul", "Siddharth", "Omkar",
           "Yash", "Aditya", "Harsh", "Tejas", "Mihir", "Akash", "Ganesh", "Varun", "Kiran", "Sameer", "Abhishek", "Ninad", "Manoj"]
FIRST_F = ["Priya", "Sneha", "Pooja", "Neha", "Anjali", "Shruti", "Kavya", "Riya", "Aditi", "Meera", "Sayali", "Tanvi",
           "Pallavi", "Ishita", "Komal", "Rutuja", "Divya", "Nisha", "Sonal", "Gauri", "Ankita", "Manasi"]
LAST = ["Patil", "Deshmukh", "Kulkarni", "Joshi", "Shinde", "Pawar", "Jadhav", "More", "Gaikwad", "Chavan", "Sharma",
        "Mehta", "Iyer", "Nair", "Rao", "Kale", "Bhosale", "Sawant", "Naik", "Kadam", "Thakur", "Verma", "Gupta"]

ROLE_USERS = [  # role, name, phone (fixed so the demo button always finds them)
    (ROLE_OWNER, "Rohan Deshmukh", "+919000000001"),
    ("manager", "Sneha Kulkarni", "+919000000002"),
    ("receptionist", "Pooja Shinde", "+919000000003"),
    ("trainer", "Vikram Pawar", "+919000000004"),
]


def _ns(client, staff):
    return SimpleNamespace(client=client, staff=staff, device=None, META={}, GET={}, POST={})


def _aware(d, hh, mm):
    return timezone.make_aware(datetime.combine(d, time(hh, mm)), IST)


class Builder:
    def __init__(self, today=None, seed=None):
        self.today = today or timezone.localdate(timezone=IST)
        self.rnd = random.Random(seed if seed is not None else self.today.toordinal())
        self.phones = set()
        self.receipt_n = 0
        self.member_n = 0

    def phone(self):
        while True:
            p = f"+9190000{self.rnd.randint(10000, 99999)}"
            if p not in self.phones and not p.startswith("+91900000000"):
                self.phones.add(p)
                return p

    # -------------------------------------------------------------------------------------------
    @transaction.atomic
    def build(self):
        for old in DemoGym.objects.select_related("client"):
            wipe_gym(old.client)
        t = self.today
        c = self.client = Client.objects.create(gym_name=GYM_NAME, owner_name="Rohan Deshmukh", phone="+919000000001",
                                                email="demo@crestlinetechnologies.in", city="Pune",
                                                notes="FitDesk demo gym: made-up data, rebuilt every night.")
        License.objects.create(client=c, expiry_date=t + timedelta(days=3650))
        prof = GymProfile.for_client(c)
        prof.time_zone, prof.max_devices, prof.owner_free_devices = "Asia/Kolkata", 100000, 100000
        prof.save()
        GymSettings.objects.create(client=c, gym_name=GYM_NAME, tagline="Train hard. Stay consistent.",
                                   address="2nd floor, Sai Plaza, Baner Road, Pune 411045", phone="+919000000001",
                                   email="hello@pulsefitness.example", gst_percent=R.ZERO, member_code_prefix=PREFIX,
                                   receipt_prefix=PREFIX, receipt_footer="Thank you for training with us!", terms="")
        DemoGym.objects.create(client=c)
        self.users()
        self.plans()
        self.team()
        self.members()
        self.expenses()
        self.enquiries()
        self.diet()
        Counter.objects.update_or_create(client=c, key="member", defaults={"value": self.member_n})
        Counter.objects.update_or_create(client=c, key="receipt", defaults={"value": self.receipt_n})
        Counter.objects.update_or_create(client=c, key="enquiry", defaults={"value": self.enquiry_n})
        return c

    # -------------------------------------------------------------------------------------------
    def users(self):
        self.users_by_role = {}
        for role, name, ph in ROLE_USERS:
            u = StaffUser(client=self.client, name=name, phone=ph, role=role)
            u.set_password(secrets.token_urlsafe(24))           # nobody types it: the demo button signs in
            u.save()
            self.users_by_role[role] = u
        self.owner = self.users_by_role[ROLE_OWNER]

    def plans(self):
        c = self.client
        spec = [("Monthly", 30, 1500, False, 0), ("Quarterly", 90, 4000, False, 0), ("Half-yearly", 180, 7000, False, 0),
                ("Yearly", 365, 12000, False, 0), ("Personal Training – Monthly", 30, 6000, True, 40)]
        self.plan = {}
        for name, days, fee, pt, share in spec:
            self.plan[name] = Plan.objects.create(client=c, name=name, duration_days=days, fee=D(fee), is_personal_training=pt,
                                                  trainer_share_percent=D(share), description="", status=Plan.STATUS_ACTIVE)

    def team(self):
        c, t = self.client, self.today
        tr = [("Vikram Pawar", "M", "Strength & conditioning"), ("Anjali Iyer", "F", "Weight loss & yoga"),
              ("Kunal Mehta", "M", "Bodybuilding")]
        self.trainers = []
        for i, (name, g, spec) in enumerate(tr, 1):
            tobj = Trainer.objects.create(client=c, code=R.trainer_code(PREFIX, i), name=name, phone=self.phone(), gender=g,
                                          specialization=spec, joining_date=t - timedelta(days=400 - i * 40))
            self.trainers.append(tobj)
        self.trainers[0].staff_user = self.users_by_role["trainer"]
        self.trainers[0].save(update_fields=["staff_user"])
        self.users_by_role["trainer"].linked_record = self.trainers[0].code
        self.users_by_role["trainer"].save(update_fields=["linked_record"])
        st = [("Sneha Kulkarni", "F", "Manager", "Management", 30000, None), ("Pooja Shinde", "F", "Receptionist", "Front Desk", 15000, None),
              ("Ganesh Jadhav", "M", "Housekeeping", "Housekeeping", 11000, None), ("Vikram Pawar", "M", "Head Trainer", "Trainer", 25000, 0),
              ("Anjali Iyer", "F", "Trainer", "Trainer", 22000, 1), ("Kunal Mehta", "M", "Trainer", "Trainer", 22000, 2)]
        self.staff = []
        for i, (name, g, des, dep, sal, ti) in enumerate(st, 1):
            s = Staff.objects.create(client=c, code=R.staff_code(PREFIX, i), name=name, phone=self.phone(), gender=g,
                                     designation=des, department=dep, joining_date=t - timedelta(days=300 + i * 15),
                                     monthly_salary=D(sal), salary_basis=Staff.BASIS_CALENDAR, status="active",
                                     trainer=self.trainers[ti] if ti is not None else None)
            self.staff.append(s)
        for back in range(1, 46):                                   # attendance for the last 45 days, not today
            d = t - timedelta(days=back)
            for s in self.staff:
                if d.weekday() == 6:
                    status = "Weekly Off"
                else:
                    status = self.rnd.choices(["Present", "Half Day", "Absent", "Paid Leave"], [88, 5, 4, 3])[0]
                StaffAttendance.objects.create(staff=s, date=d, status=status,
                                               check_in="06:00" if status in ("Present", "Half Day") else "",
                                               check_out=("14:00" if status == "Present" else "10:00") if status in ("Present", "Half Day") else "")

    # -------------------------------------------------------------------------------------------
    def _bill(self, m, plan, start, paid_ratio=1.0, discount=0, mode=None, kind=Bill.KIND_NEW, trainer=None):
        c = self.client
        self.receipt_n += 1
        fee = plan.fee
        disc = D(discount)
        total = fee - disc
        paid = (total * D(str(paid_ratio))).quantize(D("1"))
        end = start + timedelta(days=plan.duration_days - 1)
        share_pct = plan.trainer_share_percent if plan.is_personal_training else R.ZERO
        b = Bill.objects.create(client=c, receipt_no=f"{PREFIX}{self.receipt_n:05d}", member=m, plan=plan, kind=kind,
                                trainer=trainer, fee=fee, discount=disc, gst_applied=False, gst_percent=R.ZERO, gst_amount=R.ZERO,
                                total=total, is_personal_training=plan.is_personal_training, trainer_share_percent=share_pct,
                                trainer_share_includes_gst=True, created_by=self.users_by_role["receptionist"],
                                client_ref=f"demo-{self.receipt_n}")
        bill_day = min(start, self.today)
        stamp(Bill, b.pk, created_at=_aware(bill_day, self.rnd.randint(7, 20), self.rnd.randint(0, 59)))
        Membership.objects.create(client=c, member=m, bill=b, plan=plan, trainer=trainer, start_date=start, end_date=end,
                                  original_end_date=end, status=Membership.STATUS_ACTIVE)
        if paid > 0:
            tshare = (paid * share_pct / 100).quantize(D("1")) if share_pct else R.ZERO
            p = Payment.objects.create(bill=b, amount=paid, mode=mode or self.rnd.choices(["UPI", "Cash", "Card"], [65, 30, 5])[0],
                                       reference_no="", paid_on=bill_day, trainer_share=tshare, gym_share=paid - tshare,
                                       received_by=self.users_by_role["receptionist"])
            stamp(Payment, p.pk, created_at=_aware(bill_day, self.rnd.randint(7, 20), self.rnd.randint(0, 59)))
        return end

    def members(self):
        c, t, rnd = self.client, self.today, self.rnd
        names = set()
        # (how many, what their latest membership looks like)
        profile = ([("active", 20), ("ending", 5), ("dues", 4), ("pt", 6), ("later", 1), ("expired", 8), ("new_today", 2)])
        self.pt_n = 0
        plans_cycle = ["Monthly", "Quarterly", "Quarterly", "Half-yearly", "Yearly", "Monthly"]
        self.active_members = []
        for kind, n in profile:
            for _ in range(n):
                g = rnd.choice(["Male", "Female"])
                while True:
                    nm = f"{rnd.choice(FIRST_M if g == 'Male' else FIRST_F)} {rnd.choice(LAST)}"
                    if nm not in names:
                        names.add(nm)
                        break
                age = rnd.randint(19, 52)
                dob = t.replace(year=t.year - age) - timedelta(days=rnd.randint(1, 360))
                bday = {2: 0, 9: 3}.get(len(self.active_members)) if kind in ("active", "ending") else None
                if bday is not None:                                 # a birthday today and one later this week
                    d0 = t + timedelta(days=bday)
                    dob = d0.replace(year=d0.year - age) if not (d0.month == 2 and d0.day == 29) else d0.replace(year=d0.year - age, day=28)
                self.member_n += 1
                m = Member.objects.create(client=c, code=R.member_code(PREFIX, self.member_n), name=nm, phone=self.phone(),
                                          gender=g, date_of_birth=dob, age=age, address="Pune",
                                          email=f"{nm.split()[0].lower()}.{self.member_n}@example.com")
                FitnessProfile.objects.create(member=m, height_cm=D(rnd.randint(152, 186)), current_weight_kg=D(rnd.randint(52, 98)),
                                              fitness_goal=rnd.choice(["Weight Loss", "Muscle Gain", "General Fitness", "Endurance"]))
                plan = self.plan[rnd.choice(plans_cycle)]
                if kind == "pt":
                    plan = self.plan["Personal Training – Monthly"]
                # where the latest membership ends, relative to today
                if kind == "active":
                    end_in = rnd.randint(9, plan.duration_days - 2) if plan.duration_days > 12 else 20
                elif kind == "ending":
                    end_in = rnd.randint(0, 6)
                elif kind == "dues":
                    end_in = rnd.randint(10, max(11, plan.duration_days - 5))
                elif kind == "pt":
                    end_in = rnd.randint(8, 26)
                elif kind == "expired":
                    end_in = -rnd.randint(3, 75)
                elif kind == "new_today":
                    end_in = plan.duration_days - 1
                else:                                               # "later": paid in advance, starts in 5 days
                    end_in = plan.duration_days + 4
                start = t + timedelta(days=end_in - plan.duration_days + 1)
                # one or two earlier memberships before this one (renewals)
                history = rnd.choice([0, 1, 1, 2]) if kind not in ("new_today",) else 0
                s = start
                firsts = []
                for _h in range(history):
                    p0 = self.plan[rnd.choice(plans_cycle)]
                    s = s - timedelta(days=p0.duration_days)
                    firsts.append((p0, s))
                stamp(Member, m.pk, created_at=_aware(min(s, t), 9, 0))
                for p0, s0 in reversed(firsts):
                    self._bill(m, p0, s0, discount=rnd.choice([0, 0, 0, 200, 500]))
                trainer = None
                if plan.is_personal_training:                        # the demo trainer login gets most PT clients
                    trainer = self.trainers[0] if self.pt_n < 4 else self.trainers[1 + self.pt_n % 2]
                    self.pt_n += 1
                ratio = rnd.choice([0.5, 0.6, 0.75]) if kind == "dues" else 1.0
                self._bill(m, plan, start, paid_ratio=ratio, discount=rnd.choice([0, 0, 300, 500]) if plan.fee >= 4000 else 0,
                           kind=Bill.KIND_RENEWAL if history else Bill.KIND_NEW, trainer=trainer)
                if kind in ("active", "ending", "dues", "pt", "new_today"):
                    self.active_members.append(m)
        # check-ins: last 21 days, mornings and evenings; today partly done
        for back in range(0, 21):
            d = t - timedelta(days=back)
            for m in self.active_members:
                if rnd.random() < (0.35 if back == 0 else 0.55):
                    hh = rnd.choice([6, 6, 7, 7, 8, 18, 19, 19, 20])
                    if back == 0 and hh > timezone.localtime(timezone=IST).hour:
                        continue
                    ms = Membership.objects.filter(member=m, start_date__lte=d, end_date__gte=d).first()
                    if ms is None:
                        continue
                    CheckIn.objects.create(client=c, member=m, membership=ms, checked_in_at=_aware(d, hh, rnd.randint(0, 59)),
                                           local_date=d, source="front_desk", by_user=self.users_by_role["receptionist"])
        # progress for a few members
        for m in self.active_members[:4]:
            w = D(rnd.randint(70, 92))
            for k in range(4, -1, -1):
                ProgressEntry.objects.create(member=m, date=t - timedelta(days=k * 14 + 2), weight_kg=w,
                                             waist_cm=D(rnd.randint(80, 98)), recorded_by=self.users_by_role["trainer"])
                w -= D("0.8")

    def expenses(self):
        c, t = self.client, self.today
        items = [("Rent", "Monthly rent", 45000, "Bank Transfer"), ("Electricity", "MSEDCL bill", 8200, "UPI"),
                 ("Internet", "Broadband", 999, "UPI"), ("Cleaning", "Cleaning supplies", 1800, "Cash")]
        for k in range(0, 6):
            first = (t.replace(day=1) - timedelta(days=1)).replace(day=1) if k else t.replace(day=1)
            for _ in range(k - 1):
                first = (first - timedelta(days=1)).replace(day=1)
            for cat, title, amt, mode in items:
                d = min(first + timedelta(days=self.rnd.randint(1, 8)), t)
                Expense.objects.create(client=c, date=d, category=cat, title=title, amount=D(amt + self.rnd.randint(-300, 300) if amt > 2000 else amt),
                                       mode=mode, paid_to="", created_by=self.owner)
        Expense.objects.create(client=c, date=t - timedelta(days=12), category="Equipment", title="2 pairs of 20 kg dumbbells",
                               amount=D(9600), mode="UPI", created_by=self.owner)

    def enquiries(self):
        c, t, rnd = self.client, self.today, self.rnd
        spec = [("New", 0, "Hot", "Walk-in"), ("Follow-up", 0, "Warm", "Instagram"), ("Follow-up", -2, "Hot", "Phone Call"),
                ("Trial", 1, "Warm", "Referral"), ("New", 2, "Cold", "Google"), ("Follow-up", 3, "Warm", "WhatsApp"),
                ("Lost", None, "Cold", "Facebook"), ("New", 0, "Warm", "Website")]
        self.enquiry_n = 0
        for status, fu, pri, src in spec:
            self.enquiry_n += 1
            g = rnd.choice(["Male", "Female"])
            nm = f"{rnd.choice(FIRST_M if g == 'Male' else FIRST_F)} {rnd.choice(LAST)}"
            Enquiry.objects.create(client=c, code=f"ENQ-{self.enquiry_n:04d}", enquiry_date=t - timedelta(days=rnd.randint(0, 12)),
                                   name=nm, phone=self.phone(), gender=g, age=rnd.randint(20, 45), source=src,
                                   interested_plan=self.plan[rnd.choice(["Monthly", "Quarterly", "Yearly"])],
                                   fitness_goal=rnd.choice(["Weight loss", "Muscle gain", "Stay fit"]), priority=pri, status=status,
                                   next_follow_up_date=(t + timedelta(days=fu)) if fu is not None else None,
                                   lost_reason="Joined a gym closer to home" if status == "Lost" else "",
                                   notes="Asked about morning batch timings." if status != "Lost" else "",
                                   assigned_to=self.users_by_role["receptionist"], created_by=self.users_by_role["receptionist"])

    def diet(self):
        from gym_ops import diet_starter_plans as SP
        from gym_ops import services as OS
        starters = SP.list_starter_plans()
        req = _ns(self.client, self.users_by_role["trainer"])
        req.staff = self.owner
        for m, sp in zip(self.active_members[:4], starters[:4]):
            try:
                OS.create_diet_plan(req, m, {"name": sp.get("name") or "Diet plan", "goal": sp.get("goal", ""),
                                             "diet_type": sp.get("diet_type", ""), "start_date": self.today.isoformat(),
                                             "instructions": "Drink 3 litres of water a day."},
                                    [{"name": x["name"], "time": x["time"],
                                      "items": [{"food": i["food"], "portion": i["portion"]} for i in x["items"]]} for x in sp["meals"]],
                                    status="Active", source_type="starter", source_ref=sp.get("key", ""))
            except Exception:                                   # noqa: BLE001 - the demo still works without a diet plan
                pass


def build(today=None, seed=None):
    return Builder(today=today, seed=seed).build()
