from django.apps import AppConfig


class ProfileCustomizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.profile_customization"
    label = "profile_customization"
    verbose_name = "Profile Customization"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
