from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Avg, Count, Sum
from django.template import TemplateDoesNotExist
from django.template.loader import get_template

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.rbac import get_user_role
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.subscriptions.feature_codes import SITE_STORE_FULL_ACCESS
from modules.v2.subscriptions.services import user_has_feature
from modules.v2.system.module_plugins import (
    RuntimeModuleManifest,
    get_runtime_module,
    get_runtime_module_slot_state,
    get_user_module_preferences,
    is_runtime_module_compatible,
    normalize_module_preferences,
)
from modules.v2.system.services import get_active_theme, is_module_enabled
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

from .models import (
    StoreAnalyticsEvent,
    StoreBundleItem,
    StoreProductEntitlement,
    StoreProductReview,
    ThemeStoreListing,
    UserBundlePurchase,
    UserStorePurchase,
    UserThemePurchase,
)
from .presentation import (
    build_review_presenter,
    build_star_rating_data,
    format_reviews_count_label,
    get_product_preview_url,
)


STEAM_CATALOG_DETAIL_CONTENT_SLOT = "steam_catalog.detail_content"
DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE = "steam_catalog/partials/detail_content.html"


class AccessSource:
    PUBLIC = "public"
    PURCHASE = "purchase"
    BUNDLE_PURCHASE = "bundle_purchase"
    SUBSCRIPTION = "subscription"
    ADMIN = "admin"
    NONE = "none"


class ThemePurchaseError(ValueError):
    pass


@dataclass(frozen=True)
class StoreProductAccessState:
    access_source: str
    is_owned_effective: bool
    is_purchased: bool
    is_bundle_owned: bool
    is_subscription_access: bool
    is_public_access: bool
    is_runtime_available: bool
    is_compatible: bool
    can_activate: bool
    is_active: bool


@dataclass(frozen=True)
class UserThemeRuntimeState:
    preferences: dict[str, str]
    selected_global_theme: RuntimeThemeManifest | None
    selected_scope_theme: RuntimeThemeManifest | None
    css_files: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeModuleSlotSelection:
    slot: str
    module_key: str
    template_path: str
    module: RuntimeModuleManifest
    access_state: StoreProductAccessState


@dataclass(frozen=True)
class UserRuntimeModuleState:
    preferences: dict[str, str]
    slot_states: dict[str, RuntimeModuleSlotSelection]
    css_files: tuple[str, ...]
    js_files: tuple[str, ...]


@dataclass(frozen=True)
class BundlePricingSummary:
    bundle_price_rub: Decimal
    separate_price_rub: Decimal
    savings_rub: Decimal


def _template_exists(name: str) -> bool:
    try:
        get_template(name)
        return True
    except TemplateDoesNotExist:
        return False


def is_store_runtime_available() -> bool:
    try:
        if not is_module_enabled("store"):
            return False
        ThemeStoreListing.objects.exists()
        return True
    except (OperationalError, ProgrammingError):
        return False


def get_store_listings(
    *,
    category: str | None = None,
    include_unpublished: bool = False,
    include_private: bool = False,
    submitted_by_id: int | None = None,
) -> list[ThemeStoreListing]:
    try:
        queryset = ThemeStoreListing.objects.all().order_by("-updated_at", "-id")
        if category:
            queryset = queryset.filter(category=category)
        if not include_unpublished:
            queryset = queryset.filter(status=ThemeStoreListing.Status.PUBLISHED)
        if not include_private:
            queryset = queryset.filter(is_public=True)
        if submitted_by_id:
            queryset = queryset.filter(submitted_by_id=submitted_by_id)
        return list(queryset)
    except (OperationalError, ProgrammingError):
        return []


def get_theme_store_listings(*, include_unpublished: bool = False) -> list[ThemeStoreListing]:
    return get_store_listings(
        category=ThemeStoreListing.Category.THEMES,
        include_unpublished=include_unpublished,
        include_private=False,
    )


def _get_listing_by_runtime_key(*, category: str, runtime_key: str) -> ThemeStoreListing | None:
    normalized_key = str(runtime_key or "").strip().lower()
    if not normalized_key:
        return None

    queryset = ThemeStoreListing.objects.filter(category=category)
    if category == ThemeStoreListing.Category.THEMES:
        queryset = queryset.filter(theme_key=normalized_key)
    elif category == ThemeStoreListing.Category.MODULES:
        queryset = queryset.filter(module_key=normalized_key)
    else:
        queryset = queryset.filter(bundle_key=normalized_key)

    # Runtime key can have multiple listing revisions; prefer the latest published row first.
    published_candidate = (
        queryset.filter(status=ThemeStoreListing.Status.PUBLISHED)
        .order_by("-updated_at", "-id")
        .first()
    )
    if published_candidate is not None:
        return published_candidate
    return queryset.order_by("-updated_at", "-id").first()


def _get_listing_runtime_state(listing: ThemeStoreListing) -> tuple[bool, bool]:
    if listing.category == ThemeStoreListing.Category.THEMES:
        runtime_theme = get_runtime_theme(listing.theme_key)
        if runtime_theme is None:
            return False, False
        return True, bool(is_runtime_theme_compatible(runtime_theme))

    if listing.category == ThemeStoreListing.Category.MODULES:
        runtime_module = get_runtime_module(listing.module_key)
        if runtime_module is None:
            return False, False
        active_theme = get_active_theme()
        return True, bool(is_runtime_module_compatible(runtime_module, active_theme_key=getattr(active_theme, "key", "")))

    if listing.category != ThemeStoreListing.Category.BUNDLES:
        return False, False

    bundle_items = list(
        StoreBundleItem.objects.filter(bundle=listing).order_by("sort_order", "id")
    )
    if not bundle_items:
        return True, True

    all_runtime_available = True
    all_compatible = True
    for item in bundle_items:
        category = ThemeStoreListing.Category.THEMES if item.product_type == StoreBundleItem.ProductType.THEME else ThemeStoreListing.Category.MODULES
        target_listing = _get_listing_by_runtime_key(category=category, runtime_key=item.product_key)
        if target_listing is None:
            all_runtime_available = False
            all_compatible = False
            break
        target_runtime_available, target_compatible = _get_listing_runtime_state(target_listing)
        all_runtime_available = all_runtime_available and target_runtime_available
        all_compatible = all_compatible and target_compatible
    return all_runtime_available, all_compatible


def _get_user_listing_entitlement_sources(user, listing: ThemeStoreListing) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()
    try:
        sources = set(
            StoreProductEntitlement.objects.filter(user=user, listing=listing).values_list("source", flat=True)
        )
    except (OperationalError, ProgrammingError):
        sources = set()

    if listing.category == ThemeStoreListing.Category.THEMES:
        try:
            if UserThemePurchase.objects.filter(user=user, theme_key=listing.theme_key).exists():
                sources.add(StoreProductEntitlement.Source.PURCHASE)
        except (OperationalError, ProgrammingError):
            pass
    return sources


def _is_listing_active_for_user(
    *,
    user,
    listing: ThemeStoreListing,
    slot: str = "",
) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False

    if listing.category == ThemeStoreListing.Category.THEMES:
        preferences = get_user_theme_preferences(user)
        return listing.theme_key in set(preferences.values())

    if listing.category == ThemeStoreListing.Category.MODULES:
        preferences = get_user_module_preferences(user)
        if slot:
            return preferences.get(str(slot or "").strip().lower()) == listing.module_key
        return listing.module_key in set(preferences.values())

    return False


def resolve_store_product_access_state(
    *,
    user,
    listing: ThemeStoreListing,
    slot: str = "",
) -> StoreProductAccessState:
    is_runtime_available, is_compatible = _get_listing_runtime_state(listing)

    if not is_store_runtime_available():
        return StoreProductAccessState(
            access_source=AccessSource.NONE,
            is_owned_effective=False,
            is_purchased=False,
            is_bundle_owned=False,
            is_subscription_access=False,
            is_public_access=False,
            is_runtime_available=is_runtime_available,
            is_compatible=is_compatible,
            can_activate=False,
            is_active=False,
        )

    role_access = get_user_role(user)
    entitlement_sources = _get_user_listing_entitlement_sources(user, listing)

    is_purchased = StoreProductEntitlement.Source.PURCHASE in entitlement_sources
    is_bundle_owned = StoreProductEntitlement.Source.BUNDLE_PURCHASE in entitlement_sources

    listing_price = Decimal(str(listing.price_rub or "0.00")).quantize(Decimal("0.01"))
    is_listing_publicly_visible = (
        listing.status == ThemeStoreListing.Status.PUBLISHED
        and listing.is_public
    )
    is_public_access = bool(is_listing_publicly_visible and listing_price <= Decimal("0.00"))

    is_subscription_access = False
    if is_listing_publicly_visible and listing.is_included_in_store_subscription and getattr(user, "is_authenticated", False):
        is_subscription_access = user_has_feature(user, SITE_STORE_FULL_ACCESS)

    access_source = AccessSource.NONE
    if is_purchased:
        access_source = AccessSource.PURCHASE
    elif is_bundle_owned:
        access_source = AccessSource.BUNDLE_PURCHASE
    elif is_subscription_access:
        access_source = AccessSource.SUBSCRIPTION
    elif is_public_access:
        access_source = AccessSource.PUBLIC
    elif role_access.is_admin_plus:
        access_source = AccessSource.ADMIN

    is_owned_effective = access_source != AccessSource.NONE
    can_activate = bool(
        is_owned_effective
        and listing.category in (ThemeStoreListing.Category.THEMES, ThemeStoreListing.Category.MODULES)
        and is_runtime_available
        and is_compatible
    )

    return StoreProductAccessState(
        access_source=access_source,
        is_owned_effective=is_owned_effective,
        is_purchased=is_purchased,
        is_bundle_owned=is_bundle_owned,
        is_subscription_access=is_subscription_access,
        is_public_access=is_public_access,
        is_runtime_available=is_runtime_available,
        is_compatible=is_compatible,
        can_activate=can_activate,
        is_active=_is_listing_active_for_user(user=user, listing=listing, slot=slot) if can_activate else False,
    )

def get_user_purchased_theme_keys(user) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()

    keys: set[str] = set()
    try:
        keys.update(
            key
            for key in UserThemePurchase.objects.filter(user=user).values_list("theme_key", flat=True)
            if key
        )
    except (OperationalError, ProgrammingError):
        return set()

    try:
        entitlement_rows = (
            StoreProductEntitlement.objects.filter(
                user=user,
                listing__category=ThemeStoreListing.Category.THEMES,
            )
            .select_related("listing")
            .values_list("listing__theme_key", flat=True)
        )
        keys.update({key for key in entitlement_rows if key})
    except (OperationalError, ProgrammingError):
        pass
    return keys


def get_user_runtime_theme_map(user) -> dict[str, RuntimeThemeManifest]:
    if not is_store_runtime_available():
        return {}

    runtime_themes = discover_runtime_themes()
    role_access = get_user_role(user)

    try:
        listing_queryset = ThemeStoreListing.objects.filter(category=ThemeStoreListing.Category.THEMES)
        if not role_access.is_admin_plus:
            listing_queryset = listing_queryset.filter(status=ThemeStoreListing.Status.PUBLISHED)
        listing_map = {
            listing.theme_key: listing
            for listing in listing_queryset.order_by("id")
            if listing.theme_key
        }
    except (OperationalError, ProgrammingError):
        return {}

    result: dict[str, RuntimeThemeManifest] = {}
    for key, theme in runtime_themes.items():
        listing = listing_map.get(key)
        if listing is None:
            continue

        access_state = resolve_store_product_access_state(user=user, listing=listing)
        if access_state.is_owned_effective and access_state.is_runtime_available and access_state.is_compatible:
            result[key] = theme
    return result


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
    if not is_store_runtime_available():
        return UserThemeRuntimeState(
            preferences={},
            selected_global_theme=None,
            selected_scope_theme=None,
            css_files=tuple(),
        )

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


def resolve_user_runtime_module_state(*, user, request_scope: str) -> UserRuntimeModuleState:
    preferences = get_user_module_preferences(user)

    if not is_store_runtime_available():
        return UserRuntimeModuleState(
            preferences=preferences,
            slot_states={},
            css_files=tuple(),
            js_files=tuple(),
        )

    slot_states: dict[str, RuntimeModuleSlotSelection] = {}
    css_files: list[str] = []
    js_files: list[str] = []

    for slot, module_key in preferences.items():
        runtime_slot = get_runtime_module_slot_state(slot=slot, module_key=module_key)
        if runtime_slot.module is None or runtime_slot.placement is None:
            continue

        module = runtime_slot.module
        if module.target_scopes and str(request_scope or "").strip().lower() not in module.target_scopes:
            continue

        listing = _get_listing_by_runtime_key(
            category=ThemeStoreListing.Category.MODULES,
            runtime_key=module.key,
        )
        if listing is None:
            continue

        access_state = resolve_store_product_access_state(user=user, listing=listing, slot=slot)
        if not access_state.can_activate:
            continue

        if not _template_exists(runtime_slot.placement.template):
            continue

        slot_states[slot] = RuntimeModuleSlotSelection(
            slot=slot,
            module_key=module.key,
            template_path=runtime_slot.placement.template,
            module=module,
            access_state=access_state,
        )

        for css_file in module.css_files:
            if css_file not in css_files:
                css_files.append(css_file)
        for js_file in module.js_files:
            if js_file not in js_files:
                js_files.append(js_file)

    return UserRuntimeModuleState(
        preferences=preferences,
        slot_states=slot_states,
        css_files=tuple(css_files),
        js_files=tuple(js_files),
    )


def resolve_store_module_slot_template(
    *,
    user,
    slot: str,
    request_scope: str,
    default_template: str,
) -> str:
    runtime_state = resolve_user_runtime_module_state(user=user, request_scope=request_scope)
    slot_state = runtime_state.slot_states.get(str(slot or "").strip().lower())
    if slot_state is None:
        return default_template
    return slot_state.template_path

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


def save_user_module_preferences(*, user, submitted_preferences: object) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для выбора модулей нужно войти в аккаунт.")

    normalized = normalize_module_preferences(submitted_preferences)
    filtered: dict[str, str] = {}

    for slot, module_key in normalized.items():
        runtime_slot = get_runtime_module_slot_state(slot=slot, module_key=module_key)
        if runtime_slot.module is None or runtime_slot.placement is None:
            continue

        listing = _get_listing_by_runtime_key(category=ThemeStoreListing.Category.MODULES, runtime_key=module_key)
        if listing is None:
            continue

        access_state = resolve_store_product_access_state(user=user, listing=listing, slot=slot)
        if access_state.can_activate:
            filtered[slot] = module_key

    profile, _created = UserProfile.objects.get_or_create(user=user)
    if profile.module_preferences != filtered:
        profile.module_preferences = filtered
        profile.save(update_fields=["module_preferences", "updated_at"])
    return filtered


def _build_card_badges(access_state: StoreProductAccessState, listing: ThemeStoreListing) -> list[str]:
    badges: list[str] = []
    if access_state.is_purchased:
        badges.append("Куплено")
    elif access_state.is_bundle_owned:
        badges.append("Куплено в наборе")
    elif access_state.is_subscription_access:
        badges.append("В подписке")
    elif access_state.is_public_access:
        badges.append("Бесплатно" if listing.price_rub == 0 else "Публично")

    if access_state.is_active:
        badges.append("Активно")
    if not access_state.is_compatible:
        badges.append("Несовместимо")
    if not access_state.is_runtime_available:
        badges.append("Runtime missing")
    return badges


def get_store_card_for_user(
    *,
    user,
    listing: ThemeStoreListing,
    slot: str = "",
    reviews_summary_map: dict[int, dict[str, object]] | None = None,
) -> dict[str, object]:
    access_state = resolve_store_product_access_state(user=user, listing=listing, slot=slot)
    review_summary = (
        reviews_summary_map.get(listing.id, {})
        if reviews_summary_map is not None
        else get_listing_reviews_summary(listing)
    )
    if not review_summary:
        review_summary = get_listing_reviews_summary(listing)

    can_purchase = bool(
        is_store_runtime_available()
        and getattr(user, "is_authenticated", False)
        and listing.status == ThemeStoreListing.Status.PUBLISHED
        and listing.is_public
        and listing.category in (ThemeStoreListing.Category.THEMES, ThemeStoreListing.Category.MODULES)
        and not access_state.is_purchased
        and not access_state.is_bundle_owned
    )
    can_purchase_bundle = bool(
        is_store_runtime_available()
        and getattr(user, "is_authenticated", False)
        and listing.status == ThemeStoreListing.Status.PUBLISHED
        and listing.is_public
        and listing.category == ThemeStoreListing.Category.BUNDLES
        and not access_state.is_bundle_owned
    )
    can_manage = can_manage_store_listing(user=user, listing=listing)

    return {
        "listing": listing,
        "access_state": access_state,
        "preview_url": get_product_preview_url(listing),
        "fallback_preview_url": get_product_preview_url(None),
        "reviews_summary": review_summary,
        "is_owned": bool(access_state.is_purchased or access_state.is_bundle_owned),
        "badges": _build_card_badges(access_state, listing),
        "can_purchase": can_purchase,
        "can_purchase_bundle": can_purchase_bundle,
        "can_activate": access_state.can_activate,
        "can_manage": can_manage,
    }


def get_theme_store_cards_for_user(user) -> list[dict[str, object]]:
    role_access = get_user_role(user)

    try:
        queryset = ThemeStoreListing.objects.filter(category=ThemeStoreListing.Category.THEMES).order_by("-updated_at", "-id")
        if not role_access.is_admin_plus:
            queryset = queryset.filter(status=ThemeStoreListing.Status.PUBLISHED, is_public=True)
        listings = list(queryset)
    except (OperationalError, ProgrammingError):
        return []

    cards: list[dict[str, object]] = []
    runtime_themes = discover_runtime_themes()
    for listing in listings:
        theme = runtime_themes.get(listing.theme_key)
        card = get_store_card_for_user(user=user, listing=listing)
        card["runtime_theme"] = theme
        card["runtime_available"] = card["access_state"].is_runtime_available
        card["already_owned"] = card["access_state"].is_owned_effective
        card["theme_scopes"] = tuple(theme.scopes) if theme else tuple()
        cards.append(card)
    return cards


def get_storefront_cards_for_user(user) -> dict[str, list[dict[str, object]]]:
    role_access = get_user_role(user)
    include_unpublished = role_access.is_admin_plus
    include_private = role_access.is_admin_plus

    grouped: dict[str, list[dict[str, object]]] = {
        ThemeStoreListing.Category.THEMES: [],
        ThemeStoreListing.Category.MODULES: [],
        ThemeStoreListing.Category.BUNDLES: [],
    }

    for category in grouped:
        listings = get_store_listings(
            category=category,
            include_unpublished=include_unpublished,
            include_private=include_private,
        )
        summary_map = get_listings_reviews_summary_map(listings)
        grouped[category] = [
            get_store_card_for_user(
                user=user,
                listing=listing,
                reviews_summary_map=summary_map,
            )
            for listing in listings
        ]

    return grouped


def get_store_listing_by_key(*, key: str) -> ThemeStoreListing | None:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return None
    try:
        return ThemeStoreListing.objects.filter(key=normalized).first()
    except (OperationalError, ProgrammingError):
        return None


def get_bundle_pricing_summary(bundle_listing: ThemeStoreListing) -> BundlePricingSummary:
    bundle_price = Decimal(str(bundle_listing.price_rub or "0.00")).quantize(Decimal("0.01"))

    separate_price = Decimal("0.00")
    bundle_items = list(StoreBundleItem.objects.filter(bundle=bundle_listing))
    for item in bundle_items:
        category = ThemeStoreListing.Category.THEMES if item.product_type == StoreBundleItem.ProductType.THEME else ThemeStoreListing.Category.MODULES
        target_listing = _get_listing_by_runtime_key(category=category, runtime_key=item.product_key)
        if target_listing is None:
            continue
        separate_price += Decimal(str(target_listing.price_rub or "0.00")).quantize(Decimal("0.01"))

    savings = (separate_price - bundle_price).quantize(Decimal("0.01"))
    if savings < 0:
        savings = Decimal("0.00")

    return BundlePricingSummary(
        bundle_price_rub=bundle_price,
        separate_price_rub=separate_price.quantize(Decimal("0.01")),
        savings_rub=savings,
    )


def _ensure_store_runtime_or_raise() -> None:
    if not is_store_runtime_available():
        raise ThemePurchaseError("Store runtime недоступен. Модуль магазина выключен или неинициализирован.")


def _log_store_event(
    *,
    event_type: str,
    access_source: str,
    user=None,
    listing: ThemeStoreListing | None = None,
    amount_rub: Decimal | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    try:
        StoreAnalyticsEvent.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            listing=listing,
            event_type=event_type,
            access_source=access_source,
            amount_rub=amount_rub,
            payload=payload or {},
        )
    except (OperationalError, ProgrammingError):
        return

@transaction.atomic
def purchase_store_listing_with_balance(*, user, listing_id: int) -> UserStorePurchase:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для покупки нужно войти в аккаунт.")

    _ensure_store_runtime_or_raise()

    listing = ThemeStoreListing.objects.select_for_update().filter(id=listing_id).first()
    if listing is None:
        raise ThemePurchaseError("Товар в магазине не найден.")
    if listing.category not in (ThemeStoreListing.Category.THEMES, ThemeStoreListing.Category.MODULES):
        raise ThemePurchaseError("Этот товар покупается через bundle flow.")
    if listing.status != ThemeStoreListing.Status.PUBLISHED or not listing.is_public:
        raise ThemePurchaseError("Этот товар сейчас недоступен для покупки.")

    access_state = resolve_store_product_access_state(user=user, listing=listing)
    if access_state.is_purchased or access_state.is_bundle_owned:
        raise ThemePurchaseError("Этот товар уже доступен вашему аккаунту.")

    if not access_state.is_runtime_available or not access_state.is_compatible:
        raise ThemePurchaseError("Runtime-пакет товара недоступен или несовместим.")

    price = Decimal(str(listing.price_rub or "0.00")).quantize(Decimal("0.01"))
    if price < 0:
        raise ThemePurchaseError("Некорректная цена товара.")

    UserProfile.objects.select_for_update().get_or_create(user=user)
    if price > 0:
        try:
            adjust_balance(
                user=user,
                delta=-price,
                reason=LedgerReason.STORE_PURCHASE,
                amount_snapshot=price,
                idempotency_key=f"store-purchase:{user.id}:{listing.id}:{uuid.uuid4()}",
            )
        except BalanceError as exc:
            if str(exc) == "Insufficient balance":
                raise ThemePurchaseError("Недостаточно средств на балансе профиля.") from exc
            raise ThemePurchaseError(str(exc)) from exc

    purchase, _created = UserStorePurchase.objects.get_or_create(
        user=user,
        listing=listing,
        defaults={
            "category": listing.category,
            "product_key": listing.runtime_key,
            "amount_rub": price,
        },
    )

    StoreProductEntitlement.objects.get_or_create(
        user=user,
        listing=listing,
        source=StoreProductEntitlement.Source.PURCHASE,
    )

    if listing.category == ThemeStoreListing.Category.THEMES:
        UserThemePurchase.objects.get_or_create(
            user=user,
            listing=listing,
            theme_key=listing.theme_key,
            defaults={"amount_rub": price},
        )

    _log_store_event(
        event_type=StoreAnalyticsEvent.EventType.INDIVIDUAL_PURCHASE,
        access_source=AccessSource.PURCHASE,
        user=user,
        listing=listing,
        amount_rub=price,
        payload={"listing_key": listing.key, "category": listing.category},
    )

    return purchase


@transaction.atomic
def purchase_bundle_with_balance(*, user, listing_id: int) -> UserBundlePurchase:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для покупки набора нужно войти в аккаунт.")

    _ensure_store_runtime_or_raise()

    listing = ThemeStoreListing.objects.select_for_update().filter(id=listing_id).first()
    if listing is None or listing.category != ThemeStoreListing.Category.BUNDLES:
        raise ThemePurchaseError("Набор в магазине не найден.")
    if listing.status != ThemeStoreListing.Status.PUBLISHED or not listing.is_public:
        raise ThemePurchaseError("Этот набор сейчас недоступен для покупки.")

    if UserBundlePurchase.objects.select_for_update().filter(user=user, bundle_key=listing.bundle_key).exists():
        raise ThemePurchaseError("Этот набор уже куплен ранее.")

    price = Decimal(str(listing.price_rub or "0.00")).quantize(Decimal("0.01"))
    if price < 0:
        raise ThemePurchaseError("Некорректная цена набора.")

    UserProfile.objects.select_for_update().get_or_create(user=user)
    if price > 0:
        try:
            adjust_balance(
                user=user,
                delta=-price,
                reason=LedgerReason.BUNDLE_PURCHASE,
                amount_snapshot=price,
                idempotency_key=f"bundle-purchase:{user.id}:{listing.id}:{uuid.uuid4()}",
            )
        except BalanceError as exc:
            if str(exc) == "Insufficient balance":
                raise ThemePurchaseError("Недостаточно средств на балансе профиля.") from exc
            raise ThemePurchaseError(str(exc)) from exc

    bundle_purchase = UserBundlePurchase.objects.create(
        user=user,
        bundle_listing=listing,
        bundle_key=listing.bundle_key,
        amount_rub=price,
    )

    bundle_items = list(StoreBundleItem.objects.select_for_update().filter(bundle=listing).order_by("sort_order", "id"))
    for item in bundle_items:
        category = ThemeStoreListing.Category.THEMES if item.product_type == StoreBundleItem.ProductType.THEME else ThemeStoreListing.Category.MODULES
        target_listing = _get_listing_by_runtime_key(category=category, runtime_key=item.product_key)
        if target_listing is None:
            continue
        StoreProductEntitlement.objects.get_or_create(
            user=user,
            listing=target_listing,
            source=StoreProductEntitlement.Source.BUNDLE_PURCHASE,
        )

    _log_store_event(
        event_type=StoreAnalyticsEvent.EventType.BUNDLE_PURCHASE,
        access_source=AccessSource.BUNDLE_PURCHASE,
        user=user,
        listing=listing,
        amount_rub=price,
        payload={"listing_key": listing.key, "bundle_key": listing.bundle_key},
    )

    return bundle_purchase


@transaction.atomic
def purchase_theme_with_balance(*, user, listing_id: int) -> UserThemePurchase:
    purchase = purchase_store_listing_with_balance(user=user, listing_id=listing_id)
    listing = purchase.listing
    if listing.category != ThemeStoreListing.Category.THEMES:
        raise ThemePurchaseError("Выбранный товар не относится к темам.")

    theme_purchase, _created = UserThemePurchase.objects.get_or_create(
        user=user,
        listing=listing,
        theme_key=listing.theme_key,
        defaults={"amount_rub": purchase.amount_rub},
    )
    return theme_purchase

@transaction.atomic
def activate_theme_globally(*, user, listing_id: int) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для активации темы нужно войти в аккаунт.")

    listing = ThemeStoreListing.objects.filter(id=listing_id, category=ThemeStoreListing.Category.THEMES).first()
    if listing is None:
        raise ThemePurchaseError("Тема не найдена.")

    access_state = resolve_store_product_access_state(user=user, listing=listing)
    if not access_state.can_activate:
        raise ThemePurchaseError("Тема недоступна для активации.")

    runtime_theme = get_runtime_theme(listing.theme_key)
    if runtime_theme is None or GLOBAL_THEME_SCOPE not in runtime_theme.scopes:
        raise ThemePurchaseError("Эта тема не поддерживает global scope.")

    profile, _created = UserProfile.objects.get_or_create(user=user)
    preferences = normalize_theme_preferences(profile.theme_preferences or {})
    preferences[GLOBAL_THEME_SCOPE] = listing.theme_key
    profile.theme_preferences = preferences
    profile.save(update_fields=["theme_preferences", "updated_at"])

    _log_store_event(
        event_type=StoreAnalyticsEvent.EventType.ACTIVATION,
        access_source=access_state.access_source,
        user=user,
        listing=listing,
        payload={"scope": GLOBAL_THEME_SCOPE},
    )

    return preferences


@transaction.atomic
def deactivate_theme_globally(*, user) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для деактивации темы нужно войти в аккаунт.")

    profile, _created = UserProfile.objects.get_or_create(user=user)
    preferences = normalize_theme_preferences(profile.theme_preferences or {})
    previous_theme_key = preferences.pop(GLOBAL_THEME_SCOPE, "")
    profile.theme_preferences = preferences
    profile.save(update_fields=["theme_preferences", "updated_at"])

    if previous_theme_key:
        listing = _get_listing_by_runtime_key(category=ThemeStoreListing.Category.THEMES, runtime_key=previous_theme_key)
        _log_store_event(
            event_type=StoreAnalyticsEvent.EventType.DEACTIVATION,
            access_source=AccessSource.NONE,
            user=user,
            listing=listing,
            payload={"scope": GLOBAL_THEME_SCOPE, "theme_key": previous_theme_key},
        )

    return preferences


@transaction.atomic
def activate_module_slot(*, user, listing_id: int, slot: str) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для активации модуля нужно войти в аккаунт.")

    normalized_slot = str(slot or "").strip().lower()
    if not normalized_slot:
        raise ThemePurchaseError("Slot не указан.")

    listing = ThemeStoreListing.objects.filter(id=listing_id, category=ThemeStoreListing.Category.MODULES).first()
    if listing is None:
        raise ThemePurchaseError("Модуль не найден.")

    runtime_slot = get_runtime_module_slot_state(slot=normalized_slot, module_key=listing.module_key)
    if runtime_slot.module is None or runtime_slot.placement is None:
        raise ThemePurchaseError("Модуль не поддерживает выбранный slot.")

    access_state = resolve_store_product_access_state(user=user, listing=listing, slot=normalized_slot)
    if not access_state.can_activate:
        raise ThemePurchaseError("Модуль недоступен для активации.")

    profile, _created = UserProfile.objects.get_or_create(user=user)
    preferences = normalize_module_preferences(profile.module_preferences or {})
    preferences[normalized_slot] = listing.module_key
    profile.module_preferences = preferences
    profile.save(update_fields=["module_preferences", "updated_at"])

    _log_store_event(
        event_type=StoreAnalyticsEvent.EventType.ACTIVATION,
        access_source=access_state.access_source,
        user=user,
        listing=listing,
        payload={"slot": normalized_slot},
    )

    return preferences


@transaction.atomic
def deactivate_module_slot(*, user, slot: str) -> dict[str, str]:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для деактивации модуля нужно войти в аккаунт.")

    normalized_slot = str(slot or "").strip().lower()
    if not normalized_slot:
        raise ThemePurchaseError("Slot не указан.")

    profile, _created = UserProfile.objects.get_or_create(user=user)
    preferences = normalize_module_preferences(profile.module_preferences or {})
    previous_key = preferences.pop(normalized_slot, "")
    profile.module_preferences = preferences
    profile.save(update_fields=["module_preferences", "updated_at"])

    if previous_key:
        listing = _get_listing_by_runtime_key(category=ThemeStoreListing.Category.MODULES, runtime_key=previous_key)
        _log_store_event(
            event_type=StoreAnalyticsEvent.EventType.DEACTIVATION,
            access_source=AccessSource.NONE,
            user=user,
            listing=listing,
            payload={"slot": normalized_slot, "module_key": previous_key},
        )

    return preferences


def is_user_store_author(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    role_access = get_user_role(user)
    if role_access.is_admin_plus:
        return True
    profile = getattr(user, "profile_v2", None)
    if profile is None:
        try:
            profile = user.profile_v2
        except UserProfile.DoesNotExist:
            return False
    return bool(profile.is_store_author)


def can_manage_store_listing(*, user, listing: ThemeStoreListing) -> bool:
    role_access = get_user_role(user)
    if role_access.is_admin_plus:
        return True
    return bool(
        is_user_store_author(user)
        and listing.submitted_by_id
        and listing.submitted_by_id == getattr(user, "id", None)
    )


@transaction.atomic
def submit_listing_for_moderation(*, user, listing: ThemeStoreListing) -> ThemeStoreListing:
    if not can_manage_store_listing(user=user, listing=listing):
        raise ThemePurchaseError("Недостаточно прав для отправки на модерацию.")

    listing.status = ThemeStoreListing.Status.PENDING_MODERATION
    listing.moderation_comment = ""
    listing.save(update_fields=["status", "moderation_comment", "updated_at"])
    return listing


@transaction.atomic
def moderate_listing(*, user, listing: ThemeStoreListing, action: str, comment: str = "") -> ThemeStoreListing:
    role_access = get_user_role(user)
    if not role_access.is_admin_plus:
        raise ThemePurchaseError("Модерация доступна только admin+.")

    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "reject"}:
        raise ThemePurchaseError("Неизвестное действие модерации.")

    listing.status = ThemeStoreListing.Status.PUBLISHED if normalized_action == "approve" else ThemeStoreListing.Status.REJECTED
    listing.moderation_comment = (comment or "").strip()
    listing.moderated_by = user
    listing.save(update_fields=["status", "moderation_comment", "moderated_by", "updated_at"])
    return listing

def submit_store_review(
    *,
    user,
    listing: ThemeStoreListing,
    rating: int,
    comment: str,
    improvement_suggestion: str,
) -> StoreProductReview:
    if not getattr(user, "is_authenticated", False):
        raise ThemePurchaseError("Для отзыва нужно войти в аккаунт.")

    access_state = resolve_store_product_access_state(user=user, listing=listing)
    if access_state.access_source in {AccessSource.NONE, AccessSource.PUBLIC}:
        raise ThemePurchaseError("Оставлять отзыв можно только при валидном доступе к товару.")

    review, _created = StoreProductReview.objects.update_or_create(
        listing=listing,
        user=user,
        defaults={
            "rating": int(rating),
            "comment": (comment or "").strip(),
            "improvement_suggestion": (improvement_suggestion or "").strip(),
            "is_visible": True,
            "access_source_snapshot": access_state.access_source,
        },
    )

    _log_store_event(
        event_type=StoreAnalyticsEvent.EventType.REVIEW_CREATED,
        access_source=access_state.access_source,
        user=user,
        listing=listing,
        payload={"rating": review.rating},
    )

    return review


def get_listing_reviews_summary(listing: ThemeStoreListing) -> dict[str, object]:
    aggregate = listing.reviews.filter(is_visible=True).aggregate(avg_rating=Avg("rating"), total_reviews=Count("id"))
    avg_rating = aggregate.get("avg_rating") or Decimal("0.00")
    total_reviews = int(aggregate.get("total_reviews") or 0)
    rating_data = build_star_rating_data(avg_rating, precision=1)
    return {
        "avg_rating": avg_rating,
        "total_reviews": total_reviews,
        "avg_rating_label": rating_data["value_label"],
        "avg_rating_normalized": rating_data["normalized_value"],
        "avg_rating_stars": rating_data["states"],
        "total_reviews_label": format_reviews_count_label(total_reviews),
    }


def get_listings_reviews_summary_map(listings: list[ThemeStoreListing]) -> dict[int, dict[str, object]]:
    listing_ids = [listing.id for listing in listings if listing and listing.id]
    if not listing_ids:
        return {}

    aggregates = {
        row["listing_id"]: row
        for row in (
            StoreProductReview.objects.filter(listing_id__in=listing_ids, is_visible=True)
            .values("listing_id")
            .annotate(avg_rating=Avg("rating"), total_reviews=Count("id"))
        )
    }

    summary_map: dict[int, dict[str, object]] = {}
    for listing in listings:
        row = aggregates.get(listing.id, {})
        avg_rating = row.get("avg_rating") or Decimal("0.00")
        total_reviews = int(row.get("total_reviews") or 0)
        rating_data = build_star_rating_data(avg_rating, precision=1)
        summary_map[listing.id] = {
            "avg_rating": avg_rating,
            "total_reviews": total_reviews,
            "avg_rating_label": rating_data["value_label"],
            "avg_rating_normalized": rating_data["normalized_value"],
            "avg_rating_stars": rating_data["states"],
            "total_reviews_label": format_reviews_count_label(total_reviews),
        }
    return summary_map


def get_listing_reviews_for_display(listing: ThemeStoreListing) -> list[dict[str, object]]:
    reviews = list(
        listing.reviews.filter(is_visible=True)
        .select_related("user", "user__profile_v2")
        .order_by("-created_at", "-id")
    )
    return [build_review_presenter(review) for review in reviews]


def get_bundle_items_for_display(bundle_listing: ThemeStoreListing) -> list[dict[str, object]]:
    bundle_items = list(StoreBundleItem.objects.filter(bundle=bundle_listing).order_by("sort_order", "id"))
    type_labels = {
        StoreBundleItem.ProductType.THEME: "Тема",
        StoreBundleItem.ProductType.MODULE: "Модуль",
    }
    result: list[dict[str, object]] = []
    for item in bundle_items:
        category = (
            ThemeStoreListing.Category.THEMES
            if item.product_type == StoreBundleItem.ProductType.THEME
            else ThemeStoreListing.Category.MODULES
        )
        target_listing = _get_listing_by_runtime_key(category=category, runtime_key=item.product_key)
        result.append(
            {
                "product_type": item.product_type,
                "product_type_label": type_labels.get(item.product_type, "Продукт"),
                "product_title": target_listing.title if target_listing else "Недоступный продукт",
            }
        )
    return result


def get_user_portfolio_cards(user) -> list[dict[str, object]]:
    role_access = get_user_role(user)
    include_unpublished = role_access.is_admin_plus
    include_private = role_access.is_admin_plus
    listings = get_store_listings(include_unpublished=include_unpublished, include_private=include_private)
    summary_map = get_listings_reviews_summary_map(listings)
    cards = [
        get_store_card_for_user(
            user=user,
            listing=listing,
            reviews_summary_map=summary_map,
        )
        for listing in listings
    ]
    cards = [card for card in cards if card["access_state"].is_owned_effective or card["access_state"].is_public_access]
    return cards


def get_store_analytics_rows(user) -> list[dict[str, object]]:
    role_access = get_user_role(user)
    queryset = ThemeStoreListing.objects.all()

    if not role_access.is_admin_plus:
        if not is_user_store_author(user):
            return []
        queryset = queryset.filter(submitted_by=user)

    rows: list[dict[str, object]] = []
    for listing in queryset.order_by("-updated_at", "-id"):
        purchase_stats = listing.purchase_records.aggregate(
            purchases=Count("id"),
            revenue=Sum("amount_rub"),
        )
        bundle_stats = listing.bundle_purchase_records.aggregate(
            bundle_purchases=Count("id"),
            bundle_revenue=Sum("amount_rub"),
        )
        review_stats = listing.reviews.filter(is_visible=True).aggregate(
            reviews=Count("id"),
            avg_rating=Avg("rating"),
        )

        rows.append(
            {
                "listing": listing,
                "purchases": purchase_stats.get("purchases") or 0,
                "revenue": purchase_stats.get("revenue") or Decimal("0.00"),
                "bundle_purchases": bundle_stats.get("bundle_purchases") or 0,
                "bundle_revenue": bundle_stats.get("bundle_revenue") or Decimal("0.00"),
                "reviews": review_stats.get("reviews") or 0,
                "avg_rating": review_stats.get("avg_rating") or Decimal("0.00"),
            }
        )
    return rows


def get_store_listing_by_id(listing_id: int) -> ThemeStoreListing | None:
    try:
        return ThemeStoreListing.objects.filter(id=listing_id).first()
    except (OperationalError, ProgrammingError):
        return None
