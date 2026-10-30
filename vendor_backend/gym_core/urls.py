from django.urls import path

from . import views

urlpatterns = [
    path("gym/settings/", views.gym_settings),
    path("gym/logo.png", views.gym_logo),
    path("gym/settings/logo/", views.gym_logo_upload),
    path("gym/settings/logo/remove/", views.gym_logo_remove),
    path("dashboard/", views.dashboard),
    path("plans/", views.plans),
    path("plans/<int:plan_id>/update/", views.plan_update),
    path("plans/<int:plan_id>/archive/", views.plan_archive),
    path("trainers/", views.trainers),
    path("trainers/<int:trainer_id>/", views.trainer_detail),
    path("trainers/<int:trainer_id>/update/", views.trainer_update),
    path("trainers/<int:trainer_id>/remove/", views.trainer_remove),
    path("members/", views.members),
    path("members/<uuid:member_id>/", views.member_detail),
    path("members/<uuid:member_id>/update/", views.member_update),
    path("members/<uuid:member_id>/fitness/", views.member_fitness),
    path("members/<uuid:member_id>/archive/", views.member_archive),
    path("members/<uuid:member_id>/records/", views.member_records),
    path("members/<uuid:member_id>/delete/", views.member_delete),
    path("members/<uuid:member_id>/merge/", views.member_merge),
    path("bills/quote/", views.bill_quote),
    path("bills/", views.bills),
    path("bills/<uuid:bill_id>/", views.bill_detail),
    path("bills/<uuid:bill_id>/payments/", views.bill_payment),
    path("bills/<uuid:bill_id>/cancel/", views.bill_cancel),
    path("bills/<uuid:bill_id>/receipt/", views.bill_receipt),
    path("memberships/<uuid:membership_id>/freeze/", views.membership_freeze),
    path("memberships/<uuid:membership_id>/freezes/<int:freeze_id>/end/", views.membership_unfreeze),
    path("memberships/<uuid:membership_id>/sessions/", views.membership_session),
    path("checkins/lookup/", views.checkin_lookup),
    path("checkins/", views.checkins),
]

from django.conf import settings  # noqa: E402

if settings.ENABLE_TEST_CONSOLE:
    # http://127.0.0.1:8000/api/v1/test-console/gym/
    urlpatterns += [path("test-console/gym/", views.test_console),
                    path("test-console/app/", views.test_app)]
