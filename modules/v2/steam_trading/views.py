from __future__ import annotations

import json
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from common.search import render_search_not_found
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe

from modules.v2.farm.auth import get_farm_access
from modules.v2.farm.decorators import farm_required
from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.tg_notifier.services import TelegramNotifierError, send_investment_purchase_notification
from modules.v2.subscriptions.feature_codes import STEAM_TRADING_ADVANCED_ANALYTICS, TG_NOTIFIER_USER
from modules.v2.subscriptions.responses import render_feature_locked_response
from modules.v2.subscriptions.services import get_feature_access_state

from .investment_services import (
    SORT_OPTIONS,
    STATUS_FILTER_OPTIONS,
    build_investment_dashboard,
    get_investment_purchase_queryset,
)
from .forms import CASE_ITEM_QUERYSET, SteamPurchaseForm, SteamPurchaseSaleForm
from .models import SteamPurchase
from .services import (
    SteamTradingSaleError,
    build_purchase_row,
    build_purchase_rows,
    build_purchase_summary,
    create_purchase_sale,
    preview_purchase_sale,
    with_purchase_sales_annotations,
)


def _resolve_safe_next(request, fallback_url_name: str) -> str:
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return redirect(fallback_url_name).url


@farm_required
def purchases_list(request):
    return redirect("steam_trading:investments")


@farm_required
def purchase_add(request):
    access = get_farm_access(request.user)
    initial = {
        "quantity": 1,
        "purchased_at": timezone.localtime().replace(second=0, microsecond=0),
    }

    if request.method == "POST":
        form = SteamPurchaseForm(request.POST, initial=initial)
        if form.is_valid():
            purchase = form.save(created_by=request.user)
            messages.success(request, f"Закупка {purchase.item.group_name} сохранена.")
            return redirect("steam_trading:investments")
    else:
        preselected_item_id = (request.GET.get("item") or "").strip()
        if preselected_item_id.isdigit():
            item = CASE_ITEM_QUERYSET.filter(pk=int(preselected_item_id)).first()
            if item is not None:
                initial["item"] = item
                initial["purchase_price_rub"] = item.price_rub
        form = SteamPurchaseForm(initial=initial)

    case_items = list(CASE_ITEM_QUERYSET.only("id", "market_hash_name", "name", "name_ru", "price_rub", "icon_url"))
    item_prices = {
        str(item.id): str(item.price_rub if item.price_rub is not None else "0.00")
        for item in case_items
    }

    return render_dashboard_page(
        request,
        full_template="steam_trading/add.html",
        partial_template="steam_trading/partials/add_content.html",
        context={
            "steam_trading_access": access,
            "role_access": get_user_role(request.user),
            "form": form,
            "item_prices_json": mark_safe(json.dumps(item_prices)),
        },
        page_title="Добавить закупку | Steam Trading",
        active_menu_key="steam_investments",
    )


@farm_required
def purchase_sell(request, purchase_id: int):
    access = get_farm_access(request.user)
    purchase_qs = with_purchase_sales_annotations(
        SteamPurchase.objects.select_related("item", "created_by").prefetch_related("sales__created_by")
    )
    if not access.staff_access:
        purchase_qs = purchase_qs.filter(created_by=request.user)

    purchase = get_object_or_404(purchase_qs, pk=purchase_id)
    purchase_row = build_purchase_row(purchase)
    history = list(purchase.sales.select_related("created_by").order_by("-sold_at", "-id"))

    initial = {
        "sold_at": timezone.localtime().replace(second=0, microsecond=0),
        "quantity": 1 if purchase.remaining_quantity > 0 else 0,
        "sale_price_rub": purchase.current_price_rub,
    }

    if request.method == "POST":
        form = SteamPurchaseSaleForm(request.POST, purchase=purchase, initial=initial)
        if form.is_valid():
            try:
                sale = create_purchase_sale(
                    purchase=purchase,
                    sold_at=form.cleaned_data["sold_at"],
                    quantity=form.cleaned_data["quantity"],
                    sale_price_rub=form.cleaned_data["sale_price_rub"],
                    created_by=request.user,
                )
            except SteamTradingSaleError as exc:
                if hasattr(exc, "message_dict"):
                    for field, errors in exc.message_dict.items():
                        target_field = field if field in form.fields else None
                        for error in errors:
                            form.add_error(target_field, error)
                else:
                    for message in exc.messages:
                        form.add_error(None, message)
            else:
                messages.success(
                    request,
                    f"Продажа сохранена: {sale.quantity} шт. {purchase.item.group_name} на сумму {sale.total_sale_rub:.2f} ₽.",
                )
                return redirect("steam_trading:investments")
    else:
        form = SteamPurchaseSaleForm(purchase=purchase, initial=initial)

    preview = preview_purchase_sale(
        purchase=purchase,
        quantity=form["quantity"].value() or 0,
        sale_price_rub=form["sale_price_rub"].value() or 0,
    )

    return render_dashboard_page(
        request,
        full_template="steam_trading/sell.html",
        partial_template="steam_trading/partials/sell_content.html",
        context={
            "steam_trading_access": access,
            "role_access": get_user_role(request.user),
            "purchase": purchase,
            "purchase_row": purchase_row,
            "form": form,
            "preview": preview,
            "sales_history": history,
        },
        page_title="Продажа закупки | Steam Trading",
        active_menu_key="steam_investments",
    )


@farm_required
def investments_dashboard(request):
    access = get_farm_access(request.user)
    advanced_analytics_access = get_feature_access_state(request.user, STEAM_TRADING_ADVANCED_ANALYTICS)
    tg_feature_access = get_feature_access_state(request.user, TG_NOTIFIER_USER)
    purchases = list(
        get_investment_purchase_queryset(
            user=request.user,
            staff_access=access.staff_access,
        )[:300]
    )

    dashboard = build_investment_dashboard(
        purchases,
        query=request.GET.get("q", ""),
        status=request.GET.get("status", ""),
        sort=request.GET.get("sort", ""),
    )

    if dashboard.filter_state.query and dashboard.filtered_active_count == 0:
        reset_params = {}
        if dashboard.filter_state.status and dashboard.filter_state.status != "all":
            reset_params["status"] = dashboard.filter_state.status
        if dashboard.filter_state.sort and dashboard.filter_state.sort != "purchased_desc":
            reset_params["sort"] = dashboard.filter_state.sort
        reset_url = reverse("steam_trading:investments")
        if reset_params:
            reset_url = f"{reset_url}?{urlencode(reset_params)}"
        return render_search_not_found(
            request,
            query=dashboard.filter_state.query,
            title="Активные закупки не найдены",
            hint="В инвестиционном дашборде нет позиций, которые совпадают с этим запросом. Попробуй изменить строку поиска или сбросить поиск.",
            reset_url=reset_url,
        )

    return render_dashboard_page(
        request,
        full_template="steam_trading/investments.html",
        partial_template="steam_trading/partials/investments_content.html",
        context={
            "steam_trading_access": access,
            "role_access": get_user_role(request.user),
            "analytics": dashboard.analytics,
            "filtered_active_rows": dashboard.filtered_active_rows,
            "investment_filter_state": dashboard.filter_state,
            "investment_status_options": STATUS_FILTER_OPTIONS,
            "investment_sort_options": SORT_OPTIONS,
            "filtered_active_count": dashboard.filtered_active_count,
            "total_active_count": dashboard.total_active_count,
            "has_investment_filters": dashboard.has_active_filters,
            "investment_can_view_advanced_analytics": advanced_analytics_access.is_available,
            "investment_analytics_access_state": advanced_analytics_access,
            "investment_can_send_tg": tg_feature_access.is_available,
            "investment_tg_access_state": tg_feature_access,
        },
        page_title="Steam Investments | CaseUP",
        active_menu_key="steam_investments",
    )


@farm_required
def purchase_send_telegram(request, purchase_id: int):
    if request.method != "POST":
        return redirect("steam_trading:investments")

    tg_access = get_feature_access_state(request.user, TG_NOTIFIER_USER)
    if not tg_access.is_available:
        return render_feature_locked_response(
            request,
            feature_code=TG_NOTIFIER_USER,
            title="TG-отправка закрыта тарифом",
            description="Ручные пользовательские TG-функции доступны начиная с Level 3.",
        )

    access = get_farm_access(request.user)
    purchase_qs = get_investment_purchase_queryset(
        user=request.user,
        staff_access=access.staff_access,
    )
    purchase = get_object_or_404(purchase_qs, pk=purchase_id)

    try:
        send_investment_purchase_notification(
            purchase=purchase,
            recipient_user=purchase.created_by,
            allow_default_fallback=False,
        )
    except TelegramNotifierError as exc:
        messages.error(request, f"TG отправка не удалась: {exc}")
    else:
        messages.success(request, f"TG уведомление отправлено пользователю по позиции #{purchase.id}.")

    return redirect(_resolve_safe_next(request, "steam_trading:investments"))