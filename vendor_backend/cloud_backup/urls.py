from django.urls import path

from . import views

# Mounted under /api/backups/ (see vendor_backend/urls.py). Same POST +
# license_key/machine_id-in-body convention as the existing /api/ routes.
urlpatterns = [
    path("status/", views.cloud_backup_status, name="cloud_backup_status"),
    path("key/", views.backup_key, name="cloud_backup_key"),
    path("request-upload/", views.request_upload, name="cloud_backup_request_upload"),
    path("complete-upload/", views.complete_upload, name="cloud_backup_complete_upload"),
    path("list/", views.list_backups, name="cloud_backup_list"),
    path("request-download/", views.request_download, name="cloud_backup_request_download"),
    path("verify/", views.verify_backup, name="cloud_backup_verify"),
    path("delete/", views.delete_backup, name="cloud_backup_delete"),
]
