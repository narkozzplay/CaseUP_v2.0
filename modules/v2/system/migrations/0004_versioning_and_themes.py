from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def seed_versioning(apps, schema_editor):
    ProjectVersion = apps.get_model("system_config", "ProjectVersion")
    ModuleRelease = apps.get_model("system_config", "ModuleRelease")
    ModuleCompatibility = apps.get_model("system_config", "ModuleCompatibility")
    ThemeProfile = apps.get_model("system_config", "ThemeProfile")
    EnvironmentModuleState = apps.get_model("system_config", "EnvironmentModuleState")
    SiteConfiguration = apps.get_model("system_config", "SiteConfiguration")

    project_version, _ = ProjectVersion.objects.get_or_create(
        version="0.1.38",
        defaults={
            "version_order": 138,
            "title": "CaseUP 0.1.38",
            "status": "active",
            "is_current": True,
            "is_default_for_dev": True,
            "is_default_for_prod": True,
        },
    )

    registry = [
        ("farm", "Farm", "Инфраструктура фермы, аккаунты, сессии и аналитика."),
        ("tradehub", "TradeHub", "Торговый хаб и продажи на базе farm-контура."),
        ("steam_catalog", "Steam Catalog", "Каталог Steam-предметов, цены, алерты и staff inventory."),
        ("steam_trading", "Steam Trading", "Инвестиционный контур Steam с покупками, sell-flow и аналитикой."),
        ("celery_tasks", "Celery Tasks", "Панель управления фоновой очередью, правилами и run-логами."),
        ("tg_notifier", "TG Notifier", "Телеграм-уведомления и webhook-интеграция."),
        ("subscriptions", "Subscriptions", "Коммерческие тарифы, feature-based доступ и paywall-состояния."),
        ("game_servers", "Game Servers", "Локальная интеграция игровых серверов, мониторинг статуса и foundation под привилегии."),
    ]
    for key, title, description in registry:
        release, _ = ModuleRelease.objects.get_or_create(
            module_key=key,
            version="0.1.38",
            defaults={
                "version_order": 138,
                "channel": "stable",
                "title": f"{title} 0.1.38",
                "description": description,
                "is_default": True,
                "is_active": True,
            },
        )
        ModuleCompatibility.objects.get_or_create(
            module_release=release,
            project_version_from=project_version,
            project_version_to=project_version,
            defaults={
                "compatibility_status": "supported",
                "notes": "Seeded from existing module registry.",
            },
        )
        for environment in ("dev", "prod", "test"):
            EnvironmentModuleState.objects.get_or_create(
                environment=environment,
                module_key=key,
                defaults={
                    "module_release": release,
                    "is_enabled": True if key != "tg_notifier" else False,
                    "is_test_mode": environment == "test",
                },
            )

    theme, _ = ThemeProfile.objects.get_or_create(
        key="default",
        defaults={
            "name": "Default CaseUP",
            "code": "default",
            "description": "Текущий основной стиль проекта.",
            "template_base_path": "themes/default",
            "css_file": "css/themes/default.css",
            "is_default": True,
            "is_active": True,
            "is_test_only": False,
            "project_version_from": project_version,
            "project_version_to": project_version,
        },
    )
    config = SiteConfiguration.objects.filter(singleton_key=1).first()
    if config:
        if not config.current_project_version_id:
            config.current_project_version = project_version
        if not config.active_theme_id:
            config.active_theme = theme
        config.save(update_fields=["current_project_version", "active_theme", "updated_at"])


def seed_dashboard_menu_versioning(apps, schema_editor):
    DashboardMenuItem = apps.get_model("system_config", "DashboardMenuItem")
    DashboardMenuItem.objects.get_or_create(
        key="system_versioning",
        defaults={
            "label_ru": "Версии и темы",
            "label_en": "Versions & Themes",
            "url_name": "system_config:versioning",
            "group": "admin",
            "visibility_condition": "admin_plus",
            "is_enabled": True,
            "sort_order": 25,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("system_config", "0003_siteconfiguration_currency_rate_and_choices"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.CharField(max_length=32, unique=True)),
                ("version_order", models.PositiveIntegerField(db_index=True, unique=True)),
                ("title", models.CharField(blank=True, default="", max_length=120)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("active", "Active"), ("supported", "Supported"), ("deprecated", "Deprecated"), ("archived", "Archived")], db_index=True, default="active", max_length=24)),
                ("is_current", models.BooleanField(db_index=True, default=False)),
                ("is_default_for_dev", models.BooleanField(db_index=True, default=False)),
                ("is_default_for_prod", models.BooleanField(db_index=True, default=False)),
                ("release_date", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Project version", "verbose_name_plural": "Project versions", "ordering": ("version_order", "version")},
        ),
        migrations.CreateModel(
            name="ModuleRelease",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("module_key", models.CharField(db_index=True, max_length=64)),
                ("version", models.CharField(max_length=32)),
                ("version_order", models.PositiveIntegerField(db_index=True, default=1)),
                ("channel", models.CharField(choices=[("stable", "Stable"), ("test", "Test"), ("deprecated", "Deprecated")], db_index=True, default="stable", max_length=16)),
                ("title", models.CharField(blank=True, default="", max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Module release", "verbose_name_plural": "Module releases", "ordering": ("module_key", "version_order", "version")},
        ),
        migrations.CreateModel(
            name="ThemeProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("code", models.CharField(blank=True, default="", max_length=64)),
                ("description", models.TextField(blank=True, default="")),
                ("template_base_path", models.CharField(blank=True, default="", max_length=255)),
                ("css_file", models.CharField(blank=True, default="", max_length=255)),
                ("preview_image", models.ImageField(blank=True, null=True, upload_to="themes/previews/")),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("is_test_only", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project_version_from", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="themes_from", to="system_config.projectversion")),
                ("project_version_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="themes_to", to="system_config.projectversion")),
            ],
            options={"verbose_name": "Theme profile", "verbose_name_plural": "Theme profiles", "ordering": ("name", "key")},
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="active_theme",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="site_configurations", to="system_config.themeprofile"),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="current_project_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="site_configurations", to="system_config.projectversion"),
        ),
        migrations.CreateModel(
            name="ModuleCompatibility",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("compatibility_status", models.CharField(choices=[("supported", "Supported"), ("partial", "Partial"), ("unsupported", "Unsupported"), ("test_only", "Test only")], db_index=True, default="supported", max_length=16)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("module_release", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="compatibilities", to="system_config.modulerelease")),
                ("project_version_from", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="module_compat_from", to="system_config.projectversion")),
                ("project_version_to", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="module_compat_to", to="system_config.projectversion")),
            ],
            options={"verbose_name": "Module compatibility", "verbose_name_plural": "Module compatibilities"},
        ),
        migrations.CreateModel(
            name="EnvironmentModuleState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("environment", models.CharField(choices=[("dev", "Dev"), ("prod", "Prod"), ("test", "Test")], db_index=True, max_length=12)),
                ("module_key", models.CharField(db_index=True, max_length=64)),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("is_test_mode", models.BooleanField(db_index=True, default=False)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("module_release", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="environment_states", to="system_config.modulerelease")),
            ],
            options={"verbose_name": "Environment module state", "verbose_name_plural": "Environment module states", "ordering": ("environment", "module_key")},
        ),
        migrations.AddConstraint(model_name="modulerelease", constraint=models.UniqueConstraint(fields=("module_key", "version"), name="uniq_module_release_version")),
        migrations.AddConstraint(model_name="modulecompatibility", constraint=models.UniqueConstraint(fields=("module_release", "project_version_from", "project_version_to"), name="uniq_module_release_project_range")),
        migrations.AddConstraint(model_name="environmentmodulestate", constraint=models.UniqueConstraint(fields=("environment", "module_key"), name="uniq_environment_module_state")),
        migrations.RunPython(seed_versioning, migrations.RunPython.noop),
        migrations.RunPython(seed_dashboard_menu_versioning, migrations.RunPython.noop),
    ]
