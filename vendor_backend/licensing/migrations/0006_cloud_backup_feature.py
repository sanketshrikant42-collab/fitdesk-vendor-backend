"""Data migration -- adds the `cloud_backup` feature code.

Additive only. Grants it to:
  * "premium" (cloud backup is a premium capability), and
  * "legacy_full_access" -- every license that existed before the
    feature-entitlement layer (e.g. P&P Fitness) keeps "everything", which
    now includes cloud backup.
Basic/Standard licenses get it via a LicenseFeatureOverride (admin ->
License -> feature overrides) if sold as an add-on.

LOCAL backups on the desktop never depend on this feature -- only the
cloud upload does.

Reverse is a no-op (same policy as 0005): by the time anyone reverses
this, real overrides/backups may reference the feature.
"""

from django.db import migrations

FEATURE = ("cloud_backup", "Cloud Backup", "Encrypted daily backup of the gym database to FitDesk Pro cloud storage (Cloudflare R2), with cloud restore.")
PLANS_WITH_FEATURE = ["premium", "legacy_full_access"]


def add_feature(apps, schema_editor):
    Feature = apps.get_model("licensing", "Feature")
    Plan = apps.get_model("licensing", "Plan")
    PlanFeature = apps.get_model("licensing", "PlanFeature")

    code, name, description = FEATURE
    feature, _ = Feature.objects.get_or_create(code=code, defaults={"name": name, "description": description})
    for plan in Plan.objects.filter(plan_id__in=PLANS_WITH_FEATURE):
        PlanFeature.objects.get_or_create(plan=plan, feature=feature)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("licensing", "0005_seed_features_plans_and_backfill"),
    ]

    operations = [
        migrations.RunPython(add_feature, noop_reverse),
    ]
