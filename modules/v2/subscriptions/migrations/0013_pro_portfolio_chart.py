from django.db import migrations

NEW_LINE = "Новое: график портфеля и недельная сводка в Telegram"
AFTER = "Аналитика портфеля «Инвестиции»"
FEATURE_DESCRIPTION = "Стоимость и прибыль портфеля «Инвестиции», график по дням и недельная сводка."
OLD_FEATURE_DESCRIPTION = "Расширенная аналитика портфеля «Инвестиции»."


def add_line(apps, schema_editor):
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    plan = SubscriptionPlan.objects.filter(code="site_pro").first()
    if plan is not None:
        features = list(plan.ui_features or [])
        if NEW_LINE not in features:
            # Right after the investments line, or at the top when an admin has reworded the list.
            index = features.index(AFTER) + 1 if AFTER in features else min(1, len(features))
            features.insert(index, NEW_LINE)
            plan.ui_features = features
            plan.save(update_fields=["ui_features"])
    SubscriptionFeature.objects.filter(code="steam_trading_advanced_analytics", description=OLD_FEATURE_DESCRIPTION).update(
        description=FEATURE_DESCRIPTION
    )


def remove_line(apps, schema_editor):
    SubscriptionPlan = apps.get_model("subscriptions", "SubscriptionPlan")
    SubscriptionFeature = apps.get_model("subscriptions", "SubscriptionFeature")
    plan = SubscriptionPlan.objects.filter(code="site_pro").first()
    if plan is not None and NEW_LINE in (plan.ui_features or []):
        plan.ui_features = [line for line in plan.ui_features if line != NEW_LINE]
        plan.save(update_fields=["ui_features"])
    SubscriptionFeature.objects.filter(code="steam_trading_advanced_analytics", description=FEATURE_DESCRIPTION).update(
        description=OLD_FEATURE_DESCRIPTION
    )


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0012_trial_and_auto_renew")]

    operations = [migrations.RunPython(add_line, remove_line)]
