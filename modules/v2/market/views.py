from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
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
            **_buy_context(request, listing, is_owner),
        },
        page_title=build_page_title(listing.market_hash_name, "Рынок"),
        active_menu_key=MENU_KEY,
    )


def _buy_context(request, listing, is_owner: bool) -> dict:
    from modules.v2.payments.services import get_topup_limits, quote_topup

    from .deals import buyer_problems, shortfall_for
    from .models import Deal

    if is_owner or listing.status != Listing.Status.ACTIVE:
        own_deal = Deal.objects.filter(listing=listing).filter(buyer=request.user).order_by("-created_at").first() or (
            Deal.objects.filter(listing=listing).order_by("-created_at").first() if is_owner else None
        )
        return {"own_deal": own_deal}
    shortfall = shortfall_for(request.user, listing)
    context = {"buy_problems": buyer_problems(request.user), "shortfall": shortfall, "after_topup": request.GET.get("buy") == "1"}
    if shortfall > 0:
        minimum, _maximum = get_topup_limits()
        topup = max(shortfall, minimum).quantize(Decimal("1"), rounding=ROUND_CEILING)
        context.update({"topup_amount": topup, "topup_total": quote_topup(topup)[1]})
    return context


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


# --------------------------------------------------------------------------- deals


@login_required
@require_POST
def lot_buy(request, listing_id: int):
    from urllib.parse import urlencode

    from django.urls import reverse

    from modules.v2.payments.services import get_topup_limits

    from . import deals

    try:
        deal = deals.buy(request.user, listing_id=listing_id, ip=client_ip(request))
    except deals.InsufficientFunds as exc:
        minimum, _maximum = get_topup_limits()
        amount = max(exc.shortfall, minimum).quantize(Decimal("1"), rounding=ROUND_CEILING)
        messages.info(request, f"Не хватает {exc.shortfall:.2f} ₽. Пополните баланс — после оплаты вы вернётесь к лоту.")
        back = reverse("market:lot", args=[listing_id]) + "?buy=1"
        return redirect(reverse("payments:topup") + "?" + urlencode({"amount": f"{amount:.0f}", "next": back}))
    except services.MarketError as exc:
        messages.error(request, str(exc))
        if "трейд-ссылку" in str(exc) or "Steam" in str(exc):
            return redirect("market:sell")
        return redirect("market:lot", listing_id=listing_id)
    messages.success(request, f"Сделка {deal.code} открыта: деньги заморожены, продавец получил уведомление.")
    return redirect("market:deal", code=deal.code)


@login_required
def my_deals(request):
    from .deals import held_amount
    from .models import Deal

    base = Deal.objects.select_related("listing", "buyer", "buyer__profile_v2", "seller", "seller__profile_v2")
    return render_dashboard_page(
        request,
        full_template="market/deals.html",
        partial_template="market/partials/deals_content.html",
        context={
            "purchases": base.filter(buyer=request.user)[:100],
            "sales": base.filter(seller=request.user)[:100],
            "held": held_amount(request.user),
        },
        page_title=build_page_title("Мои сделки", "Рынок"),
        active_menu_key=MENU_KEY,
    )


def _deal_for(request, code: str):
    from modules.v2.profiles.rbac import get_user_role

    from .models import Deal

    deal = get_object_or_404(Deal.objects.select_related("listing", "buyer", "buyer__profile_v2", "seller", "seller__profile_v2", "resolved_by", "disputed_by"), code=code.upper())
    party = deal.party(request.user)
    is_admin = get_user_role(request.user).is_admin_plus
    if not party and not is_admin:
        return None, "", False
    return deal, party, is_admin


@login_required
def deal_page(request, code: str):
    from django.http import Http404

    from modules.v2.social.models import SocialSpace
    from modules.v2.social.services import build_space_url

    from .deals import deal_history

    deal, party, is_admin = _deal_for(request, code)
    if deal is None:
        raise Http404
    space = SocialSpace.objects.filter(pk=deal.chat_space_id).first() if deal.chat_space_id else None
    now = timezone.now()
    return render_dashboard_page(
        request,
        full_template="market/deal.html",
        partial_template="market/partials/deal_content.html",
        context={
            "deal": deal,
            "party": party,
            "is_admin": is_admin,
            "chat_url": build_space_url(space) if space else "",
            "history": deal_history(deal),
            "deadline_passed": now >= deal.send_deadline,
            "steps": _deal_steps(deal),
        },
        page_title=build_page_title(deal.code, "Рынок"),
        active_menu_key=MENU_KEY,
    )


def _deal_steps(deal) -> list[dict]:
    from .models import Deal

    order = [Deal.Status.PAID, Deal.Status.SENT, Deal.Status.HOLD, Deal.Status.COMPLETED]
    current = deal.status_before_dispute if deal.status == Deal.Status.DISPUTED else deal.status
    reached = order.index(current) if current in order else -1
    labels = ("Оплачено", "Трейд отправлен", "Трейд принят · удержание", "Завершено")
    return [{"label": label, "done": index < reached or deal.status == Deal.Status.COMPLETED, "current": index == reached and deal.status != Deal.Status.COMPLETED} for index, label in enumerate(labels)]


@login_required
@require_POST
def deal_action(request, code: str, action: str):
    from django.http import Http404

    from . import deals

    deal, party, is_admin = _deal_for(request, code)
    if deal is None:
        raise Http404
    ip = client_ip(request)
    actions = {
        "sent": (lambda: deals.mark_sent(request.user, deal.code, ip=ip), "Отмечено: трейд отправлен. Покупатель получил уведомление."),
        "received": (lambda: deals.confirm_received(request.user, deal.code, ip=ip), "Спасибо! Деньги удерживаются 7 дней (защита обмена Steam), затем поступят продавцу."),
        "cancel": (lambda: deals.cancel(request.user, deal.code, ip=ip), "Сделка отменена, деньги возвращены покупателю."),
        "dispute": (lambda: deals.open_dispute(request.user, deal.code, reason=request.POST.get("reason", ""), ip=ip), "Администрация вызвана. Таймеры сделки остановлены до решения."),
    }
    if action == "resolve" and is_admin:
        _run(request, lambda: deals.resolve_dispute(request.user, deal.code, decision=request.POST.get("decision", ""), note=request.POST.get("note", ""), request=request), "Спор решён.")
    elif action in actions:
        call, success = actions[action]
        _run(request, call, success)
    else:
        raise Http404
    return redirect("market:deal", code=deal.code)
