from django.contrib import admin

from .models import Bill, CheckIn, GymSettings, Member, Membership, Payment, Plan, Trainer


@admin.register(GymSettings)
class GymSettingsAdmin(admin.ModelAdmin):
    list_display = ("gym_name", "client", "gst_percent", "upi_id", "receipt_prefix")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "duration_days", "fee", "is_personal_training", "members_covered", "status")
    list_filter = ("status", "is_personal_training")


@admin.register(Trainer)
class TrainerAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "client", "phone", "status", "staff_user")


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "client", "phone", "is_archived", "created_at")
    search_fields = ("code", "name", "phone", "client__gym_name")


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = [f.name for f in Payment._meta.fields]
    can_delete = False


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("receipt_no", "client", "member", "plan", "total", "status", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("receipt_no", "member__name", "client__gym_name")
    inlines = [PaymentInline]
    readonly_fields = [f.name for f in Bill._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("member", "plan", "start_date", "end_date", "status")


@admin.register(CheckIn)
class CheckInAdmin(admin.ModelAdmin):
    list_display = ("member", "client", "checked_in_at", "source", "by_user")
    list_filter = ("source",)
