from django.db import migrations

# Renamed menu items: (key, old label, new label). Only labels still at the old default are changed,
# so a label an admin edited by hand stays as it is.
RENAMES_RU = (
    ("steam_investments", "Steam Investments", "Портфель"),
    ("celery_tasks", "Celery Tasks", "Автообновление цен"),
    ("subscription_pricing", "Subscription Pricing", "Цены подписок"),
)
RENAMES_EN = (
    ("steam_investments", "Steam Investments", "Portfolio"),
    ("celery_tasks", "Celery Tasks", "Price auto-update"),
    ("subscription_pricing", "Subscription Pricing", "Subscription pricing"),
)


def rename(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    for key, old, new in RENAMES_RU:
        DashboardMenuItem.objects.filter(key=key, label_ru=old).update(label_ru=new)
    for key, old, new in RENAMES_EN:
        DashboardMenuItem.objects.filter(key=key, label_en=old).update(label_en=new)


def unrename(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    for key, old, new in RENAMES_RU:
        DashboardMenuItem.objects.filter(key=key, label_ru=new).update(label_ru=old)
    for key, old, new in RENAMES_EN:
        DashboardMenuItem.objects.filter(key=key, label_en=new).update(label_en=old)


class Migration(migrations.Migration):
    dependencies = [("system_config", "0013_site_legal_owner")]

    operations = [migrations.RunPython(rename, unrename)]
