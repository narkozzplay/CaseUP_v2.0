from __future__ import annotations

from modules.v2.profiles.presentation import get_verification_badge
from modules.v2.profiles.rbac import get_user_role
from modules.v2.profiles.view_as_user import can_view_as_user, is_viewing_as_user
from modules.v2.store.services import (
    is_store_runtime_available,
    resolve_user_runtime_module_state,
    resolve_user_theme_runtime_state,
)

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
    user_module_state = resolve_user_runtime_module_state(
        user=getattr(request, "user", None),
        request_scope=request_scope,
    )
    return {
        "site_settings": config,
        "feature_flags": get_module_state_map(),
        "store_runtime_available": is_store_runtime_available(),
        "role_access": get_user_role(getattr(request, "user", None)),
        "viewing_as_user": is_viewing_as_user(getattr(request, "user", None)),
        "can_view_as_user": can_view_as_user(getattr(request, "user", None)),
        "current_user_verification_badge": get_verification_badge(
            getattr(getattr(request, "user", None), "profile_v2", None)
        ),
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
        "runtime_module_css_files": user_module_state.css_files,
        "runtime_module_js_files": user_module_state.js_files,
        "user_module_preferences": user_module_state.preferences,
        "runtime_module_slot_states": user_module_state.slot_states,
        **get_theme_template_map(),
    }
