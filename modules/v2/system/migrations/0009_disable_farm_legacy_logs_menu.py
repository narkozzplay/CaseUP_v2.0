from django.db import migrations


def disable_legacy_nav(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key="farm_legacy_logs").update(is_enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ("system_config", "0008_alter_dashboardmenuitem_visibility_condition"),
    ]

    operations = [
        migrations.RunPython(disable_legacy_nav, migrations.RunPython.noop),
    ]
