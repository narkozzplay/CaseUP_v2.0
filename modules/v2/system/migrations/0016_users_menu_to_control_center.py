from django.db import migrations


def forward(apps, schema_editor):
    apps.get_model("system_config", "DashboardMenuItem").objects.filter(key="users_catalog").update(url_name="control_center:users", label_en="Users")


def backward(apps, schema_editor):
    apps.get_model("system_config", "DashboardMenuItem").objects.filter(key="users_catalog").update(url_name="profiles:users_catalog", label_en="Users Catalog")


class Migration(migrations.Migration):
    dependencies = [("system_config", "0015_remove_versioning_menu_item")]

    operations = [migrations.RunPython(forward, backward)]
