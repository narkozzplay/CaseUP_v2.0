from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.subscriptions"
    label = "subscriptions"
    verbose_name = "Subscriptions"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
