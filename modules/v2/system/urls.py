from django.urls import path

from .views import activate_theme_page, settings_page, toggle_module_state_page, versioning_page

app_name = "system_config"

urlpatterns = [
    path("", settings_page, name="settings"),
    path("versioning/", versioning_page, name="versioning"),
    path("themes/activate/", activate_theme_page, name="activate_theme"),
    path("modules/<slug:module_key>/toggle/", toggle_module_state_page, name="toggle_module_state"),
]
