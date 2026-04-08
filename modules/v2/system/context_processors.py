from __future__ import annotations

from modules.v2.profiles.rbac import get_user_role

from .dashboard_menu import build_dashboard_menu
from .services import (
    bootstrap_versioning,
    get_active_theme,
    get_current_project_version,
    get_module_state_map,
    get_runtime_environment,
    get_site_configuration,
    get_theme_template_map,
)


def system_settings(request):
    bootstrap_versioning()
    config = get_site_configuration()
    active_theme = get_active_theme(project_version=get_current_project_version())
    return {
        "site_settings": config,
        "feature_flags": get_module_state_map(),
        "role_access": get_user_role(getattr(request, "user", None)),
        "dashboard_menu_groups": build_dashboard_menu(request),
        "active_theme": active_theme,
        "current_project_version": get_current_project_version(),
        "runtime_environment": get_runtime_environment(),
        "active_theme_css_file": active_theme.css_file if active_theme else "",
        **get_theme_template_map(),
    }
