from django.urls import path

from .views import (
    activate_theme_page,
    disable_runtime_module_page,
    settings_page,
    sync_runtime_modules_page,
    toggle_module_state_page,
    versioning_page,
)

app_name = "system_config"

urlpatterns = [
    path("", settings_page, name="settings"),
    path("versioning/", versioning_page, name="versioning"),
    path("themes/activate/", activate_theme_page, name="activate_theme"),
    path("runtime-modules/sync/", sync_runtime_modules_page, name="sync_runtime_modules"),
    path("runtime-modules/<slug:module_key>/disable/", disable_runtime_module_page, name="disable_runtime_module"),
    path("modules/<slug:module_key>/toggle/", toggle_module_state_page, name="toggle_module_state"),
]
