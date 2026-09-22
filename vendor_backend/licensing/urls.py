from django.urls import path

from . import views


urlpatterns = [

    # Existing URLs
    path(
        "validate-license/",
        views.validate_license,
        name="validate_license"
    ),

    path(
        "devices/activate/",
        views.activate_device,
        name="activate_device"
    ),

    path(
        "submit-query/",
        views.submit_query,
        name="submit_query"
    ),

    # NEW
    path(
        "support-queries/",
        views.get_support_queries,
        name="get_support_queries"
    ),

    path(
        "send-whatsapp-receipt/",
        views.send_whatsapp_receipt,
        name="send_whatsapp_receipt"
    ),

    path(
        "whatsapp-status/",
        views.whatsapp_status,
        name="whatsapp_status"
    ),

    path(
        "whatsapp/connect/start/",
        views.whatsapp_connect_start,
        name="whatsapp_connect_start"
    ),

    path(
        "whatsapp/connect/callback/",
        views.whatsapp_connect_callback,
        name="whatsapp_connect_callback"
    ),

    path(
        "whatsapp/disconnect/",
        views.whatsapp_disconnect,
        name="whatsapp_disconnect"
    ),
]