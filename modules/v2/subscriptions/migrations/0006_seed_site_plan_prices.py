from decimal import Decimal

from django.db import migrations


SITE_PRICE_BY_CODE = {
    "level_1_viewer": Decimal("99.00"),
    "level_2_analytics": Decimal("149.00"),
    "level_3_notifier": Decimal("239.00"),
    "level_4_investor": Decimal("329.00"),
    "level_5_pro": Decimal("399.00"),
}


def seed_site_plan_prices(apps, schema_editor):
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    for code, price in SITE_PRICE_BY_CODE.items():
        SubscriptionPlan.objects.filter(code=code, family="site").update(price_rub=price)


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0005_subscriptionfeaturepricingrule"),
    ]

    operations = [
        migrations.RunPython(seed_site_plan_prices, migrations.RunPython.noop),
    ]
