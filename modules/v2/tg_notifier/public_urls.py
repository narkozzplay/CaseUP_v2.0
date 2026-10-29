from django.urls import path

from .views import bot_prefs_save, telegram_webhook_view

app_name = "tg_notifier_public"

urlpatterns = [
    path("webhook/<str:webhook_secret>/", telegram_webhook_view, name="webhook"),
    path("bot-settings/", bot_prefs_save, name="bot_prefs"),
]