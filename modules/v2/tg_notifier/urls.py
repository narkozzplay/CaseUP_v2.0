from django.urls import path

from . import views

app_name = "tg_notifier"

urlpatterns = [
    path("", views.settings_page, name="settings"),
    path("center/", views.notification_center_page, name="center"),
]