from django.apps import AppConfig


class TgNotifierConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.tg_notifier"
    verbose_name = "TG Notifier"