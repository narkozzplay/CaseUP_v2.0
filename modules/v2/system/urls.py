from django.urls import path

from .views import settings_page

app_name = "system_config"

urlpatterns = [
    path("", settings_page, name="settings"),
]
