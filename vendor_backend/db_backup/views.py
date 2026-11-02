import re
from urllib.parse import quote

from django.http import HttpResponse
from django.utils import timezone

from gym_accounts.api import endpoint, staff_required
from gym_core import services as C
from gym_core.models import GymSettings

from .excel import workbook


@endpoint("GET")
@staff_required(owner_only=True)
def export_all(request):
    """The owner downloads every record of their gym as one Excel file."""
    data = workbook(request.client)
    gym = GymSettings.for_client(request.client).gym_name or request.client.gym_name
    name = re.sub(r'[\\/:*?"<>|]+', " ", f"{gym} - all data - {C.today_for(request.client):%Y-%m-%d}.xlsx").strip()
    C.log(request, "data_exported", "All data (Excel)")
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = (f'attachment; filename="{name.encode("ascii", "ignore").decode() or "fitdesk-data.xlsx"}"; '
                                   f"filename*=UTF-8''{quote(name)}")
    resp["Cache-Control"] = "private, no-store"
    return resp
