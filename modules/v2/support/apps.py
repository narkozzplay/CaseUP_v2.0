from django.apps import AppConfig


class SupportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.support"
    label = "support"
    verbose_name = "Support"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
