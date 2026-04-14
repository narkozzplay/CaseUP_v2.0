from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db import OperationalError, ProgrammingError, transaction

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.rbac import get_user_role
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.system.theme_plugins import (
    GLOBAL_THEME_SCOPE,
    RuntimeThemeManifest,
    discover_runtime_themes,
    get_runtime_theme,
    get_theme_scope_label,
    get_user_theme_preferences,
    is_runtime_theme_compatible,
    normalize_theme_preferences,
)

from .models import ThemeStoreListing, UserThemePurchase


class ThemePurchaseError(ValueError):
    pass


@dataclass(frozen=True)
class UserThemeRuntimeState:
    preferences: dict[str, str]
    selected_global_theme: RuntimeThemeManifest | None
    selected_scope_theme: RuntimeThemeManifest | None
    css_files: tuple[str, ...]


def get_theme_store_listings(*, include_unpublished: bool = False) -> list[ThemeStoreListing]:
    try:
        queryset = ThemeStoreListing.objects.filter(category=ThemeStoreListing.Category.THEMES).order_by("-updated_at", "-id")
        if not include_unpublished:
            queryset = queryset.filter(status=ThemeStoreListing.Status.PUBLISHED, is_public=True)
        return list(queryset)
    except (OperationalError, ProgrammingError):
        return []


def get_user_purchased_theme_keys(user) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()
    try:
        return {
            key
            for key in UserThemePurchase.objects.filter(user=user).values_list("theme_key", flat=True)
            if key
        }
    except (OperationalError, ProgrammingError):
        return set()


def _is_theme_accessible_to_user(*, user, theme: RuntimeThemeManifest, purchased_theme_keys: set[str]) -> bool:
    if not is_runtime_theme_compatible(theme):
        return False

    role_access = get_user_role(user)
    if role_access.is_admin_plus:
        return True
    if theme.is_public:
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    return theme.key in purchased_theme_keys


def get_user_runtime_theme_map(user) -> dict[str, RuntimeThemeManifest]:
    runtime_themes = discover_runtime_themes()
    purchased_theme_keys = get_user_purchased_theme_keys(user)
    return {
        key: theme
        for key, theme in runtime_themes.items()
        if _is_theme_accessible_to_user(user=user, theme=theme, purchased_theme_keys=purchased_theme_keys)
    }


def get_user_theme_options_by_scope(user) -> dict[str, list[RuntimeThemeManifest]]:
    runtime_map = get_user_runtime_theme_map(user)
    grouped: dict[str, list[RuntimeThemeManifest]] = {}
    for theme in runtime_map.values():
        for scope in theme.scopes:
            grouped.setdefault(scope, []).append(theme)
    for scope, themes in grouped.items():
        grouped[scope] = sorted(themes, key=lambda item: (item.name.lower(), item.key))
    return grouped


def get_theme_scope_display_map(user) -> dict[str, str]:
    return {
        scope: get_theme_scope_label(scope)
        for scope in get_user_theme_options_by_scope(user)
    }


def _theme_supports_scope(theme: RuntimeThemeManifest, scope: str) -> bool:
    normalized_scope = str(scope or "").strip().lower()
    return normalized_scope in theme.scopes


def resolve_user_theme_runtime_state(*, user, request_scope: str) -> UserThemeRuntimeState:
    preferences = get_user_theme_preferences(user)
    available_themes = get_user_runtime_theme_map(user)

    selected_global_theme = None
    selected_scope_theme = None
    css_files: list[str] = []

    global_key = preferences.get(GLOBAL_THEME_SCOPE)
    if global_key:
        candidate = available_themes.get(global_key)
        if candidate is not None and _theme_supports_scope(candidate, GLOBAL_THEME_SCOPE):
            selected_global_theme = candidate
            css_files.extend(candidate.css_files)

    scope = str(request_scope or "").strip().lower()
    if scope and scope != GLOBAL_THEME_SCOPE:
        scoped_key = preferences.get(scope)
        if scoped_key:
            candidate = available_themes.get(scoped_key)
            if candidate is not None and _theme_supports_scope(candidate, scope):
                selected_scope_theme = candidate
                css_files.extend(candidate.css_files)

    deduplicated_css: list[str] = []
    for css_file in css_files:
        if css_file not in deduplicated_css:
            deduplicated_css.append(css_file)

    return UserThemeRuntimeState(
        preferences=preferences,
        selected_global_theme=selected_global_theme,
        selected_scope_theme=selected_scope_theme,
        css_files=tuple(deduplicated_css),
    )


def save_user_theme_preferences(*, user, submitted_preferences: object) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для выбора темы нужно войти в аккаунт.")

    normalized = normalize_theme_preferences(submitted_preferences)
    allowed_by_scope = get_user_theme_options_by_scope(user)
    filtered: dict[str, str] = {}
    for scope, theme_key in normalized.items():
        allowed_themes = allowed_by_scope.get(scope) or []
        if any(theme.key == theme_key for theme in allowed_themes):
            filtered[scope] = theme_key

    profile, _created = UserProfile.objects.get_or_create(user=user)
    if profile.theme_preferences != filtered:
        profile.theme_preferences = filtered
        profile.save(update_fields=["theme_preferences", "updated_at"])
    return filtered


def get_theme_store_cards_for_user(user) -> list[dict[str, object]]:
    role_access = get_user_role(user)
    runtime_themes = discover_runtime_themes()
    purchased = get_user_purchased_theme_keys(user)

    try:
        queryset = ThemeStoreListing.objects.filter(category=ThemeStoreListing.Category.THEMES).order_by("-updated_at", "-id")
        if not role_access.is_admin_plus:
            queryset = queryset.filter(status=ThemeStoreListing.Status.PUBLISHED, is_public=True)
        listings = list(queryset)
    except (OperationalError, ProgrammingError):
        return []

    cards: list[dict[str, object]] = []
    for listing in listings:
        theme = runtime_themes.get(listing.theme_key)
        runtime_available = theme is not None and is_runtime_theme_compatible(theme)
        already_owned = bool(theme and theme.is_public)
        if getattr(user, "is_authenticated", False) and listing.theme_key in purchased:
            already_owned = True
        if role_access.is_admin_plus:
            already_owned = True

        can_purchase = (
            getattr(user, "is_authenticated", False)
            and listing.status == ThemeStoreListing.Status.PUBLISHED
            and listing.is_public
            and runtime_available
            and not already_owned
        )

        cards.append(
            {
                "listing": listing,
                "runtime_theme": theme,
                "runtime_available": runtime_available,
                "already_owned": already_owned,
                "can_purchase": can_purchase,
                "theme_scopes": tuple(theme.scopes) if theme else tuple(),
            }
        )
    return cards


@transaction.atomic
def purchase_theme_with_balance(*, user, listing_id: int) -> UserThemePurchase:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для покупки темы нужно войти в аккаунт.")

    listing = ThemeStoreListing.objects.select_for_update().filter(id=listing_id).first()
    if listing is None:
        raise ThemePurchaseError("Тема в магазине не найдена.")
    if listing.status != ThemeStoreListing.Status.PUBLISHED or not listing.is_public:
        raise ThemePurchaseError("Эта тема сейчас недоступна для покупки.")

    theme = get_runtime_theme(listing.theme_key)
    if theme is None or not is_runtime_theme_compatible(theme):
        raise ThemePurchaseError("Runtime-пакет темы недоступен или несовместим с текущей версией проекта.")

    role_access = get_user_role(user)
    if theme.is_public or role_access.is_admin_plus:
        raise ThemePurchaseError("Эта тема уже доступна вашему аккаунту без покупки.")

    existing_purchase = (
        UserThemePurchase.objects.select_for_update()
        .filter(user=user, theme_key=listing.theme_key)
        .first()
    )
    if existing_purchase is not None:
        raise ThemePurchaseError("Эта тема уже была куплена ранее.")

    price = Decimal(str(listing.price_rub or "0.00")).quantize(Decimal("0.01"))
    if price < 0:
        raise ThemePurchaseError("Некорректная цена темы.")

    UserProfile.objects.select_for_update().get_or_create(user=user)
    if price > 0:
        try:
            adjust_balance(
                user=user,
                delta=-price,
                reason=LedgerReason.THEME_PURCHASE,
                amount_snapshot=price,
                idempotency_key=f"theme-purchase:{user.id}:{listing.id}:{uuid.uuid4()}",
            )
        except BalanceError as exc:
            if str(exc) == "Insufficient balance":
                raise ThemePurchaseError("Недостаточно средств на балансе профиля.") from exc
            raise ThemePurchaseError(str(exc)) from exc

    return UserThemePurchase.objects.create(
        user=user,
        listing=listing,
        theme_key=listing.theme_key,
        amount_rub=price,
    )
