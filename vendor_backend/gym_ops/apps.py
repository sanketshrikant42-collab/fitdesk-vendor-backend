from django.apps import AppConfig


class GymOpsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gym_ops"
    verbose_name = "Gym operations (staff, payroll, expenses, enquiries, diet, progress)"
