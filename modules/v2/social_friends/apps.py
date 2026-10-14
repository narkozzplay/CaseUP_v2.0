from django.apps import AppConfig


class SocialFriendsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.social_friends"
    label = "social_friends"
    verbose_name = "Social Friends"

    def ready(self):
        from .notifications import register_notification_kinds

        register_notification_kinds()
