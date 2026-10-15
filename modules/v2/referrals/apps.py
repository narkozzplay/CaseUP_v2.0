from django.apps import AppConfig


class ReferralsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.referrals"
    label = "referrals"
    verbose_name = "Referrals"

    def ready(self):
        from .notifications import register_notification_kinds
        from .signals import connect_ledger_signals

        register_notification_kinds()
        connect_ledger_signals()
