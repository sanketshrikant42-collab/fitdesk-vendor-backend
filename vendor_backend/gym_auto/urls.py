from django.urls import path

from . import views

urlpatterns = [
    path("reminders/", views.reminders),
    path("reminders/older/", views.reminders_older),
    path("reminders/run/", views.reminders_run),
    path("reminders/settings/", views.reminder_settings),
    path("reminders/<int:reminder_id>/email/", views.reminder_email),
    path("reminders/<int:reminder_id>/<slug:action>/", views.reminder_action),
    path("summary/", views.owner_summary),
    path("reports/<slug:name>/", views.report),
    path("reports/<slug:name>/export/", views.report_export),
    path("bills/<uuid:bill_id>/receipt.pdf", views.receipt_pdf),
    path("bills/<uuid:bill_id>/receipt-link/", views.receipt_link),
    path("receipts/<str:token>/<str:name>", views.receipt_shared),
    path("diet/plans/<int:plan_id>/pdf/", views.diet_pdf),
    path("diet/plans/<int:plan_id>/pdf-link/", views.diet_pdf_link),
    path("d/<str:token>/<str:name>", views.diet_pdf_shared),
    path("email/settings/", views.email_settings),
    path("email/test/", views.email_test),
    path("email/log/", views.email_log),
    path("bills/<uuid:bill_id>/email/", views.receipt_email),
    path("diet/plans/<int:plan_id>/email/", views.diet_email),
]
