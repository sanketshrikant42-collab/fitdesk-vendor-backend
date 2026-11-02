""""Download all my data": every record of one gym as an Excel workbook,
one sheet per kind of record, readable by the owner (names instead of ids).
Passwords, PINs, secret codes and tokens are never included."""
import json
import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from uuid import UUID

from django.db import models
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .gymscope import in_live

SECRET = re.compile(r"password|secret|hash|token|pin|setup_code|invite_code|app_key|smtp_pass|encrypted", re.I)
SKIP = {"gym_accounts.staffsession", "gym_accounts.ownersetupcode", "gym_accounts.invite", "gym_core.counter",
        "gym_auto.dailyrun", "gym_import.importrun", "gym_accounts.gymprofile", "gym_accounts.staffdevice",
        "gym_auto.emailsettings", "gym_auto.emaillog", "gym_auto.remindersettings", "gym_accounts.permissionoverride"}
NAMES = {"gym_core.checkin": "Check-ins", "gym_ops.enquiry": "Enquiries", "gym_ops.enquiryfollowup": "Enquiry follow-ups",
         "gym_core.fitnessprofile": "Fitness profiles", "gym_ops.dietplanversion": "Diet plan versions",
         "gym_accounts.staffuser": "Logins", "gym_accounts.teamactivity": "Activity log", "gym_core.sessionlog": "PT sessions",
         "gym_ops.progressentry": "Progress", "gym_ops.consentform": "Consent forms", "gym_core.gymsettings": "Gym settings",
         "gym_ops.staffattendance": "Staff attendance", "gym_ops.payrollsettlement": "Payroll",
         "gym_ops.salaryadvance": "Salary advances", "gym_ops.trainerpayout": "Trainer payouts",
         "gym_ops.referralreward": "Referral rewards", "gym_ops.dietshare": "Diet plans sent", "gym_ops.food": "Foods",
         "gym_ops.diettemplate": "Diet templates", "gym_auto.reminder": "Reminders"}
HIDE = {"id", "client"}
FIRST = ["gym_core.member", "gym_core.fitnessprofile", "gym_core.plan", "gym_core.bill", "gym_core.payment",
         "gym_core.membership", "gym_core.freeze", "gym_core.checkin", "gym_core.trainer", "gym_ops.staff",
         "gym_ops.staffattendance", "gym_ops.payrollsettlement", "gym_ops.salaryadvance", "gym_ops.trainerpayout",
         "gym_ops.expense", "gym_ops.enquiry", "gym_ops.enquiryfollowup", "gym_ops.dietplan", "gym_ops.dietplanversion",
         "gym_ops.progressentry", "gym_ops.consentform"]
HEAD = PatternFill("solid", fgColor="EEEAFE")
MONEY = '"₹"#,##0.00'
MAX = 32000


def _label(obj):
    if obj is None:
        return None
    for a in ("receipt_no", "name", "title", "gym_name"):
        v = getattr(obj, a, None)
        if v:
            code = getattr(obj, "code", "")
            return f"{v} ({code})" if a == "name" and code else str(v)
    return str(obj.pk)


def _value(v, tz):
    if v is None:
        return None
    if isinstance(v, datetime):
        return timezone.localtime(v, tz).replace(tzinfo=None) if timezone.is_aware(v) else v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    if isinstance(v, (bytes, memoryview)):
        return None
    if isinstance(v, str) and len(v) > MAX:
        return v[:MAX] + "…"
    return v


def workbook(client):
    from gym_core.services import gym_tz
    tz = gym_tz(client)
    owned = {m._meta.label_lower: (m, pks) for m, pks in in_live(client.pk).items()}
    order = [k for k in FIRST if k in owned] + sorted(k for k in owned if k not in FIRST)
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    for label in order:
        m, pks = owned[label]
        if label in SKIP or not pks:
            continue
        fields = [f for f in m._meta.concrete_fields if not SECRET.search(f.name) and f.name not in HIDE
                  and not isinstance(f, models.BinaryField)]
        rels = [f.name for f in fields if f.is_relation]
        rows = m._base_manager.filter(pk__in=list(pks)).select_related(*rels)
        name = NAMES.get(label, str(m._meta.verbose_name_plural).capitalize())[:31]
        while name in used:
            name = name[:28] + f" {len(used)}"
        used.add(name)
        ws = wb.create_sheet(name)
        ws.append([str(f.verbose_name).capitalize() for f in fields])
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), HEAD
        for obj in rows.order_by(*(["created_at"] if any(f.name == "created_at" for f in fields) else ["pk"])):
            ws.append([_label(getattr(obj, f.name)) if f.is_relation else _value(getattr(obj, f.attname), tz)
                       for f in fields])
        for i, f in enumerate(fields, 1):
            col = get_column_letter(i)
            ws.column_dimensions[col].width = 14 if not isinstance(f, (models.CharField, models.TextField)) else 22
            if isinstance(f, models.DecimalField):
                for c in ws[col][1:]:
                    c.number_format = MONEY
            elif isinstance(f, models.DateTimeField):
                for c in ws[col][1:]:
                    c.number_format = "dd mmm yyyy hh:mm"
            elif isinstance(f, models.DateField):
                for c in ws[col][1:]:
                    c.number_format = "dd mmm yyyy"
        ws.freeze_panes = "A2"
    if not wb.sheetnames:
        wb.create_sheet("No data yet")
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
