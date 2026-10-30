from django.apps import AppConfig


class GymCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gym_core"
    verbose_name = "Gym data (members, plans, billing, check-in)"
