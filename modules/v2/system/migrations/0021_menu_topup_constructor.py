from django.db import migrations


def forwards(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    # Top-up stays one click away from the balance in the header and the sidebar card.
    DashboardMenuItem.objects.filter(key="balance_topup").update(is_enabled=False)
    DashboardMenuItem.objects.filter(key="web_constructor", visibility_condition="authenticated").update(group="admin", visibility_condition="admin_plus")


def backwards(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key="balance_topup").update(is_enabled=True)
    DashboardMenuItem.objects.filter(key="web_constructor", visibility_condition="admin_plus").update(group="overview", visibility_condition="authenticated")


class Migration(migrations.Migration):

    dependencies = [
        ("system_config", "0020_menu_declutter_for_users"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
