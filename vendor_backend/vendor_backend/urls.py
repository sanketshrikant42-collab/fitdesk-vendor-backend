"""
URL configuration for vendor_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

from licensing import views as licensing_views

def healthz(request):
    """Render's health check (Phase 1): the app is up AND can reach its
    database. Render only switches traffic to a new deploy once this
    answers 200, so a broken deploy never replaces a working one."""
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        return JsonResponse({"ok": True})
    except Exception:  # noqa: BLE001
        return JsonResponse({"ok": False}, status=503)


urlpatterns = [
    path('healthz/', healthz),
    path('admin/', admin.site.urls),
    path('api/', include('licensing.urls')),

    # Cloud Backup module (cloud_backup app) -- same /api/ prefix and
    # POST + license_key/machine_id convention as the licensing routes.
    path('api/backups/', include('cloud_backup.urls')),

    # New cross-platform apps: staff sign-in, roles, devices (gym_accounts).
    # Versioned so the legacy /api/ routes above never change shape.
    path('api/v1/', include('gym_accounts.urls')),
    path('api/v1/', include('gym_core.urls')),
    path('api/v1/', include('gym_ops.urls')),
    path('api/v1/', include('gym_auto.urls')),

    # NOT under /api/ -- this is a browser-rendered HTML page (Meta's
    # Embedded Signup JS SDK needs a real web page context), opened by
    # the desktop app in the user's system browser, not called by the
    # desktop app itself. See licensing/views.py's whatsapp_connect_page.
    path('whatsapp/connect/<str:token>/', licensing_views.whatsapp_connect_page, name='whatsapp_connect_page'),
]
