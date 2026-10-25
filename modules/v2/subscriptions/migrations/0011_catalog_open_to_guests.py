from django.db import migrations


def open_to_guests(apps, schema_editor):
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    SubscriptionFeature.objects.filter(code="steam_catalog_view").update(free_tier="guest")


def back_to_registered(apps, schema_editor):
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    SubscriptionFeature.objects.filter(code="steam_catalog_view").update(free_tier="registered")


class Migration(migrations.Migration):
    """The catalog, item pages and price charts are open without signing in (IDEA-011, step 3)."""

    dependencies = [
        ("subscriptions", "0010_new_plan_lineup"),
    ]

    operations = [
        migrations.RunPython(open_to_guests, back_to_registered),
    ]
