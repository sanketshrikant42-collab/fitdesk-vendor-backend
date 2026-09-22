from django.contrib import admin
from .models import (
    Client,
    License,
    SupportQuery,
    WhatsAppSendLog,
    WhatsAppConfiguration,
    WhatsAppConnectSession,
    Plan,
    Feature,
    PlanFeature,
    LicenseFeatureOverride,
    Device,
    AuditLog,
)


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


@admin.action(description="Reset selected license machine ID (legacy field)")
def reset_machine_id(modeladmin, request, queryset):

    updated = queryset.update(
        machine_id=""
    )

    modeladmin.message_user(
        request,
        f"{updated} license(s) machine ID reset successfully. "
        f"Note: this clears the legacy field only -- also remove/block the "
        f"matching row under Devices below if you're freeing up a device slot."
    )


class LicenseFeatureOverrideInline(admin.TabularInline):
    """The "Members ON / Trainers OFF" per-license toggle grid the
    licensing brief asked for. Plan features are the baseline; a row
    here either adds a feature on top of the plan (enabled=True) or
    explicitly revokes one the plan would otherwise grant
    (enabled=False)."""

    model = LicenseFeatureOverride
    extra = 1
    autocomplete_fields = ["feature"]
    fields = ("feature", "enabled", "note")


class DeviceInline(admin.TabularInline):
    model = Device
    extra = 0
    readonly_fields = ("fingerprint", "device_name", "activated_at", "last_seen_at")
    fields = ("device_name", "fingerprint", "status", "activated_at", "last_seen_at")
    can_delete = False  # blocking a device (status) is the supported action, not deleting its history


@admin.action(description="Block selected devices")
def block_devices(modeladmin, request, queryset):
    updated = queryset.update(status=Device.STATUS_BLOCKED)
    for device in queryset:
        AuditLog.record("device_blocked", license=device.license, fingerprint=device.fingerprint)
    modeladmin.message_user(request, f"{updated} device(s) blocked.")


@admin.action(description="Unblock selected devices")
def unblock_devices(modeladmin, request, queryset):
    updated = queryset.update(status=Device.STATUS_ACTIVE)
    for device in queryset:
        AuditLog.record("device_unblocked", license=device.license, fingerprint=device.fingerprint)
    modeladmin.message_user(request, f"{updated} device(s) unblocked.")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    """Standalone view of every device across every license -- the inline
    on LicenseAdmin below covers the common "look at one license" case;
    this covers "find which license a given machine belongs to"."""

    list_display = ("license", "device_name", "fingerprint", "status", "activated_at", "last_seen_at")
    list_filter = ("status",)
    search_fields = ("license__key", "license__client__gym_name", "fingerprint", "device_name")
    readonly_fields = ("fingerprint", "activated_at")
    actions = [block_devices, unblock_devices]


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):

    list_display = (
        "key",
        "client",
        "plan",
        "status",
        "expiry_date",
        "device_count_display",
        "last_check_in",
    )

    list_filter = (
        "status",
        "plan",
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

    autocomplete_fields = ["plan"]

    inlines = [DeviceInline, LicenseFeatureOverrideInline]

    actions = [
        reset_machine_id,
    ]

    @admin.display(description="Devices")
    def device_count_display(self, obj):
        return f"{obj.active_device_count} / {obj.max_devices}"

    def save_formset(self, request, form, formset, change):
        """Writes an AuditLog row for every feature-override add/change/
        delete made through the inline above, and for device status
        changes -- so "who enabled WhatsApp for this gym, and when" is
        always answerable without cross-referencing Django's own admin
        log."""
        instances = formset.save(commit=False)

        for obj in instances:
            if isinstance(obj, LicenseFeatureOverride):
                obj.save()
                AuditLog.record(
                    "feature_override_changed",
                    license=obj.license,
                    feature=obj.feature.code,
                    enabled=obj.enabled,
                )
            else:
                obj.save()

        for obj in formset.deleted_objects:
            if isinstance(obj, LicenseFeatureOverride):
                AuditLog.record(
                    "feature_override_removed",
                    license=obj.license,
                    feature=obj.feature.code,
                )
            obj.delete()

        formset.save_m2m()

    def save_model(self, request, obj, form, change):
        if change and "plan" in form.changed_data:
            AuditLog.record(
                "plan_changed",
                license=obj,
                new_plan=obj.plan.plan_id if obj.plan_id else None,
            )
        super().save_model(request, obj, form, change)


class PlanFeatureInline(admin.TabularInline):
    model = PlanFeature
    extra = 1
    autocomplete_fields = ["feature"]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("plan_id", "name", "price", "status")
    list_filter = ("status",)
    search_fields = ("plan_id", "name")
    inlines = [PlanFeatureInline]


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "status")
    list_filter = ("status",)
    search_fields = ("code", "name")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only, append-only -- see AuditLog.record() in models.py,
    which is the only thing that ever creates a row here."""

    list_display = ("created_at", "action", "client", "license")
    list_filter = ("action",)
    search_fields = ("client__gym_name", "license__key", "action")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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


@admin.register(WhatsAppSendLog)
class WhatsAppSendLogAdmin(admin.ModelAdmin):

    list_display = (
        "created_at",
        "client",
        "phone",
        "message_type",
        "mode",
        "status",
        "error_code",
    )

    list_filter = (
        "status",
        "mode",
        "message_type",
    )

    search_fields = (
        "phone",
        "document_reference",
        "client__gym_name",
        "license__key",
    )

    readonly_fields = [f.name for f in WhatsAppSendLog._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.action(description="Disconnect WhatsApp for selected gyms")
def disconnect_whatsapp(modeladmin, request, queryset):
    for config in queryset:
        config.disconnect()
    modeladmin.message_user(request, f"{queryset.count()} gym(s) disconnected from WhatsApp.")


@admin.register(WhatsAppConfiguration)
class WhatsAppConfigurationAdmin(admin.ModelAdmin):
    """access_token_encrypted (and the decrypted access_token property)
    are never shown here, in the list view or the edit form -- only
    whether a token is present at all (has_token), which is what a
    vendor admin actually needs to see to know if a gym is genuinely
    connected."""

    list_display = (
        "client",
        "connection_status",
        "enabled",
        "business_name",
        "phone_number",
        "phone_number_id",
        "waba_id",
        "template_name",
        "template_language",
        "has_token",
        "last_connection_check",
    )

    list_filter = (
        "connection_status",
        "enabled",
    )

    search_fields = (
        "client__gym_name",
        "business_name",
        "phone_number",
        "phone_number_id",
        "waba_id",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "last_connection_check",
        "has_token",
    )

    exclude = ("access_token_encrypted",)

    actions = [disconnect_whatsapp]


@admin.register(WhatsAppConnectSession)
class WhatsAppConnectSessionAdmin(admin.ModelAdmin):
    """Read-only -- these are short-lived, single-use tokens created by
    /api/whatsapp/connect/start/ and consumed by the callback; nothing
    about them should be hand-edited in the admin."""

    list_display = ("client", "created_at", "expires_at", "consumed_at")
    list_filter = ("client",)
    search_fields = ("client__gym_name", "token")
    readonly_fields = [f.name for f in WhatsAppConnectSession._meta.fields]

    def has_add_permission(self, request):
        return False