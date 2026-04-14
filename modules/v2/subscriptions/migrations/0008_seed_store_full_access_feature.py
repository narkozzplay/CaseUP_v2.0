from django.db import migrations


def seed_store_full_access_feature(apps, schema_editor):
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")

    feature, _created = SubscriptionFeature.objects.update_or_create(
        code="site_store_full_access",
        defaults={
            "name": "Store · Full access",
            "description": "Подписочный доступ ко всем опубликованным store-товарам, участвующим в подписке.",
            "module_key": "store",
            "is_active": True,
            "sort_order": 55,
        },
    )

    plan = SubscriptionPlan.objects.filter(code="level_5_pro", family="site").first()
    if plan is None:
        return

    plan.features.add(feature)

    ui_features = list(plan.ui_features or [])
    ui_line = "Store full access для участвующих товаров"
    if ui_line not in ui_features:
        ui_features.append(ui_line)
        plan.ui_features = ui_features
        plan.save(update_fields=["ui_features", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0007_usersubscription_family_active_unique"),
    ]

    operations = [
        migrations.RunPython(seed_store_full_access_feature, migrations.RunPython.noop),
    ]
