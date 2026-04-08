from django.urls import path

from .views import activate_theme_page, settings_page, versioning_page

app_name = "system_config"

urlpatterns = [
    path("", settings_page, name="settings"),
    path("versioning/", versioning_page, name="versioning"),
    path("themes/activate/", activate_theme_page, name="activate_theme"),
]
