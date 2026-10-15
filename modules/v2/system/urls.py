from django.urls import path

from .views import (
    check_integrations_page,
    clear_cache_page,
    export_settings_page,
    maintenance_preview_page,
    settings_page,
    toggle_module_state_page,
)

app_name = "system_config"

urlpatterns = [
    path("", settings_page, name="settings"),
    path("integrations/check/", check_integrations_page, name="check_integrations"),
    path("cache/clear/", clear_cache_page, name="clear_cache"),
    path("export.json", export_settings_page, name="export_settings"),
    path("maintenance-preview/", maintenance_preview_page, name="maintenance_preview"),
    path("modules/<slug:module_key>/toggle/", toggle_module_state_page, name="toggle_module_state"),
]
