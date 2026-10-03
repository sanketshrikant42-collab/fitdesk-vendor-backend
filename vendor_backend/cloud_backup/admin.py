from django.contrib import admin, messages

from .models import CloudBackup, GymBackupKey
from . import services
from .storage import StorageError, get_storage


@admin.register(CloudBackup)
class CloudBackupAdmin(admin.ModelAdmin):
    list_display = (
        "client", "file_name", "backup_type", "status", "file_size",
        "backup_created_at", "verified_at", "last_error",
    )
    list_filter = ("status", "backup_type")
    search_fields = ("client__gym_name", "file_name", "backup_id")
    date_hierarchy = "backup_created_at"
    # Metadata only -- the admin never offers to download/decrypt a gym's
    # backup. Records are created by the API, never by hand.
    readonly_fields = [f.name for f in CloudBackup._meta.fields]
    actions = ["run_retention_for_gyms"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting the row here would orphan the R2 object; use the
        # retention/maintenance flow instead.
        return False

    @admin.action(description="Run retention now for the selected backups' gyms")
    def run_retention_for_gyms(self, request, queryset):
        try:
            storage = get_storage()
        except StorageError as exc:
            self.message_user(request, f"Storage unavailable: {exc.code}", messages.ERROR)
            return
        total = {"deleted": 0, "delete_failed": 0}
        for client in {b.client for b in queryset.select_related("client")}:
            r = services.apply_retention(client, storage=storage)
            total["deleted"] += r["deleted"]
            total["delete_failed"] += r["delete_failed"]
        self.message_user(request, f"Retention: {total['deleted']} deleted, {total['delete_failed']} failed (will retry).")


@admin.register(GymBackupKey)
class GymBackupKeyAdmin(admin.ModelAdmin):
    # key_encrypted is deliberately NOT displayed anywhere.
    list_display = ("client", "key_id", "created_at")
    fields = ("client", "key_id", "created_at")
    readonly_fields = ("client", "key_id", "created_at")
    search_fields = ("client__gym_name", "key_id")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting a gym key makes every existing cloud backup of that gym
        # permanently unreadable.
        return False
