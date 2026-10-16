from django.apps import AppConfig


class SteamTradingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.steam_trading"
    label = "steam_trading"
    verbose_name = "Steam Trading"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
