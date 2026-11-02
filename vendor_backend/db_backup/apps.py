from django.apps import AppConfig


class DbBackupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "db_backup"
    verbose_name = "Server backups"
