from __future__ import annotations

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render

from modules.v2.farm.auth import get_farm_access
from modules.v2.farm.decorators import farm_required
from modules.v2.system.dashboard_shell import render_dashboard_page

from .forms import TradeBotAccountForm
from .models import SaleOrder, SaleOrderItem, TradeBotAccount, TradeUserPenalty
from .services import (
    TradeHubError,
    cancel_sale_order,
    create_sale_order,
    finalize_sale_order,
    get_sales_stats,
    get_tradehub_dashboard_context,
    mark_offer_accepted,
    mark_offer_sent,
)


def _orders_for_request(user, access):
    qs = SaleOrder.objects.select_related("seller", "farm_account", "bot_account")
    if not access.staff_access:
        qs = qs.filter(seller=user)
    return qs


@farm_required
def dashboard(request):
    access = get_farm_access(request.user)
    context = get_tradehub_dashboard_context(user=None if access.staff_access else request.user)
    context["tradehub_access"] = access
    context["recent_pending"] = _orders_for_request(request.user, access).exclude(status=SaleOrder.Status.COMPLETED)[:10]
    return render_dashboard_page(
        request,
        full_template="tradehub/dashboard.html",
        partial_template="tradehub/partials/dashboard_content.html",
        context=context,
        page_title="TradeHub | CaseUP",
        active_menu_key="tradehub_dashboard",
    )


@farm_required
def sales_list(request):
    access = get_farm_access(request.user)
    qs = _orders_for_request(request.user, access)

    status = (request.GET.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status)

    context = {
        "tradehub_access": access,
        "orders": qs.prefetch_related(Prefetch("items", queryset=SaleOrderItem.objects.select_related("steam_item", "inventory_item")))[:100],
        "selected_status": status,
        "statuses": SaleOrder.Status.choices,
        "stats": get_sales_stats(user=None if access.staff_access else request.user),
    }
    return render_dashboard_page(
        request,
        full_template="tradehub/sales_list.html",
        partial_template="tradehub/partials/sales_list_content.html",
        context=context,
        page_title="TradeHub Sales | CaseUP",
        active_menu_key="tradehub_dashboard",
    )


@farm_required
def sale_detail(request, order_id: int):
    access = get_farm_access(request.user)
    order = get_object_or_404(
        _orders_for_request(request.user, access).prefetch_related(
            Prefetch("items", queryset=SaleOrderItem.objects.select_related("steam_item", "inventory_item")),
            "logs",
            "penalties",
        ),
        pk=order_id,
    )
    return render_dashboard_page(
        request,
        full_template="tradehub/sale_detail.html",
        partial_template="tradehub/partials/sale_detail_content.html",
        context={
            "tradehub_access": access,
            "order": order,
            "offer": getattr(order, "offer", None),
        },
        page_title=f"Sale #{order.id} | TradeHub | CaseUP",
        active_menu_key="tradehub_dashboard",
    )


@farm_required
def create_order(request):
    if request.method != "POST":
        return redirect("tradehub:sales_list")

    access = get_farm_access(request.user)
    account_id = request.POST.get("account_id")
    item_ids = request.POST.getlist("inventory_item_ids")

    from modules.v2.farm.models import FarmAccount

    account_qs = FarmAccount.objects.all()
    if not access.staff_access:
        account_qs = account_qs.filter(owner=request.user)
    account = get_object_or_404(account_qs, pk=account_id)

    try:
        order = create_sale_order(
            seller=request.user,
            account=account,
            inventory_item_ids=item_ids,
            created_by=request.user,
        )
    except TradeHubError as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", []) or [str(exc)]))
        return redirect("farm:accounts_detail", account_id=account.id)

    messages.success(request, f"Ордер продажи #{order.id} создан. Бот: {order.bot_account.title}.")
    return redirect("tradehub:sale_detail", order_id=order.id)


@farm_required
def sale_action(request, order_id: int):
    if request.method != "POST":
        return redirect("tradehub:sale_detail", order_id=order_id)

    access = get_farm_access(request.user)
    if not access.staff_access:
        messages.error(request, "Только staff может менять статус сделки.")
        return redirect("tradehub:sale_detail", order_id=order_id)

    order = get_object_or_404(SaleOrder.objects.select_related("seller", "bot_account"), pk=order_id)
    action = (request.POST.get("action") or "").strip()

    try:
        if action == "mark_sent":
            external_offer_id = (request.POST.get("external_offer_id") or "").strip()
            mark_offer_sent(order=order, external_offer_id=external_offer_id, created_by=request.user)
            messages.success(request, "Оффер помечен как отправленный.")
        elif action == "mark_accepted":
            mark_offer_accepted(order=order, created_by=request.user)
            messages.success(request, "Оффер помечен как принятый.")
        elif action == "finalize":
            finalize_sale_order(order=order, created_by=request.user)
            messages.success(request, "Сделка завершена, выплата начислена.")
        elif action == "cancel_user":
            cancel_sale_order(order=order, reason=request.POST.get("reason") or "Отмена пользователем.", cancelled_by_user=True, created_by=request.user)
            messages.warning(request, "Оффер отменён пользователем. Санкция применена.")
        elif action == "cancel_system":
            cancel_sale_order(order=order, reason=request.POST.get("reason") or "Отмена системой.", cancelled_by_user=False, created_by=request.user)
            messages.warning(request, "Оффер отменён системой.")
        else:
            messages.error(request, "Неизвестное действие.")
    except TradeHubError as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", []) or [str(exc)]))

    return redirect("tradehub:sale_detail", order_id=order.id)


@farm_required
def bots_list(request):
    access = get_farm_access(request.user)
    if not access.staff_access:
        messages.error(request, "Раздел ботов доступен только staff.")
        return redirect("tradehub:dashboard")

    return render(
        request,
        "tradehub/bots_list.html",
        {
            "tradehub_access": access,
            "bots": TradeBotAccount.objects.all(),
        },
    )


@farm_required
def bots_add(request):
    access = get_farm_access(request.user)
    if not access.staff_access:
        messages.error(request, "Раздел ботов доступен только staff.")
        return redirect("tradehub:dashboard")

    if request.method == "POST":
        form = TradeBotAccountForm(request.POST)
        if form.is_valid():
            if form.cleaned_data.get("is_default"):
                TradeBotAccount.objects.filter(is_default=True).update(is_default=False)
            bot = form.save()
            messages.success(request, f"Трейд-бот {bot.title} создан.")
            return redirect("tradehub:bots_list")
    else:
        form = TradeBotAccountForm()

    return render(
        request,
        "tradehub/bots_add.html",
        {
            "tradehub_access": access,
            "form": form,
        },
    )
