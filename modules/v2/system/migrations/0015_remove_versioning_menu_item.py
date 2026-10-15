from django.db import migrations


def remove_item(apps, schema_editor):
    apps.get_model("system_config", "DashboardMenuItem").objects.filter(key="system_versioning").delete()


class Migration(migrations.Migration):
    dependencies = [("system_config", "0014_menu_labels_ru")]

    operations = [migrations.RunPython(remove_item, migrations.RunPython.noop)]
