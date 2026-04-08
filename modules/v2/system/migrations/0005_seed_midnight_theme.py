from django.db import migrations


def seed_midnight_theme(apps, schema_editor):
    ThemeProfile = apps.get_model("system_config", "ThemeProfile")
    ProjectVersion = apps.get_model("system_config", "ProjectVersion")
    SiteConfiguration = apps.get_model("system_config", "SiteConfiguration")

    project_version = ProjectVersion.objects.order_by("-version_order").first()
    theme, _created = ThemeProfile.objects.update_or_create(
        key="midnight",
        defaults={
            "name": "Midnight Neon",
            "code": "midnight",
            "description": "Альтернативная тёмная тема для dev/test и plugin-поставок.",
            "template_base_path": "themes/midnight",
            "css_file": "css/themes/midnight.css",
            "is_default": False,
            "is_active": True,
            "is_test_only": True,
            "project_version_from": project_version,
            "project_version_to": project_version,
        },
    )

    config = SiteConfiguration.objects.filter(singleton_key=1).first()
    if config and not config.active_theme_id:
        default_theme = ThemeProfile.objects.filter(key="default").first() or theme
        config.active_theme = default_theme
        config.save(update_fields=["active_theme", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("system_config", "0004_versioning_and_themes"),
    ]

    operations = [
        migrations.RunPython(seed_midnight_theme, migrations.RunPython.noop),
    ]
