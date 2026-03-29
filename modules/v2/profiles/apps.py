from django.apps import AppConfig


class ProfilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.profiles"
    label = "profiles"
    verbose_name = "Profiles"

    def ready(self):
        from . import signals  # noqa