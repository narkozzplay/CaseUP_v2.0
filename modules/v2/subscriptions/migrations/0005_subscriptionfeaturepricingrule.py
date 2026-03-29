from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


SITE_RULES = {
    "steam_catalog_view": ("level_1_viewer", Decimal("0.00"), 10),
    "steam_catalog_deep_analytics": ("level_2_analytics", Decimal("129.00"), 20),
    "tg_notifier_user": ("level_3_notifier", Decimal("89.00"), 30),
    "steam_trading_advanced_analytics": ("level_4_investor", Decimal("139.00"), 40),
    "premium_future_access": ("level_5_pro", Decimal("59.00"), 50),
}

FARMER_RULES = {
    "farm_analytics_deep": ("farmer_start", Decimal("129.00"), 110),
    "farm_tg_alerts": ("farmer_pro", Decimal("89.00"), 120),
    "farm_caseup_reduced_commission": ("farmer_pro", Decimal("0.00"), 130),
    "farm_sales_advanced_analytics": ("farmer_pro", Decimal("139.00"), 140),
    "farm_bonus_program": ("farmer_ultra", Decimal("79.00"), 150),
    "farm_enhanced_benefits": ("farmer_ultra", Decimal("49.00"), 160),
}


def seed_feature_pricing_rules(apps, schema_editor):
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    SubscriptionFeaturePricingRule = apps.get_model("subscriptions", "SubscriptionFeaturePricingRule")

    for family, rules in (("site", SITE_RULES), ("farmer", FARMER_RULES)):
        for feature_code, (plan_code, value_rub, sort_order) in rules.items():
            feature = SubscriptionFeature.objects.get(code=feature_code)
            plan = SubscriptionPlan.objects.get(code=plan_code)
            SubscriptionFeaturePricingRule.objects.update_or_create(
                feature=feature,
                family=family,
                defaults={
                    "required_plan": plan,
                    "value_rub": value_rub,
                    "is_enabled": True,
                    "sort_order": sort_order,
                },
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0004_seed_farmer_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionFeaturePricingRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("family", models.CharField(choices=[("site", "Site"), ("farmer", "Farmer")], db_index=True, max_length=16)),
                ("value_rub", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("feature", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pricing_rules", to="subscriptions.subscriptionfeature")),
                ("required_plan", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="feature_pricing_rules", to="subscriptions.subscriptionplan")),
            ],
            options={
                "verbose_name": "Subscription feature pricing rule",
                "verbose_name_plural": "Subscription feature pricing rules",
                "ordering": ("family", "sort_order", "id"),
            },
        ),
        migrations.AddIndex(
            model_name="subscriptionfeaturepricingrule",
            index=models.Index(fields=["family", "is_enabled", "sort_order"], name="sub_feat_rule_fam_enabled_idx"),
        ),
        migrations.AddConstraint(
            model_name="subscriptionfeaturepricingrule",
            constraint=models.UniqueConstraint(fields=("feature", "family"), name="sub_feat_rule_feature_family_uniq"),
        ),
        migrations.RunPython(seed_feature_pricing_rules, noop),
    ]
