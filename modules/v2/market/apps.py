from django.apps import AppConfig


class MarketConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.market"
    label = "market"
    verbose_name = "Market"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
