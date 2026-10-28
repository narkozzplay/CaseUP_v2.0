from django.db import migrations

# Moved off the sidebar: they live on the dashboard («Бонусы») and on the CS2 stats page now.
HIDDEN_KEYS = ("referrals", "promo", "daily_bonus", "cs2_match_history")


def forwards(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key__in=HIDDEN_KEYS).update(is_enabled=False)
    DashboardMenuItem.objects.filter(key="steam_investments", label_ru="Портфель").update(label_ru="Инвестиции", label_en="Investments")
    DashboardMenuItem.objects.filter(key="steam_catalog", label_ru="Steam Catalog").update(label_ru="Каталог скинов")
    DashboardMenuItem.objects.filter(key="dashboard_preview", label_ru="Dashboard").update(label_ru="Панель")


def backwards(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key__in=HIDDEN_KEYS).update(is_enabled=True)
    DashboardMenuItem.objects.filter(key="steam_investments", label_ru="Инвестиции").update(label_ru="Портфель", label_en="Portfolio")
    DashboardMenuItem.objects.filter(key="steam_catalog", label_ru="Каталог скинов").update(label_ru="Steam Catalog")
    DashboardMenuItem.objects.filter(key="dashboard_preview", label_ru="Панель").update(label_ru="Dashboard")


class Migration(migrations.Migration):

    dependencies = [
        ("system_config", "0019_menu_donations_visibility"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
