from django.apps import AppConfig


class TgNotifierConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.tg_notifier"
    verbose_name = "TG Notifier"

    def ready(self):
        from django.db.models.signals import post_save

        from modules.v2.notifications.models import Notification

        from .admin_events import connect_signals
        from .delivery import on_notification_saved

        connect_signals()
        post_save.connect(on_notification_saved, sender=Notification, dispatch_uid="tg_notifier_mirror_notification")
