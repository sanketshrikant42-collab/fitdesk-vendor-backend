from django.db import models


class ServerBackup(models.Model):
    """One run of the nightly backup (or a manual one). The backup file
    itself lives in Cloudflare R2 (or the local backup folder on a PC)."""
    STATUS_RUNNING, STATUS_OK, STATUS_FAILED = "running", "ok", "failed"
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default=STATUS_RUNNING)
    reason = models.CharField(max_length=30, default="nightly", help_text="nightly / manual / before-restore")
    key = models.CharField(max_length=200, blank=True, help_text="Where the file is stored")
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    gyms = models.PositiveIntegerField(default=0)
    members = models.PositiveIntegerField(default=0)
    bills = models.PositiveIntegerField(default=0)
    rows = models.PositiveIntegerField(default=0)
    removed_old = models.PositiveIntegerField(default=0, help_text="Old backups deleted by the keep-30-days / 12-months rule")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.started_at:%d %b %Y %H:%M} UTC · {self.status}"
