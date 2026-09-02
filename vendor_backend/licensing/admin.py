from django.contrib import admin
from .models import Client, License, SupportQuery


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "gym_name",
        "owner_name",
        "phone",
        "email",
        "city",
        "created_at",
    )

    search_fields = (
        "gym_name",
        "owner_name",
        "phone",
        "email",
    )


@admin.action(description="Reset selected license machine ID")
def reset_machine_id(modeladmin, request, queryset):

    updated = queryset.update(
        machine_id=""
    )

    modeladmin.message_user(
        request,
        f"{updated} license(s) machine ID reset successfully."
    )


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):

    list_display = (
        "key",
        "client",
        "status",
        "expiry_date",
        "machine_id",
        "last_check_in",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "key",
        "client__gym_name",
        "machine_id",
    )

    readonly_fields = (
        "issued_at",
        "last_check_in",
    )

    actions = [
        reset_machine_id,
    ]


@admin.register(SupportQuery)
class SupportQueryAdmin(admin.ModelAdmin):

    list_display = (
        "subject",
        "client",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "subject",
        "client__gym_name",
    )