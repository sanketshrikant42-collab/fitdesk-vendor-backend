"""Data migration -- no schema changes.

Three things happen here, all additive/backfill, none destructive:

1. Seeds the Feature catalog (the 9 confirmed feature codes mapped to
   real existing modules, plus 3 provisioned-but-not-yet-enforced codes
   -- see the Phase 2 design doc's open questions).
2. Seeds Basic/Standard/Premium as illustrative starting plans (easy to
   edit in the admin afterwards -- these are a reasonable default, not a
   locked business decision) PLUS a "Legacy Full Access" plan that is
   never shown as something to *sell* -- it exists purely so step 3
   below has something safe to assign.
3. Every EXISTING License (this only ever runs once, at the moment this
   migration is first applied) gets assigned to "Legacy Full Access" and
   keeps every feature it implicitly had before this migration -- so no
   currently-active customer (e.g. P&P Fitness) loses anything the
   instant this deploys. It also converts each License's legacy
   `machine_id` scalar into a matching Device row, so an already-bound
   machine keeps validating normally under the new Device model.

Reversing this migration intentionally does NOT delete the created
Plan/Feature/Device rows -- by the time anyone would reverse it, real
admin edits and real Device activity may already reference them, and
deleting that silently on a rollback is worse than leaving inert rows
behind. Re-running forward is idempotent (get_or_create everywhere), so
this is safe to re-apply if needed.
"""

from django.db import migrations


CONFIRMED_FEATURES = [
    ("members", "Members", "Member records and profiles."),
    ("memberships", "Memberships", "Membership plans and transactions."),
    ("trainers", "Trainers", "Trainer profiles, schedules, and payouts."),
    ("staff", "Staff", "Staff records, attendance, and payroll."),
    ("attendance", "Attendance", "Member and staff attendance tracking."),
    ("expenses", "Expenses", "Expense tracking."),
    ("billing", "Billing", "Billing and receipt generation."),
    ("reports", "Reports & Analytics", "Reports and analytics dashboard."),
    ("whatsapp", "WhatsApp Receipts", "Sending receipts via WhatsApp Business API."),
]

# Provisioned so the Plan/Feature schema is ready to sell these, but NOT
# yet wired into any FeatureManager.is_enabled() check in the desktop app
# -- see the Phase 2 design doc's open questions (advanced_analytics and
# advanced_receipts have no existing "basic vs advanced" split in the
# code to gate; multi_user's exact per-plan limit is undecided).
PROVISIONED_FEATURES = [
    ("multi_user", "Multiple Users", "Allow more than one staff login account. Enforcement pending a decided per-plan limit."),
    ("advanced_analytics", "Advanced Analytics", "Deeper reporting/trend views. Not yet split out from the base Reports module."),
    ("advanced_receipts", "Advanced Receipt Branding", "Richer receipt branding/customization. Not yet split out from the base Billing module."),
]

ALL_FEATURE_CODES = [code for code, _, _ in CONFIRMED_FEATURES + PROVISIONED_FEATURES]

# Illustrative starting tiers -- easy to change in the admin afterwards,
# not a locked business decision. Matches the shape from the original
# licensing brief (Basic -> Standard -> Premium, WhatsApp as a
# paid add-on rather than automatically bundled into Standard).
PLAN_DEFS = [
    ("basic", "Basic", ["members", "memberships", "billing"]),
    ("standard", "Standard", ["members", "memberships", "billing", "trainers", "staff", "attendance", "expenses"]),
    ("premium", "Premium", ALL_FEATURE_CODES),
]

LEGACY_PLAN_ID = "legacy_full_access"


def seed_and_backfill(apps, schema_editor):
    Feature = apps.get_model("licensing", "Feature")
    Plan = apps.get_model("licensing", "Plan")
    PlanFeature = apps.get_model("licensing", "PlanFeature")
    License = apps.get_model("licensing", "License")
    Device = apps.get_model("licensing", "Device")

    feature_objs = {}
    for code, name, description in CONFIRMED_FEATURES + PROVISIONED_FEATURES:
        feature_objs[code], _ = Feature.objects.get_or_create(
            code=code, defaults={"name": name, "description": description}
        )

    for plan_id, name, codes in PLAN_DEFS:
        plan, _ = Plan.objects.get_or_create(plan_id=plan_id, defaults={"name": name})
        for code in codes:
            PlanFeature.objects.get_or_create(plan=plan, feature=feature_objs[code])

    legacy_plan, _ = Plan.objects.get_or_create(
        plan_id=LEGACY_PLAN_ID,
        defaults={
            "name": "Legacy Full Access",
            "description": (
                "Auto-assigned to every license that existed before the "
                "feature-entitlement system shipped, so no existing "
                "customer lost access. Not intended to be sold as a plan "
                "going forward -- move a real customer to Basic/Standard/"
                "Premium (or a custom set of overrides) once you've "
                "confirmed their actual tier."
            ),
        },
    )
    for code in ALL_FEATURE_CODES:
        PlanFeature.objects.get_or_create(plan=legacy_plan, feature=feature_objs[code])

    for lic in License.objects.filter(plan__isnull=True):
        lic.plan_id = legacy_plan.id
        # Preserve whatever max_activations was already set to (the old,
        # never-enforced field) as the new, enforced max_devices, rather
        # than silently resetting every existing license to 1.
        lic.max_devices = max(1, lic.max_activations or 1)
        lic.save(update_fields=["plan", "max_devices"])

        if lic.machine_id:
            Device.objects.get_or_create(
                license=lic,
                fingerprint=lic.machine_id,
                defaults={
                    "device_name": "",
                    "status": "active",
                    "last_seen_at": lic.last_check_in,
                },
            )


def noop_reverse(apps, schema_editor):
    # Deliberately does not undo the seeding/backfill -- see module
    # docstring. If you need to fully undo this, do it by hand in the
    # admin/shell with an understanding of what's since been built on it.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("licensing", "0004_feature_plan_license_max_devices_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_and_backfill, noop_reverse),
    ]
