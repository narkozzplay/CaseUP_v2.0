from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .services import ThemePurchaseError, get_theme_store_cards_for_user, purchase_theme_with_balance


@login_required
def storefront_page(request):
    cards = get_theme_store_cards_for_user(request.user)
    context = {
        "theme_cards": cards,
        "available_cards_count": sum(1 for card in cards if card["runtime_available"]),
        "unavailable_cards_count": sum(1 for card in cards if not card["runtime_available"]),
    }
    return render_dashboard_page(
        request,
        full_template="store/storefront.html",
        partial_template="store/partials/storefront_content.html",
        context=context,
        page_title=build_page_title("Store"),
        active_menu_key="store",
    )


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


@login_required
@require_POST
def purchase_theme_page(request, listing_id: int):
    next_url = _resolve_safe_next_url(request)
    try:
        purchase = purchase_theme_with_balance(user=request.user, listing_id=listing_id)
    except ThemePurchaseError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Тема '{purchase.listing.title}' куплена и добавлена в ваш локальный выбор оформления.",
        )
    return redirect(next_url)
