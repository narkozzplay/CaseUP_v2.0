from django.apps import AppConfig


class FarmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.farm"
    label = "farm"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
