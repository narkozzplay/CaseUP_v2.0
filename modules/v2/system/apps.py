from django.apps import AppConfig


class SystemConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.system"
    label = "system_config"
    verbose_name = "System Configuration"

    def ready(self):
        from . import checks, signals  # noqa: F401
