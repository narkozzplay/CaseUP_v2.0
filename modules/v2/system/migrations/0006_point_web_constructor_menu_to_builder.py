from django.db import migrations


def forwards(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key="web_constructor").update(url_name="web_constructor:builder")


def backwards(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key="web_constructor").update(url_name="web_constructor:dashboard")


class Migration(migrations.Migration):
    dependencies = [("system_config", "0005_seed_midnight_theme")]

    operations = [migrations.RunPython(forwards, backwards)]
