from django.db import migrations

WEB_CONSTRUCTOR_LABEL = "web_constructor"


def remove_midnight_theme(apps):
    ThemeProfile = apps.get_model("system_config", "ThemeProfile")
    SiteConfiguration = apps.get_model("system_config", "SiteConfiguration")
    midnight = ThemeProfile.objects.filter(key="midnight").first()
    if midnight:
        default_theme = ThemeProfile.objects.filter(key="default").first()
        SiteConfiguration.objects.filter(active_theme=midnight).update(active_theme=default_theme)
        midnight.delete()
    apps.get_model("system_config", "ModuleState").objects.filter(key="midnight_theme").delete()
    apps.get_model("system_config", "ModuleRelease").objects.filter(module_key="midnight_theme").delete()
    apps.get_model("system_config", "EnvironmentModuleState").objects.filter(module_key="midnight_theme").delete()


def forwards(apps, schema_editor):
    remove_midnight_theme(apps)

    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.filter(key__in=("web_constructor", "web_constructor_moderation")).delete()
    apps.get_model("system_config", "ModuleState").objects.filter(key=WEB_CONSTRUCTOR_LABEL).delete()
    apps.get_model("system_config", "ModuleRelease").objects.filter(module_key=WEB_CONSTRUCTOR_LABEL).delete()
    apps.get_model("system_config", "EnvironmentModuleState").objects.filter(module_key=WEB_CONSTRUCTOR_LABEL).delete()

    # The web constructor app is gone: drop its tables and the rows Django kept about it.
    connection = schema_editor.connection
    prefix = f"{WEB_CONSTRUCTOR_LABEL}_"
    tables = [name for name in connection.introspection.table_names() if name.startswith(prefix)]
    cascade = " CASCADE" if connection.vendor == "postgresql" else ""
    for table in tables:
        schema_editor.execute(f"DROP TABLE IF EXISTS {schema_editor.quote_name(table)}{cascade}")

    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label=WEB_CONSTRUCTOR_LABEL).delete()
    schema_editor.execute("DELETE FROM django_migrations WHERE app = %s", [WEB_CONSTRUCTOR_LABEL])


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("system_config", "0021_menu_topup_constructor"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
