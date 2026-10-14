from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.payments"
    verbose_name = "Payments"

    def ready(self):
        from . import checks  # noqa: F401
        from .notifications import register_notification_kinds

        register_notification_kinds()
