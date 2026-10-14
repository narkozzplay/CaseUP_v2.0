from django.db import migrations


def remove_testing_preview_menu(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key="testing_preview").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("system_config", "0009_disable_farm_legacy_logs_menu"),
    ]

    operations = [
        migrations.RunPython(remove_testing_preview_menu, migrations.RunPython.noop),
    ]
