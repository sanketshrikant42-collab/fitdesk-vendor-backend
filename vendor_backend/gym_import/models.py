"""A record of each import from a FitDesk v1.2.0 database (Phase 4)."""
from django.db import models

from licensing.models import Client


class ImportRun(models.Model):
    """One import of a v1.2.0 database into a gym. A gym created by the
    importer can be wiped and imported again (--replace); a gym that wasn't
    created by the importer never can."""
    client = models.ForeignKey(Client, null=True, on_delete=models.SET_NULL, related_name="import_runs")
    created_gym = models.BooleanField(default=True, help_text="The importer created this gym (so --replace may delete it).")
    source_path = models.CharField(max_length=500)
    source_sha256 = models.CharField(max_length=64)
    source_gym_name = models.CharField(max_length=200, blank=True)
    ok = models.BooleanField(default=False)
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
