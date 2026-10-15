from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from django.db import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse

from modules.v2.farm.auth import get_farm_access
from modules.v2.profiles.rbac import get_user_role
from modules.v2.subscriptions.feature_codes import TG_NOTIFIER_USER
from modules.v2.subscriptions.services import get_subscription_context

from .models import DashboardMenuItem
from .services import build_lab_url, get_module_state_map, resolve_module_for_namespace


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
    "subscriptions:plans",
    "subscriptions:farmer_plans",
    "admin:index",
    "web_constructor:builder",
    "web_constructor:builder_editor",
}

ACTIVE_VIEW_ALIASES = {
    "dashboard_preview": {"frontend:dashboard_preview"},
    "profile": {
        "profiles:profile",
        "profiles:profile_detail",
        "profiles:profile_admin_update",
        "profiles:profile_settings",
        "profiles:profile_user_settings",
    },
    "cs2_stats": {"cs2_stats:own", "cs2_stats:user"},
    "cs2_match_history": {"cs2_stats:match_history", "cs2_stats:match_detail"},
    "verification_requests": {"profiles:verification_requests"},
    "customization_catalog": {
        "profile_customization:catalog",
        "profile_customization:admin_add_background",
        "profile_customization:admin_add_avatar",
    },
    "users_catalog": {"profiles:users_catalog"},
    "game_servers": {"game_servers:list", "game_servers:detail"},
    "balance_topup": {"payments:topup", "payments:dev_checkout"},
    "case_simulator": {"case_simulator:list", "case_simulator:detail", "case_simulator:terminal", "case_simulator:inventory"},
    "notification_center": {"tg_notifier:center"},
    "farm_hub": {"farm:hub"},
    "farm_dashboard": {"farm:dashboard"},
    "farm_accounts": {
        "farm:accounts_list",
        "farm:accounts_add",
        "farm:accounts_detail",
        "farm:accounts_edit",
        "farm:accounts_delete",
        "farm:account_inventory_sync",
        "farm:accounts_refresh_profiles",
    },
    "farm_drops": {"farm:sessions_list", "farm:sessions_add", "farm:sessions_detail", "farm:item_search"},
    "farm_drop_pool": {"farm:drop_pool", "farm:drop_pool_remove"},
    "farm_legacy_logs": {"farm:drops_list", "farm:drops_add"},
    "tradehub_dashboard": {"tradehub:dashboard", "tradehub:sales_list", "tradehub:sale_detail", "tradehub:create_order", "tradehub:sale_action", "tradehub:bots_list", "tradehub:bots_add"},
    "steam_catalog": {"steam_catalog:list", "steam_catalog:add", "steam_catalog:detail", "steam_catalog:alert_rules", "steam_catalog:alert_help", "steam_catalog:alert_history", "steam_catalog:alert_rule_create", "steam_catalog:alert_rule_edit", "steam_catalog:alert_rule_delete", "steam_catalog:user_alert_create", "steam_catalog:user_alert_toggle"},
    "steam_investments": {"steam_trading:investments", "steam_trading:list", "steam_trading:add", "steam_trading:sell"},
    "control_center": {
        "frontend:admin_panel",
        "control_center:users",
        "control_center:user_detail",
        "control_center:audit",
        "control_center:ledger",
        "control_center:moderation",
        "control_center:health",
        "control_center:referrals",
        "control_center:promo",
        "control_center:promo_create",
        "control_center:promo_edit",
        "control_center:multi_accounts",
        "control_center:multi_account_detail",
    },
    "system_settings": {"system_config:settings"},
    "celery_tasks": {"celery_tasks:control_center", "celery_tasks:toggle_rule", "celery_tasks:run_rule_now"},
    "tg_settings": {"tg_notifier:settings"},
    "subscription_pricing": {"subscriptions:pricing_dashboard"},
    "farm_settings": {"farm:settings"},
    "web_constructor": {"web_constructor:builder", "web_constructor:builder_editor", "web_constructor:dashboard", "web_constructor:create", "web_constructor:detail", "web_constructor:editor", "web_constructor:versions"},
    "store": {
        "store:storefront",
        "store:library",
        "store:product_detail",
        "store:author_products",
        "store:moderation_queue",
        "store:analytics",
    },
    "web_constructor_moderation": {"web_constructor:moderation", "web_constructor:moderate_version"},
    "django_admin": {"admin:index"},
}


CLAIMED_VIEW_NAMES = frozenset(name for names in ACTIVE_VIEW_ALIASES.values() for name in names)


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


def _visibility_map(request, *, feature_flags: dict[str, bool] | None = None) -> dict[str, bool]:
    user = getattr(request, "user", None)
    role_access = get_user_role(user)
    farm_access = get_farm_access(user)
    feature_flags = feature_flags or get_module_state_map()
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
        DashboardMenuItem.Visibility.MODER_PLUS: bool(getattr(user, "is_authenticated", False) and role_access.is_moder_plus),
        DashboardMenuItem.Visibility.ADMIN_PLUS: bool(getattr(user, "is_authenticated", False) and role_access.is_admin_plus),
        DashboardMenuItem.Visibility.SUBSCRIPTIONS_ENABLED: bool(feature_flags.get("subscriptions", False)),
        DashboardMenuItem.Visibility.STORE_ENABLED: bool(feature_flags.get("store", False)),
    }


def _resolve_menu_target_url(item: DashboardMenuItem, url: str) -> str:
    if item.key == "web_constructor":
        lab_url = build_lab_url(url)
        if lab_url:
            return lab_url
    return url


def _is_item_active(item: DashboardMenuItem, request, url: str) -> bool:
    current_view_name = getattr(getattr(request, "resolver_match", None), "view_name", "") or ""
    aliases = ACTIVE_VIEW_ALIASES.get(item.key, {item.url_name})
    if current_view_name in aliases:
        return True
    if current_view_name in CLAIMED_VIEW_NAMES:
        # Another menu item owns this view, so a URL prefix match (e.g. /farm/
        # for /farm/hub/) must not highlight this one as well.
        return False
    path = getattr(request, "path", "") or ""
    comparable_url = urlparse(url).path or url
    if comparable_url != "/" and path.startswith(comparable_url.rstrip("/") + "/"):
        return True
    return path == comparable_url


def _resolve_item_namespace(url_name: str) -> str:
    raw = (url_name or "").strip()
    if ":" not in raw:
        return ""
    return raw.split(":", 1)[0]


def build_dashboard_menu(request) -> tuple[dict[str, object], ...]:
    ensure_dashboard_menu_items()

    feature_flags = get_module_state_map()
    visibility_map = _visibility_map(request, feature_flags=feature_flags)
    items = []
    try:
        queryset = DashboardMenuItem.objects.filter(is_enabled=True).order_by("group", "sort_order", "id")
        for item in queryset:
            namespace = _resolve_item_namespace(item.url_name)
            module = resolve_module_for_namespace(namespace)
            if module is not None and not bool(feature_flags.get(module.key, module.default_enabled)):
                continue
            if not visibility_map.get(item.visibility_condition, False):
                continue
            try:
                url = reverse(item.url_name)
            except NoReverseMatch:
                continue
            items.append((item, _resolve_menu_target_url(item, url)))
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
