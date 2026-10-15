from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from modules.v2.control_center._compat import client_ip
from modules.v2.steam_catalog.services.constants import EXTERIOR_LABELS
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from . import services
from .models import Listing, MarketSeller

SHOWCASE_PAGE_SIZE = 36
MENU_KEY = "market"


def _querystring(request) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


@login_required
def showcase_page(request):
    qs, filters = services.showcase(request.GET)
    page_obj = Paginator(qs, SHOWCASE_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    return render_dashboard_page(
        request,
        full_template="market/index.html",
        partial_template="market/partials/index_content.html",
        context={
            "page_obj": page_obj,
            "filters": filters,
            "sort_options": services.SORT_LABELS,
            "type_options": services.TYPE_LABELS,
            "exterior_options": EXTERIOR_LABELS,
            "pagination_query": _querystring(request),
            "total_active": Listing.objects.filter(status=Listing.Status.ACTIVE).count(),
        },
        page_title=build_page_title("Рынок"),
        active_menu_key=MENU_KEY,
    )


@login_required
def lot_page(request, listing_id: int):
    listing = get_object_or_404(Listing.objects.select_related("seller", "seller__profile_v2", "steam_item"), pk=listing_id)
    is_owner = listing.seller_id == request.user.pk
    if not listing.is_open and not is_owner and not request.user.is_staff:
        messages.info(request, "Этот лот уже не продаётся.")
        return redirect("market:index")
    return render_dashboard_page(
        request,
        full_template="market/lot.html",
        partial_template="market/partials/lot_content.html",
        context={
            "listing": listing,
            "is_owner": is_owner,
            "history": services.listing_history(listing) if is_owner or request.user.is_staff else None,
            "seller_stats": services.seller_stats(listing.seller),
            "more_from_seller": Listing.objects.filter(seller=listing.seller, status=Listing.Status.ACTIVE).exclude(pk=listing.pk)[:6],
        },
        page_title=build_page_title(listing.market_hash_name, "Рынок"),
        active_menu_key=MENU_KEY,
    )


@login_required
def sell_page(request):
    requirements = services.seller_requirements(request.user)
    seller = MarketSeller.objects.filter(user=request.user).first()
    inventory, inventory_error = [], ""
    ready = all(item.ok for item in requirements)
    if ready:
        try:
            inventory = services.inventory_for_sale(request.user, force=request.GET.get("refresh") == "1")
        except services.MarketError as exc:
            inventory_error = str(exc)
    return render_dashboard_page(
        request,
        full_template="market/sell.html",
        partial_template="market/partials/sell_content.html",
        context={
            "requirements": requirements,
            "ready": ready,
            "seller": seller,
            "inventory": inventory,
            "inventory_error": inventory_error,
            "my_listings": Listing.objects.filter(seller=request.user).select_related("steam_item").order_by("-created_at")[:100],
        },
        page_title=build_page_title("Продать", "Рынок"),
        active_menu_key=MENU_KEY,
    )


def _run(request, action, success: str):
    try:
        result = action()
    except services.MarketError as exc:
        messages.error(request, str(exc))
        return None
    messages.success(request, success)
    return result


@login_required
@require_POST
def trade_link_save(request):
    _run(request, lambda: services.save_trade_url(request.user, request.POST.get("trade_url", ""), ip=client_ip(request)), "Трейд-ссылка сохранена.")
    return redirect("market:sell")


@login_required
@require_POST
def list_item(request):
    listing = _run(
        request,
        lambda: services.create_listing(request.user, asset_id=request.POST.get("asset_id", ""), price=request.POST.get("price", ""), ip=client_ip(request)),
        "Лот выставлен на рынок.",
    )
    return redirect("market:lot", listing_id=listing.pk) if listing else redirect("market:sell")


@login_required
@require_POST
def lot_price(request, listing_id: int):
    _run(request, lambda: services.change_price(request.user, listing_id=listing_id, price=request.POST.get("price", ""), ip=client_ip(request)), "Цена обновлена.")
    return redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/market/") else "market:sell")


@login_required
@require_POST
def lot_cancel(request, listing_id: int):
    _run(request, lambda: services.cancel_listing(request.user, listing_id=listing_id, ip=client_ip(request)), "Лот снят с витрины.")
    return redirect("market:sell")
