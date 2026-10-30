from django.urls import path

from . import views

urlpatterns = [
    # Sign-in (no token)
    path("auth/owner-setup/", views.owner_setup),
    path("auth/join/", views.join),
    path("auth/login/", views.login),
    path("auth/refresh/", views.refresh),
    path("auth/device-status/", views.device_status),
    path("auth/pin-users/", views.pin_users),
    path("auth/pin-switch/", views.pin_switch),
    path("auth/logout/", views.logout),

    # Signed-in user
    path("me/", views.me),
    path("me/password/", views.change_password),
    path("me/pin/", views.set_pin),

    # Owner: Team & Access
    path("team/summary/", views.team_summary),
    path("team/users/", views.team_users),
    path("team/users/<int:user_id>/", views.team_user_detail),
    path("team/users/<int:user_id>/update/", views.team_user_update),
    path("team/users/<int:user_id>/disable/", views.team_user_disable),
    path("team/users/<int:user_id>/enable/", views.team_user_enable),
    path("team/users/<int:user_id>/reset-password/", views.team_user_reset_password),
    path("team/users/<int:user_id>/sign-out-everywhere/", views.team_user_sign_out),
    path("team/users/<int:user_id>/salary-access/", views.team_user_salary_access),
    path("team/users/<int:user_id>/permissions/", views.team_user_permissions),
    path("team/invites/", views.team_invites),
    path("team/invites/<int:invite_id>/cancel/", views.team_invite_cancel),
    path("team/devices/", views.team_devices),
    path("team/devices/<int:device_id>/<slug:action>/", views.team_device_action),
    path("team/activity/", views.team_activity),
]

from django.conf import settings  # noqa: E402

if settings.ENABLE_TEST_CONSOLE:
    # Local testing page: http://127.0.0.1:8000/api/v1/test-console/
    urlpatterns += [path("test-console/", views.test_console)]
