from __future__ import annotations

from modules.v2.profiles.rbac import get_user_role
from modules.v2.store.services import resolve_user_theme_runtime_state

from .dashboard_menu import build_dashboard_menu
from .services import (
    get_active_theme,
    get_current_project_version,
    get_module_state_map,
    get_runtime_environment,
    get_site_configuration,
    get_theme_template_map,
)
from .theme_plugins import resolve_request_theme_scope


def system_settings(request):
    config = get_site_configuration()
    active_theme = get_active_theme(project_version=get_current_project_version())
    request_scope = resolve_request_theme_scope(request)
    user_theme_state = resolve_user_theme_runtime_state(
        user=getattr(request, "user", None),
        request_scope=request_scope,
    )
    return {
        "site_settings": config,
        "feature_flags": get_module_state_map(),
        "role_access": get_user_role(getattr(request, "user", None)),
        "dashboard_menu_groups": build_dashboard_menu(request),
        "active_theme": active_theme,
        "current_project_version": get_current_project_version(),
        "runtime_environment": get_runtime_environment(),
        "active_theme_css_file": active_theme.css_file if active_theme else "",
        "runtime_theme_scope": request_scope,
        "runtime_theme_css_files": user_theme_state.css_files,
        "user_theme_preferences": user_theme_state.preferences,
        "user_selected_global_theme": user_theme_state.selected_global_theme,
        "user_selected_scope_theme": user_theme_state.selected_scope_theme,
        **get_theme_template_map(),
    }
