from django.db import migrations


def move_customization_to_overview(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key="customization_catalog").update(
        label_ru="Оформление профиля",
        label_en="Profile appearance",
        group="overview",
        visibility_condition="authenticated",
        sort_order=52,
    )


def move_customization_back(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key="customization_catalog").update(
        label_ru="Каталог кастомизации",
        label_en="Customization Catalog",
        group="admin",
        visibility_condition="admin_plus",
        sort_order=67,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("system_config", "0010_remove_testing_preview_menu"),
    ]

    operations = [
        migrations.RunPython(move_customization_to_overview, move_customization_back),
    ]
