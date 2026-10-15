from django.apps import AppConfig


class AntifraudConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.antifraud"
    label = "antifraud"
    verbose_name = "Anti-fraud"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
