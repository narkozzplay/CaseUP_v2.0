from django.apps import AppConfig


class BusinessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.business"
    label = "business"
    verbose_name = "Business analytics"

    def ready(self):
        from .signals import connect_ledger_signals

        connect_ledger_signals()
