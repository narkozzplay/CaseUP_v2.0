from django.apps import AppConfig


class PromoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.promo"
    label = "promo"
    verbose_name = "Promo codes"

    def ready(self):
        from .notifications import register_notification_kinds
        from .signals import connect_ledger_signals

        register_notification_kinds()
        connect_ledger_signals()
