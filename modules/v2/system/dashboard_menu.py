from __future__ import annotations

from dataclasses import dataclass

from django.db import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse

from modules.v2.farm.auth import get_farm_access
from modules.v2.profiles.rbac import get_user_role
from modules.v2.subscriptions.feature_codes import TG_NOTIFIER_USER
from modules.v2.subscriptions.services import get_subscription_context

from .models import DashboardMenuItem
from .services import get_module_state_map


@dataclass(frozen=True)
class DashboardMenuItemView:
    key: str
    label: str
    url: str
    group: str
    group_label: str
    is_active: bool
    is_shell_nav: bool


GROUP_LABELS = {
    DashboardMenuItem.Group.OVERVIEW: {"ru": "Обзор", "en": "Overview"},
    DashboardMenuItem.Group.FARM: {"ru": "Ферма", "en": "Farm"},
    DashboardMenuItem.Group.STEAM: {"ru": "Steam", "en": "Steam"},
    DashboardMenuItem.Group.INVESTMENTS: {"ru": "Инвестиции", "en": "Investments"},
    DashboardMenuItem.Group.ADMIN: {"ru": "Админ панель", "en": "Admin panel"},
}

SHELL_DISABLED_URL_NAMES = {
    "frontend:home",
    "frontend:testing_preview",
    "subscriptions:plans",
    "subscriptions:farmer_plans",
    "admin:index",
}

ACTIVE_VIEW_ALIASES = {
    "dashboard_preview": {"frontend:dashboard_preview"},
    "profile": {"profiles:profile", "profiles:profile_detail", "profiles:profile_admin_update"},
    "notification_center": {"tg_notifier:center"},
    "farm_dashboard": {"farm:dashboard"},
    "farm_accounts": {"farm:accounts_list", "farm:accounts_add", "farm:accounts_detail", "farm:account_inventory_sync"},
    "farm_drops": {"farm:sessions_list", "farm:sessions_add", "farm:sessions_detail"},
    "farm_legacy_logs": {"farm:drops_list", "farm:drops_add", "farm:drop_pool", "farm:drop_pool_remove"},
    "tradehub_dashboard": {"tradehub:dashboard", "tradehub:sales_list", "tradehub:sale_detail", "tradehub:create_order", "tradehub:sale_action", "tradehub:bots_list", "tradehub:bots_add"},
    "steam_catalog": {"steam_catalog:list", "steam_catalog:add", "steam_catalog:detail", "steam_catalog:alert_rules", "steam_catalog:alert_help", "steam_catalog:alert_history", "steam_catalog:alert_rule_create", "steam_catalog:alert_rule_edit", "steam_catalog:alert_rule_delete", "steam_catalog:user_alert_create", "steam_catalog:user_alert_toggle"},
    "steam_investments": {"steam_trading:investments", "steam_trading:list", "steam_trading:add", "steam_trading:sell"},
    "control_center": {"frontend:admin_panel"},
    "system_settings": {"system_config:settings"},
    "celery_tasks": {"celery_tasks:control_center", "celery_tasks:toggle_rule", "celery_tasks:run_rule_now"},
    "tg_settings": {"tg_notifier:settings"},
    "subscription_pricing": {"subscriptions:pricing_dashboard"},
    "farm_settings": {"farm:settings"},
    "django_admin": {"admin:index"},
}


def ensure_dashboard_menu_items() -> None:
    try:
        existing_keys = set(DashboardMenuItem.objects.values_list("key", flat=True))
    except (OperationalError, ProgrammingError):
        return

    missing_rows = [
        defaults
        for defaults in DashboardMenuItem.bootstrap_defaults()
        if defaults["key"] not in existing_keys
    ]
    if missing_rows:
        DashboardMenuItem.objects.bulk_create(DashboardMenuItem(**row) for row in missing_rows)


def _lang_code(request) -> str:
    return "ru" if (getattr(request, "LANGUAGE_CODE", "ru") or "ru").startswith("ru") else "en"


def _resolve_group_label(group: str, request) -> str:
    lang = _lang_code(request)
    return GROUP_LABELS.get(group, {}).get(lang, group)


def _resolve_item_label(item: DashboardMenuItem, request) -> str:
    lang = _lang_code(request)
    if lang == "ru":
        return item.label_ru or item.label_en or item.key
    return item.label_en or item.label_ru or item.key


def _visibility_map(request) -> dict[str, bool]:
    user = getattr(request, "user", None)
    role_access = get_user_role(user)
    farm_access = get_farm_access(user)
    feature_flags = get_module_state_map()
    site_subscription = get_subscription_context(user)
    tg_allowed = bool(feature_flags.get("tg_notifier", False) and site_subscription.feature_map.get(TG_NOTIFIER_USER, False))

    return {
        DashboardMenuItem.Visibility.ALWAYS: True,
        DashboardMenuItem.Visibility.AUTHENTICATED: bool(getattr(user, "is_authenticated", False)),
        DashboardMenuItem.Visibility.FARM_ACCESS: bool(feature_flags.get("farm", False) and farm_access.has_access),
        DashboardMenuItem.Visibility.FARM_STAFF: bool(feature_flags.get("farm", False) and farm_access.staff_access),
        DashboardMenuItem.Visibility.STEAM_CATALOG_ACCESS: bool(
            feature_flags.get("steam_catalog", False) and site_subscription.feature_map.get("steam_catalog_view", False)
        ),
        DashboardMenuItem.Visibility.STEAM_INVESTOR_ACCESS: bool(
            feature_flags.get("steam_trading", False) and farm_access.has_access
        ),
        DashboardMenuItem.Visibility.TG_USER_ACCESS: tg_allowed,
        DashboardMenuItem.Visibility.ADMIN_PLUS: bool(getattr(user, "is_authenticated", False) and role_access.is_admin_plus),
        DashboardMenuItem.Visibility.SUBSCRIPTIONS_ENABLED: bool(feature_flags.get("subscriptions", False)),
    }


def _is_item_active(item: DashboardMenuItem, request, url: str) -> bool:
    current_view_name = getattr(getattr(request, "resolver_match", None), "view_name", "") or ""
    aliases = ACTIVE_VIEW_ALIASES.get(item.key, {item.url_name})
    if current_view_name in aliases:
        return True
    path = getattr(request, "path", "") or ""
    if url != "/" and path.startswith(url.rstrip("/") + "/"):
        return True
    return path == url


def build_dashboard_menu(request) -> tuple[dict[str, object], ...]:
    ensure_dashboard_menu_items()

    visibility_map = _visibility_map(request)
    items = []
    try:
        queryset = DashboardMenuItem.objects.filter(is_enabled=True).order_by("group", "sort_order", "id")
        for item in queryset:
            if not visibility_map.get(item.visibility_condition, False):
                continue
            try:
                url = reverse(item.url_name)
            except NoReverseMatch:
                continue
            items.append((item, url))
    except (OperationalError, ProgrammingError):
        return tuple()

    groups_present = {item.group for item, _ in items}
    merge_investments = DashboardMenuItem.Group.FARM in groups_present and DashboardMenuItem.Group.STEAM in groups_present

    grouped: dict[str, list[DashboardMenuItemView]] = {}
    for item, url in items:
        group_code = item.group
        if merge_investments and item.group in {DashboardMenuItem.Group.FARM, DashboardMenuItem.Group.STEAM}:
            group_code = DashboardMenuItem.Group.INVESTMENTS

        view = DashboardMenuItemView(
            key=item.key,
            label=_resolve_item_label(item, request),
            url=url,
            group=group_code,
            group_label=_resolve_group_label(group_code, request),
            is_active=_is_item_active(item, request, url),
            is_shell_nav=item.url_name not in SHELL_DISABLED_URL_NAMES,
        )
        grouped.setdefault(group_code, []).append(view)

    ordered_group_keys = [
        DashboardMenuItem.Group.OVERVIEW,
        DashboardMenuItem.Group.INVESTMENTS,
        DashboardMenuItem.Group.FARM,
        DashboardMenuItem.Group.STEAM,
        DashboardMenuItem.Group.ADMIN,
    ]
    return tuple(
        {
            "key": group_code,
            "label": _resolve_group_label(group_code, request),
            "items": tuple(grouped[group_code]),
            "is_open": any(item.is_active for item in grouped[group_code]),
        }
        for group_code in ordered_group_keys
        if grouped.get(group_code)
    )
