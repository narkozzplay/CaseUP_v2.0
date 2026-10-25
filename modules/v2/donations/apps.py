from django.apps import AppConfig


class DonationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.donations"
    label = "donations"
    verbose_name = "Donations"
