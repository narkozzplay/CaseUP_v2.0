from django.apps import AppConfig


class ControlCenterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.control_center"
    label = "control_center"
    verbose_name = "Control Center"

    def ready(self):
        from .activity_events import connect_activity_receivers

        connect_activity_receivers()
