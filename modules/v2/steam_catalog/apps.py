from django.apps import AppConfig


class SteamCatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.steam_catalog"
    label = "steam_catalog"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
