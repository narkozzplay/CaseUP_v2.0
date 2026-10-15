from django.db import migrations


def backfill(apps, schema_editor):
    from modules.v2.business.revenue import sync

    sync(
        ledger_model=apps.get_model("profiles", "LedgerEntry"),
        revenue_model=apps.get_model("business", "RevenueEntry"),
        subscription_model=apps.get_model("subscriptions", "UserSubscription"),
        order_model=apps.get_model("tradehub", "SaleOrder"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("business", "0001_initial"),
        ("profiles", "0017_ledger_referral_promo_bonus"),
        ("subscriptions", "0008_seed_store_full_access_feature"),
        ("tradehub", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
