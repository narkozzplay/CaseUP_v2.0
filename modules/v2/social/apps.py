from django.apps import AppConfig


class SocialConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.social"
    label = "social_layer"
    verbose_name = "Social Layer"
