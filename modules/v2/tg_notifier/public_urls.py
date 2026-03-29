from django.urls import path

from .views import telegram_webhook_view

app_name = "tg_notifier_public"

urlpatterns = [
    path("webhook/<str:webhook_secret>/", telegram_webhook_view, name="webhook"),
]