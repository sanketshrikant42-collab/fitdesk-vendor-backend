from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .core import BackupError, open_blob, run_backup, target
from .models import ServerBackup


@admin.register(ServerBackup)
class ServerBackupAdmin(admin.ModelAdmin):
    """History of server backups. 'Back up now' and 'Check it opens' work
    without a shell, so they can be used on Render's free plan too."""
    list_display = ("started_at", "reason", "status", "size_kb", "gyms", "members", "bills", "removed_old", "key")
    list_filter = ("status", "reason")
    readonly_fields = [f.name for f in ServerBackup._meta.fields]
    actions = ["check_opens"]
    change_list_template = "admin/db_backup/serverbackup/change_list.html"

    def size_kb(self, o):
        return f"{o.size_bytes / 1024:.0f} KB"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path("run/", self.admin_site.admin_view(self.run_now), name="db_backup_run")] + super().get_urls()

    def run_now(self, request):
        if request.method == "POST":
            try:
                rec = run_backup(reason="manual")
                self.message_user(request, f"Backup made: {rec.key} ({rec.size_bytes / 1024:.0f} KB, "
                                           f"{rec.gyms} gyms, {rec.members} members).")
            except Exception as e:
                self.message_user(request, f"Backup failed: {e}", messages.ERROR)
        return redirect("..")

    @admin.action(description="Check the selected backups can be opened")
    def check_opens(self, request, qs):
        t = target()
        for rec in qs.exclude(key=""):
            try:
                manifest, data = open_blob(t.get(rec.key))
                self.message_user(request, f"{rec.key}: opens fine · {len(data)} rows · gyms: "
                                           + ", ".join(g["name"] for g in manifest["gyms"]))
            except (BackupError, Exception) as e:
                self.message_user(request, f"{rec.key}: {e}", messages.ERROR)
