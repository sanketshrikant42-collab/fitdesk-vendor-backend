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
]
