from django.apps import AppConfig


class DailyBonusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.daily_bonus"
    label = "daily_bonus"
    verbose_name = "Daily bonus"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
