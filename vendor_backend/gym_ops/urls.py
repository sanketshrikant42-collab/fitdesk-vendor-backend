from django.conf import settings
from django.urls import path

from . import views

urlpatterns = [
    path("staff/", views.staff_list),
    path("staff/<int:staff_id>/", views.staff_detail),
    path("staff/<int:staff_id>/payroll/", views.payroll_calc),
    path("staff/<int:staff_id>/payroll/pay/", views.payroll_pay),
    path("staff/<int:staff_id>/advances/", views.salary_advance),
    path("staff-attendance/", views.staff_attendance),
    path("payroll/", views.payroll_month),
    path("expenses/", views.expenses),
    path("expenses/<int:expense_id>/update/", views.expense_update),
    path("expenses/<int:expense_id>/delete/", views.expense_delete),
    path("enquiries/", views.enquiries),
    path("enquiries/<int:enquiry_id>/", views.enquiry_detail),
    path("enquiries/<int:enquiry_id>/<slug:action>/", views.enquiry_action),
    path("members/<uuid:member_id>/referrals/", views.member_referrals),
    path("members/<uuid:member_id>/progress/", views.member_progress),
    path("members/<uuid:member_id>/consent/", views.member_consent),
    path("progress/<int:entry_id>/photo/", views.progress_photo),
    path("diet/foods/", views.diet_foods),
    path("diet/starters/", views.diet_starters),
    path("diet/templates/", views.diet_templates),
    path("diet/calculate/", views.diet_calculate),
    path("diet/plans/", views.diet_plans),
    path("diet/plans/<int:plan_id>/", views.diet_plan_detail),
    path("diet/plans/<int:plan_id>/share/", views.diet_plan_share),
    path("support/", views.support),
]

if settings.ENABLE_TEST_CONSOLE:
    # http://127.0.0.1:8000/api/v1/test-console/ops/
    urlpatterns += [path("test-console/ops/", views.test_console)]
