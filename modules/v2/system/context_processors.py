from __future__ import annotations

from modules.v2.profiles.rbac import get_user_role

from .dashboard_menu import build_dashboard_menu
from .services import get_module_state_map, get_site_configuration


def system_settings(request):
    config = get_site_configuration()
    return {
        "site_settings": config,
        "feature_flags": get_module_state_map(),
        "role_access": get_user_role(getattr(request, "user", None)),
        "dashboard_menu_groups": build_dashboard_menu(request),
    }
