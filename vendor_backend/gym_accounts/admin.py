from django.contrib import admin, messages

from .models import GymProfile, Invite, OwnerSetupCode, StaffDevice, StaffSession, StaffUser, TeamActivity


@admin.register(GymProfile)
class GymProfileAdmin(admin.ModelAdmin):
    list_display = ("client", "gym_code", "max_users", "max_devices", "owner_free_devices", "created_at")
    search_fields = ("client__gym_name", "gym_code")
    readonly_fields = ("gym_code", "created_at")
    autocomplete_fields = ()


@admin.register(OwnerSetupCode)
class OwnerSetupCodeAdmin(admin.ModelAdmin):
    """Add one to give a gym owner their first sign-in. The code is shown
    once in the green message after saving; only a hash is stored."""

    list_display = ("client", "code_hint", "created_by", "created_at", "expires_at", "used_at")
    fields = ("client",)
    search_fields = ("client__gym_name",)

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        new, code = OwnerSetupCode.issue(obj.client, created_by=request.user.get_username())
        obj.pk = new.pk
        pretty = f"{code[:5]}-{code[5:]}"
        messages.success(request, f"Owner setup code for {obj.client.gym_name}: {pretty}. Valid {OwnerSetupCode.TTL_DAYS} days, "
                                  f"single use. Copy it now; it won't be shown again.")

    def log_addition(self, request, obj, message):
        pass

    def response_add(self, request, obj, post_url_continue=None):
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        return HttpResponseRedirect(reverse("admin:gym_accounts_ownersetupcode_changelist"))


@admin.register(StaffUser)
class StaffUserAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "role", "phone", "status", "last_login_at")
    list_filter = ("role", "status")
    search_fields = ("name", "phone", "client__gym_name")
    exclude = ("password_hash", "pin_hash")
    readonly_fields = ("client", "created_at", "last_login_at", "disabled_at")
    actions = ["sign_out_everywhere"]

    def has_add_permission(self, request):
        return False  # owners use a setup code; staff join by invite

    @admin.action(description="Sign out selected staff on all devices")
    def sign_out_everywhere(self, request, queryset):
        n = StaffSession.revoke_all("vendor_signed_out", user__in=queryset)
        self.message_user(request, f"{n} session(s) ended.")


@admin.register(StaffDevice)
class StaffDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "platform", "shared", "status", "registered_by", "last_seen_at")
    list_filter = ("status", "platform", "shared")
    search_fields = ("name", "client__gym_name", "installation_id")
    exclude = ("secret_hash",)
    readonly_fields = ("client", "installation_id", "registered_by", "decided_by", "decided_at", "created_at", "last_seen_at")

    def has_add_permission(self, request):
        return False


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "role", "phone", "status", "created_at", "expires_at")
    list_filter = ("status", "role")
    exclude = ("code_hash",)
    readonly_fields = [f.name for f in Invite._meta.fields if f.name != "code_hash"]

    def has_add_permission(self, request):
        return False


@admin.register(TeamActivity)
class TeamActivityAdmin(admin.ModelAdmin):
    list_display = ("created_at", "client", "actor_label", "action", "target")
    list_filter = ("action",)
    search_fields = ("client__gym_name", "actor_label", "target")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
