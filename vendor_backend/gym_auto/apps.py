from django.apps import AppConfig


class GymAutoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gym_auto"
    verbose_name = "Gym automation & reports (reminders, owner summary, reports, exports)"
