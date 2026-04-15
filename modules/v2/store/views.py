from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.module_registry import MODULE_REGISTRY
from modules.v2.system.services import build_page_title

from .models import ThemeStoreListing
from .services import (
    ThemePurchaseError,
    activate_module_slot,
    activate_theme_globally,
    can_manage_store_listing,
    deactivate_module_slot,
    deactivate_theme_globally,
    get_bundle_pricing_summary,
    get_bundle_items_for_display,
    get_listing_reviews_summary,
    get_listing_reviews_for_display,
    get_store_analytics_rows,
    get_store_card_for_user,
    get_store_listing_by_id,
    get_store_listing_by_key,
    get_storefront_cards_for_user,
    get_user_portfolio_cards,
    is_store_runtime_available,
    is_user_store_author,
    moderate_listing,
    purchase_bundle_with_balance,
    purchase_store_listing_with_balance,
    purchase_theme_with_balance,
    submit_listing_for_moderation,
    submit_store_review,
)


def _render_store_unavailable(request, *, reason: str = ""):
    is_authenticated = bool(getattr(request.user, "is_authenticated", False))
    dashboard_url = reverse("frontend:dashboard_preview") if is_authenticated else reverse("frontend:home")
    back_url = (request.META.get("HTTP_REFERER") or "").strip() or dashboard_url
    module = MODULE_REGISTRY.get("store")
    response = render(
        request,
        "system/module_disabled.html",
        {
            "module_disabled_layout_template": "layouts/base_dashboard.html" if is_authenticated else "layouts/base_public.html",
            "module_disabled_is_dashboard": is_authenticated,
            "module_disabled_module_label": module.label if module else "Store",
            "module_disabled_reason": reason or "Store runtime unavailable",
            "module_disabled_dashboard_url": dashboard_url,
            "module_disabled_back_url": back_url,
        },
        status=404,
    )
    setattr(response, "_caseup_skip_global_404", True)
    return response


def _resolve_safe_next_url(request) -> str:
    fallback_url = reverse("store:storefront")
    for candidate in (
        request.POST.get("next_url"),
        request.META.get("HTTP_REFERER"),
    ):
        next_url = str(candidate or "").strip()
        if not next_url:
            continue
        if url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return next_url
    return fallback_url


def _parse_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


@login_required
def storefront_page(request):
    if not is_store_runtime_available():
        return _render_store_unavailable(request, reason="Store module is disabled or runtime not initialized.")

    cards_by_category = get_storefront_cards_for_user(request.user)
    context = {
        "theme_cards": cards_by_category.get(ThemeStoreListing.Category.THEMES, []),
        "module_cards": cards_by_category.get(ThemeStoreListing.Category.MODULES, []),
        "bundle_cards": cards_by_category.get(ThemeStoreListing.Category.BUNDLES, []),
        "is_store_author": is_user_store_author(request.user),
    }
    return render_dashboard_page(
        request,
        full_template="store/storefront.html",
        partial_template="store/partials/storefront_content.html",
        context=context,
        page_title=build_page_title("Store"),
        active_menu_key="store",
    )


@login_required
def library_page(request):
    if not is_store_runtime_available():
        return _render_store_unavailable(request, reason="Store runtime is unavailable.")

    cards = get_user_portfolio_cards(request.user)
    fallback_cards = [
        {
            "label": "Default theme",
            "status": "Активно по умолчанию",
            "description": "Системный fallback для global presentation.",
        },
        {
            "label": "Default steam_catalog detail",
            "status": "No module override",
            "description": "Fallback для slot steam_catalog.detail_content при недоступном runtime module.",
        },
    ]
    return render_dashboard_page(
        request,
        full_template="store/portfolio.html",
        partial_template="store/partials/portfolio_content.html",
        context={
            "portfolio_cards": cards,
            "fallback_cards": fallback_cards,
        },
        page_title=build_page_title("My Library"),
        active_menu_key="store",
    )


@login_required
def product_detail_page(request, listing_key: str):
    if not is_store_runtime_available():
        return _render_store_unavailable(request, reason="Store runtime is unavailable.")

    listing = get_store_listing_by_key(key=listing_key)
    if listing is None:
        messages.error(request, "Товар не найден.")
        return redirect("store:storefront")

    card = get_store_card_for_user(user=request.user, listing=listing)
    reviews_summary = get_listing_reviews_summary(listing)
    reviews = get_listing_reviews_for_display(listing)

    bundle_items = []
    bundle_summary = None
    if listing.category == ThemeStoreListing.Category.BUNDLES:
        bundle_items = get_bundle_items_for_display(listing)
        bundle_summary = get_bundle_pricing_summary(listing)

    return render_dashboard_page(
        request,
        full_template="store/product_detail.html",
        partial_template="store/partials/product_detail_content.html",
        context={
            "product_card": card,
            "reviews": reviews,
            "reviews_summary": reviews_summary,
            "bundle_items": bundle_items,
            "bundle_summary": bundle_summary,
        },
        page_title=build_page_title(listing.title),
        active_menu_key="store",
    )

@login_required
@require_POST
def purchase_theme_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    try:
        purchase = purchase_theme_with_balance(user=request.user, listing_id=listing_id)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Тема '{purchase.listing.title}' куплена и добавлена в библиотеку.",
        )
    return redirect(next_url)


@login_required
@require_POST
def purchase_product_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    try:
        purchase = purchase_store_listing_with_balance(user=request.user, listing_id=listing_id)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Товар '{purchase.listing.title}' успешно куплен.")
    return redirect(next_url)


@login_required
@require_POST
def purchase_bundle_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    try:
        purchase = purchase_bundle_with_balance(user=request.user, listing_id=listing_id)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Набор '{purchase.bundle_listing.title}' успешно куплен.")
    return redirect(next_url)


@login_required
@require_POST
def activate_theme_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    try:
        activate_theme_globally(user=request.user, listing_id=listing_id)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Тема активирована для global scope.")
    return redirect(next_url)


@login_required
@require_POST
def deactivate_theme_page(request):
    next_url = _resolve_safe_next_url(request)
    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    try:
        deactivate_theme_globally(user=request.user)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Global-тема сброшена на default fallback.")
    return redirect(next_url)


@login_required
@require_POST
def activate_module_slot_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    slot = str(request.POST.get("slot") or "").strip().lower()
    if not slot:
        messages.error(request, "Slot для активации не указан.")
        return redirect(next_url)

    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    try:
        activate_module_slot(user=request.user, listing_id=listing_id, slot=slot)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Модуль активирован для slot {slot}.")
    return redirect(next_url)


@login_required
@require_POST
def deactivate_module_slot_page(request):
    next_url = _resolve_safe_next_url(request)
    slot = str(request.POST.get("slot") or "").strip().lower()
    if not slot:
        messages.error(request, "Slot для деактивации не указан.")
        return redirect(next_url)

    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    try:
        deactivate_module_slot(user=request.user, slot=slot)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Модульный override для slot {slot} отключён.")
    return redirect(next_url)


@login_required
@require_POST
def add_review_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    listing = get_store_listing_by_id(listing_id)
    if listing is None:
        messages.error(request, "Товар для отзыва не найден.")
        return redirect(next_url)

    rating = int(request.POST.get("rating") or 0)
    comment = str(request.POST.get("comment") or "")
    improvement = str(request.POST.get("improvement_suggestion") or "")

    try:
        submit_store_review(
            user=request.user,
            listing=listing,
            rating=rating,
            comment=comment,
            improvement_suggestion=improvement,
        )
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Отзыв сохранён.")

    return redirect(next_url)

@login_required
def author_products_page(request):
    if not is_store_runtime_available():
        return _render_store_unavailable(request, reason="Store runtime is unavailable.")

    if not is_user_store_author(request.user):
        return HttpResponseForbidden("Author panel доступен только store-author или admin+.")

    role_access = get_user_role(request.user)
    if role_access.is_admin_plus:
        listings = ThemeStoreListing.objects.all().order_by("-updated_at", "-id")
    else:
        listings = ThemeStoreListing.objects.filter(submitted_by=request.user).order_by("-updated_at", "-id")

    return render_dashboard_page(
        request,
        full_template="store/author_products.html",
        partial_template="store/partials/author_products_content.html",
        context={
            "author_listings": listings,
            "can_moderate": role_access.is_admin_plus,
            "category_choices": ThemeStoreListing.Category.choices,
        },
        page_title=build_page_title("Store Author"),
        active_menu_key="store",
    )


@login_required
@require_POST
def author_product_create_page(request):
    next_url = _resolve_safe_next_url(request)
    if not is_store_runtime_available():
        messages.error(request, "Store runtime недоступен.")
        return redirect(next_url)

    if not is_user_store_author(request.user):
        return HttpResponseForbidden("Недостаточно прав для создания store products.")

    category = str(request.POST.get("category") or ThemeStoreListing.Category.THEMES).strip().lower()
    key = str(request.POST.get("key") or "").strip().lower()

    listing = ThemeStoreListing(
        key=key,
        category=category,
        theme_key=str(request.POST.get("theme_key") or "").strip().lower(),
        module_key=str(request.POST.get("module_key") or "").strip().lower(),
        bundle_key=str(request.POST.get("bundle_key") or "").strip().lower(),
        title=str(request.POST.get("title") or "").strip(),
        short_description=str(request.POST.get("short_description") or "").strip(),
        full_description=str(request.POST.get("full_description") or "").strip(),
        cover_image=str(request.POST.get("cover_image") or "").strip(),
        author=str(request.POST.get("author") or request.user.username).strip(),
        price_rub=_parse_decimal(request.POST.get("price_rub")),
        is_public=_parse_bool(request.POST.get("is_public")),
        is_included_in_store_subscription=_parse_bool(request.POST.get("is_included_in_store_subscription")),
        status=ThemeStoreListing.Status.DRAFT,
        submitted_by=request.user,
    )

    try:
        listing.save()
    except Exception as exc:
        messages.error(request, f"Не удалось создать товар: {exc}")
    else:
        messages.success(request, "Черновик товара создан.")

    return redirect(next_url)


@login_required
@require_POST
def author_product_update_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    listing = get_store_listing_by_id(listing_id)
    if listing is None:
        messages.error(request, "Товар не найден.")
        return redirect(next_url)

    if not can_manage_store_listing(user=request.user, listing=listing):
        return HttpResponseForbidden("Недостаточно прав для редактирования товара.")

    listing.title = str(request.POST.get("title") or listing.title).strip()
    listing.short_description = str(request.POST.get("short_description") or listing.short_description).strip()
    listing.full_description = str(request.POST.get("full_description") or listing.full_description).strip()
    listing.cover_image = str(request.POST.get("cover_image") or listing.cover_image).strip()
    listing.author = str(request.POST.get("author") or listing.author).strip()
    listing.price_rub = _parse_decimal(request.POST.get("price_rub"))
    listing.is_public = _parse_bool(request.POST.get("is_public"))
    listing.is_included_in_store_subscription = _parse_bool(request.POST.get("is_included_in_store_subscription"))

    try:
        listing.save()
    except Exception as exc:
        messages.error(request, f"Не удалось обновить товар: {exc}")
    else:
        messages.success(request, "Карточка товара обновлена.")

    return redirect(next_url)


@login_required
@require_POST
def author_submit_moderation_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    listing = get_store_listing_by_id(listing_id)
    if listing is None:
        messages.error(request, "Товар не найден.")
        return redirect(next_url)

    try:
        submit_listing_for_moderation(user=request.user, listing=listing)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Товар отправлен на модерацию.")
    return redirect(next_url)


@login_required
def moderation_queue_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Модерация доступна только admin+.")

    queue = ThemeStoreListing.objects.filter(status=ThemeStoreListing.Status.PENDING_MODERATION).order_by("updated_at", "id")
    return render_dashboard_page(
        request,
        full_template="store/moderation_queue.html",
        partial_template="store/partials/moderation_queue_content.html",
        context={
            "moderation_queue": queue,
        },
        page_title=build_page_title("Store Moderation"),
        active_menu_key="store",
    )


@login_required
@require_POST
def moderation_action_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    listing = get_store_listing_by_id(listing_id)
    if listing is None:
        messages.error(request, "Товар не найден.")
        return redirect(next_url)

    action = str(request.POST.get("action") or "").strip().lower()
    comment = str(request.POST.get("comment") or "").strip()
    try:
        moderate_listing(user=request.user, listing=listing, action=action, comment=comment)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        if action == "approve":
            messages.success(request, "Товар опубликован.")
        else:
            messages.success(request, "Товар отклонён.")
    return redirect(next_url)


@login_required
def analytics_page(request):
    if not is_store_runtime_available():
        return _render_store_unavailable(request, reason="Store runtime is unavailable.")

    if not (get_user_role(request.user).is_admin_plus or is_user_store_author(request.user)):
        return HttpResponseForbidden("Store analytics доступна только author/admin.")

    rows = get_store_analytics_rows(request.user)
    return render_dashboard_page(
        request,
        full_template="store/analytics.html",
        partial_template="store/partials/analytics_content.html",
        context={
            "analytics_rows": rows,
        },
        page_title=build_page_title("Store Analytics"),
        active_menu_key="store",
    )
