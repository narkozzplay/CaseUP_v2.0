from django.apps import AppConfig


class MajorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.majors"
    label = "majors"
    verbose_name = "Majors and Pick'em"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
