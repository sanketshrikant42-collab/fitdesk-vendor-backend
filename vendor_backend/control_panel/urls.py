from django.urls import path

from . import views

urlpatterns = [
    path("", views.today_view),
    path("login/", views.login_view),
    path("logout/", views.logout_view),
    path("gyms/", views.gyms_view),
    path("gyms/new/", views.add_gym),
    path("gyms/new/done/<int:client_id>/", views.add_gym_done),
    path("gyms/<int:client_id>/", views.gyms_view),
    path("gyms/<int:client_id>/renew/", views.gym_renew),
    path("gyms/<int:client_id>/setup-code/", views.gym_setup_code),
    path("gyms/<int:client_id>/status/", views.gym_status),
    path("gyms/<int:client_id>/edit/", views.gym_edit),
    path("gyms/<int:client_id>/licence/", views.gym_licence),
    path("gyms/<int:client_id>/devices/<int:device_id>/", views.device_status),
    path("money/", views.money_view),
    path("money/remind/<int:client_id>/", views.money_remind),
    path("payments/<int:payment_id>/invoice.pdf", views.payment_invoice),
    path("support/", views.support_view),
    path("support/<int:query_id>/", views.support_view),
    path("support/<int:query_id>/act/", views.support_action),
    path("health/", views.health_view),
    path("health/tool/", views.health_tool),
]
