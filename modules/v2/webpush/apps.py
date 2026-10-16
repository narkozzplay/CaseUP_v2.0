from django.apps import AppConfig


class WebPushConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.webpush"
    label = "webpush"
    verbose_name = "Web push"

    def ready(self):
        from . import signals  # noqa: F401
