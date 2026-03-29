from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.accounts"
    label = "accounts_v2"
    verbose_name = "Accounts v2"