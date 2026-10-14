from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from common.search import render_search_not_found

from modules.v2.profiles.rbac import get_user_role
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .auth import get_farm_access
from .decorators import farm_required, farm_staff_required
from .connect import import_accounts, refresh_account_profiles
from .forms import (
    ALLOWED_DROP_ITEM_TYPES,
    MAX_DROP_ENTRY_ITEMS,
    RU_STATUS_CHOICES,
    ActiveDropPoolEntryForm,
    FarmAccountForm,
    FarmAccountImportForm,
    FarmDropSaleForm,
    FarmDropSessionForm,
    FarmModuleConfigForm,
    WeeklyDropForm,
    build_drop_item_queryset,
    parse_drop_entry_items,
)
from .hub import (
    build_account_board,
    build_recent_activity,
    build_week_progress,
    current_week,
    drop_week_account_ids,
    frequent_drop_items,
    summarize_board,
)
from .models import (
    ZERO_MONEY,
    ActiveDropPoolEntry,
    FarmAccount,
    FarmDropSale,
    FarmDropSession,
    FarmerRewardTier,
    WeeklyDropLog,
)
from .services import (
    build_locked_item_rows,
    build_locked_session_rows,
    calculate_session_analytics,
    create_drop_session,
    dispatch_caseup_advantage_notifications,
    dispatch_price_threshold_notifications,
    get_dashboard_analytics_for_user,
    get_drop_week_by_index,
    get_farm_module_config,
    get_farmer_feature_state,
    record_drop_sale,
    resolve_sale_fee_percent,
    sync_account_inventory,
)


def _get_account_queryset_for_user(user, access):
    qs = FarmAccount.objects.select_related("owner")
    if not access.staff_access:
        qs = qs.filter(owner=user)
    return qs



def _get_session_queryset_for_user(user, access):
    qs = FarmDropSession.objects.select_related("account", "owner", "created_by").prefetch_related("items__steam_item", "items__sales")
    if not access.staff_access:
        qs = qs.filter(owner=user)
    return qs



def _get_sale_queryset_for_user(user, access):
    qs = FarmDropSale.objects.select_related("item", "session", "owner")
    if not access.staff_access:
        qs = qs.filter(owner=user)
    return qs



def _get_account_for_request(request, access, account_id: int) -> FarmAccount:
    return get_object_or_404(
        _get_account_queryset_for_user(request.user, access),
        pk=account_id,
    )



def _get_session_for_request(request, access, session_id: int) -> FarmDropSession:
    return get_object_or_404(_get_session_queryset_for_user(request.user, access), pk=session_id)



def _get_analytics_plan_name(feature_state) -> str:
    required_plan = getattr(feature_state.deep_access_state, "required_plan", None)
    return getattr(required_plan, "name", "Farmer Start") or "Farmer Start"


BOARD_FILTERS = (
    ("all", "Все"),
    ("pending", "Без дропа"),
    ("done", "С дропом"),
    ("attention", "Проблемы"),
    ("storage", "Склады"),
)


def _filter_board_rows(rows, board_filter: str):
    if board_filter == "pending":
        return [row for row in rows if row.is_farming and not row.has_week_drop]
    if board_filter == "done":
        return [row for row in rows if row.has_week_drop]
    if board_filter == "attention":
        return [row for row in rows if row.connection == "fail"]
    if board_filter == "storage":
        return [row for row in rows if row.account.role == FarmAccount.Role.STORAGE]
    return rows


@farm_required
def hub(request):
    """Overview of the whole "Ферма + Инвестиции" group."""
    from modules.v2.system.services import is_module_enabled

    access = get_farm_access(request.user)
    feature_state = get_farmer_feature_state(request.user)
    analytics_locked = not feature_state.deep_analytics

    week = current_week()
    rows = build_account_board(_get_account_queryset_for_user(request.user, access).filter(owner=request.user), week=week)
    summary = summarize_board(rows)
    week_progress = build_week_progress(rows, week=week)
    farm_analytics = None if analytics_locked else get_dashboard_analytics_for_user(request.user)

    investments_enabled = is_module_enabled("steam_trading")
    invest = None
    if investments_enabled:
        from modules.v2.steam_trading.investment_services import (
            build_investment_analytics,
            get_investment_purchase_queryset,
        )

        invest = build_investment_analytics(list(get_investment_purchase_queryset(user=request.user)[:300]))

    farm_value = summary["inventory_value_rub"]
    invest_value = invest.kpi.current_active_value_sum if invest else ZERO_MONEY
    portfolio_total = farm_value + invest_value
    farm_share = round(farm_value * 100 / portfolio_total) if portfolio_total else 0

    pnl_total = None
    if invest is not None or farm_analytics is not None:
        pnl_total = ZERO_MONEY
        if farm_analytics is not None:
            pnl_total += farm_analytics.total_pnl_rub
        if invest is not None:
            pnl_total += invest.kpi.realized_pnl_sum + invest.kpi.unrealized_pnl_sum

    upcoming_unlocks = []
    if invest is not None:
        upcoming_unlocks = (
            list(invest.upcoming_unlocks.today) + list(invest.upcoming_unlocks.tomorrow) + list(invest.upcoming_unlocks.next_7_days)
        )[:5]

    context = {
        "farm_access": access,
        "farmer_features": feature_state,
        "analytics_locked": analytics_locked,
        "analytics_plan_name": _get_analytics_plan_name(feature_state),
        "week": week,
        "week_progress": week_progress,
        "board_rows": rows,
        "board_summary": summary,
        "farm_drops_total": FarmDropSession.objects.filter(owner=request.user).count(),
        "farm_analytics": farm_analytics,
        "investments_enabled": investments_enabled,
        "invest": invest,
        "portfolio_total": portfolio_total,
        "farm_value": farm_value,
        "invest_value": invest_value,
        "farm_share": farm_share,
        "invest_share": 100 - farm_share if portfolio_total else 0,
        "pnl_total": pnl_total,
        "upcoming_unlocks": upcoming_unlocks,
        "activity": build_recent_activity(user=request.user, include_investments=investments_enabled),
    }
    return render_dashboard_page(
        request,
        full_template="farm/hub.html",
        partial_template="farm/partials/pages/hub_content.html",
        context=context,
        page_title=build_page_title("Ферма + Инвестиции"),
        active_menu_key="farm_hub",
    )


@farm_required
def dashboard(request):
    access = get_farm_access(request.user)
    feature_state = get_farmer_feature_state(request.user)
    config = get_farm_module_config()

    week = current_week()
    accounts_qs = _get_account_queryset_for_user(request.user, access).filter(owner=request.user)
    rows = build_account_board(accounts_qs, week=week)
    board_filter = (request.GET.get("show") or "all").strip()
    if board_filter not in dict(BOARD_FILTERS):
        board_filter = "all"

    sessions_qs = _get_session_queryset_for_user(request.user, access).filter(owner=request.user).order_by("-collected_at", "-id")
    sales_qs = _get_sale_queryset_for_user(request.user, access).filter(owner=request.user)
    analytics_locked = not feature_state.deep_analytics
    recent_sessions = list(sessions_qs[:6])
    dashboard_analytics = None if analytics_locked else get_dashboard_analytics_for_user(request.user)
    recent_session_rows = (
        build_locked_session_rows(recent_sessions)
        if analytics_locked
        else [calculate_session_analytics(session) for session in recent_sessions]
    )

    context = {
        "farm_access": access,
        "farmer_features": feature_state,
        "analytics_locked": analytics_locked,
        "analytics_plan_name": _get_analytics_plan_name(feature_state),
        "week": week,
        "week_progress": build_week_progress(rows, week=week),
        "board_rows": _filter_board_rows(rows, board_filter),
        "board_summary": summarize_board(rows),
        "board_filter": board_filter,
        "board_filters": BOARD_FILTERS,
        "drop_sessions_total": sessions_qs.count(),
        "sales_total": sales_qs.count(),
        "caseup_sales_total": sales_qs.filter(platform=FarmDropSale.Platform.CASEUP).count(),
        "dashboard_analytics": dashboard_analytics,
        "recent_session_rows": recent_session_rows,
        "current_caseup_fee_percent": resolve_sale_fee_percent(user=request.user, platform=FarmDropSale.Platform.CASEUP),
        "default_caseup_fee_percent": config.default_caseup_fee_percent,
        "subscribed_caseup_fee_percent": config.subscribed_caseup_fee_percent,
    }
    return render_dashboard_page(
        request,
        full_template="farm/dashboard.html",
        partial_template="farm/partials/pages/dashboard_content.html",
        context=context,
        page_title=build_page_title("Ферма"),
        active_menu_key="farm_dashboard",
    )


@farm_required
def accounts_list(request):
    access = get_farm_access(request.user)

    qs = _get_account_queryset_for_user(request.user, access)

    status = (request.GET.get("status") or "").strip()
    if status:
        qs = qs.filter(status=status)
    role = (request.GET.get("role") or "").strip()
    if role:
        qs = qs.filter(role=role)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(persona_name__icontains=q) | Q(steam_id64__icontains=q))

    rows = build_account_board(qs)

    return render_dashboard_page(
        request,
        full_template="farm/accounts_list.html",
        partial_template="farm/partials/pages/accounts_list_content.html",
        context={
            "farm_access": access,
            "board_rows": rows,
            "board_summary": summarize_board(rows),
            "selected_status": status,
            "selected_role": role,
            "q": q,
            "statuses": RU_STATUS_CHOICES,
            "roles": FarmAccount.Role.choices,
        },
        page_title=build_page_title("Аккаунты фермы"),
        active_menu_key="farm_accounts",
    )


@farm_required
def accounts_add(request):
    access = get_farm_access(request.user)
    mode = (request.POST.get("mode") or request.GET.get("mode") or "single").strip()
    if mode not in {"single", "bulk"}:
        mode = "single"

    form = FarmAccountForm(owner=request.user)
    import_form = FarmAccountImportForm()
    import_result = None

    if request.method == "POST" and mode == "bulk":
        import_form = FarmAccountImportForm(request.POST)
        if import_form.is_valid():
            import_result = import_accounts(
                owner=request.user,
                text=import_form.cleaned_data["accounts_text"],
                role=import_form.cleaned_data["role"],
                status=import_form.cleaned_data["status"],
            )
            if import_result.created and not import_result.skipped:
                messages.success(request, f"Подключено аккаунтов: {import_result.created_count}.")
                return redirect("farm:accounts_list")
            if import_result.created:
                messages.success(request, f"Подключено аккаунтов: {import_result.created_count}. Часть строк пропущена, см. ниже.")
    elif request.method == "POST":
        form = FarmAccountForm(request.POST, owner=request.user)
        if form.is_valid():
            account = form.save(commit=False)
            account.owner = request.user
            if not account.title:
                from .connect import next_account_title

                account.title = next_account_title(request.user)
            if account.sort_order == 0:
                account.sort_order = FarmAccount.objects.filter(owner=request.user).count() + 1
            account.save()
            try:
                refresh_account_profiles([account])
            except Exception:
                pass
            messages.success(request, f"Аккаунт «{account.title}» подключён. Синхронизируй инвентарь, чтобы проверить доступ.")
            return redirect("farm:accounts_detail", account_id=account.id)

    return render_dashboard_page(
        request,
        full_template="farm/accounts_add.html",
        partial_template="farm/partials/pages/accounts_connect_content.html",
        context={
            "farm_access": access,
            "form": form,
            "import_form": import_form,
            "import_result": import_result,
            "mode": mode,
            "accounts_count": FarmAccount.objects.filter(owner=request.user).count(),
        },
        page_title=build_page_title("Подключить аккаунты"),
        active_menu_key="farm_accounts",
    )


@farm_required
def accounts_edit(request, account_id: int):
    access = get_farm_access(request.user)
    account = _get_account_for_request(request, access, account_id)

    if request.method == "POST":
        form = FarmAccountForm(request.POST, instance=account, owner=account.owner)
        if form.is_valid():
            account = form.save(commit=False)
            if not account.title:
                account.title = account.persona_name or f"Bot {account.id}"
            account.save()
            messages.success(request, "Аккаунт обновлён.")
            return redirect("farm:accounts_detail", account_id=account.id)
    else:
        form = FarmAccountForm(instance=account, owner=account.owner)

    return render_dashboard_page(
        request,
        full_template="farm/accounts_edit.html",
        partial_template="farm/partials/pages/account_edit_content.html",
        context={
            "farm_access": access,
            "account": account,
            "form": form,
            "can_delete": not account.drop_sessions.exists() and not account.trade_sale_orders.exists(),
        },
        page_title=build_page_title(account.title, "Настройки аккаунта"),
        active_menu_key="farm_accounts",
    )


@farm_required
@require_POST
def accounts_delete(request, account_id: int):
    access = get_farm_access(request.user)
    account = _get_account_for_request(request, access, account_id)
    if account.drop_sessions.exists() or account.trade_sale_orders.exists():
        messages.error(request, "У аккаунта есть дропы или сделки, удалить его нельзя. Поставь статус «Paused».")
        return redirect("farm:accounts_edit", account_id=account.id)
    title = account.title
    account.delete()
    messages.success(request, f"Аккаунт «{title}» удалён.")
    return redirect("farm:accounts_list")


@farm_required
@require_POST
def accounts_refresh_profiles(request):
    access = get_farm_access(request.user)
    account_id = (request.POST.get("account_id") or "").strip()
    qs = _get_account_queryset_for_user(request.user, access)
    if account_id.isdigit():
        accounts = [get_object_or_404(qs, pk=int(account_id))]
    else:
        accounts = list(qs.filter(owner=request.user))
    updated = refresh_account_profiles(accounts)
    if updated:
        messages.success(request, f"Профили Steam обновлены: {updated}.")
    else:
        messages.warning(request, "Steam не ответил или профили закрыты. Попробуй позже.")
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("farm:accounts_list")


@farm_required
def account_detail(request, account_id: int):
    access = get_farm_access(request.user)
    feature_state = get_farmer_feature_state(request.user)
    config = get_farm_module_config()
    account = _get_account_for_request(request, access, account_id)

    tab = (request.GET.get("tab") or "overview").strip()
    if tab not in {"overview", "inventory", "sessions"}:
        tab = "overview"

    inventory_filter = (request.GET.get("inventory_filter") or "all").strip()
    if inventory_filter not in {"all", "sellable", "blocked"}:
        inventory_filter = "all"

    inventory_qs = account.inventory_items.select_related("matched_item").filter(is_missing=False)
    if inventory_filter == "sellable":
        inventory_qs = inventory_qs.filter(is_sellable=True)
    elif inventory_filter == "blocked":
        inventory_qs = inventory_qs.filter(is_sellable=False)

    latest_sync = account.inventory_sync_runs.first()

    inventory_stats_base = account.inventory_items.filter(is_missing=False)
    inventory_total = inventory_stats_base.count()
    inventory_sellable_total = inventory_stats_base.filter(is_sellable=True).count()
    inventory_blocked_total = inventory_stats_base.filter(is_sellable=False).count()
    inventory_unmatched_total = inventory_stats_base.filter(matched_item__isnull=True).count()
    sessions = account.drop_sessions.prefetch_related("items__steam_item", "items__sales").order_by("-collected_at", "-id")
    analytics_locked = not feature_state.deep_analytics
    recent_sessions = list(sessions[:10])
    session_rows = (
        build_locked_session_rows(recent_sessions)
        if analytics_locked
        else [calculate_session_analytics(session) for session in recent_sessions]
    )

    return render_dashboard_page(
        request,
        full_template="farm/account_detail.html",
        partial_template="farm/partials/pages/account_detail_content.html",
        context={
            "farm_access": access,
            "farmer_features": feature_state,
            "analytics_locked": analytics_locked,
            "analytics_plan_name": _get_analytics_plan_name(feature_state),
            "account": account,
            "week": current_week(),
            "board_row": build_account_board(_get_account_queryset_for_user(request.user, access).filter(pk=account.pk))[0],
            "active_tab": tab,
            "latest_sync": latest_sync,
            "inventory_items": inventory_qs[:250],
            "inventory_filter": inventory_filter,
            "inventory_total": inventory_total,
            "inventory_sellable_total": inventory_sellable_total,
            "inventory_blocked_total": inventory_blocked_total,
            "inventory_unmatched_total": inventory_unmatched_total,
            "session_rows": session_rows,
            "session_count": sessions.count(),
            "default_caseup_fee_percent": config.default_caseup_fee_percent,
            "subscribed_caseup_fee_percent": config.subscribed_caseup_fee_percent,
        },
        page_title=build_page_title(account.title, "Farm Account"),
        active_menu_key="farm_accounts",
    )


@farm_required
def account_inventory_sync(request, account_id: int):
    if request.method != "POST":
        return redirect("farm:accounts_detail", account_id=account_id)

    access = get_farm_access(request.user)
    account = _get_account_for_request(request, access, account_id)

    try:
        sync_run = sync_account_inventory(account=account)
    except Exception as exc:
        messages.error(request, f"Синхронизация inventory не удалась: {exc}")
    else:
        messages.success(
            request,
            f"Inventory обновлен: {sync_run.total_assets} asset(s), sellable: {sync_run.total_sellable}.",
        )

    return redirect("farm:accounts_detail", account_id=account.id)


@farm_required
def drops_list(request):
    access = get_farm_access(request.user)

    qs = WeeklyDropLog.objects.select_related(
        "account",
        "account__owner",
        "item_1",
        "item_2",
        "created_by",
    )

    if not access.staff_access:
        qs = qs.filter(account__owner=request.user)

    accounts = _get_account_queryset_for_user(request.user, access).order_by("sort_order", "title")

    account_id = (request.GET.get("account") or "").strip()
    week_index = (request.GET.get("week") or "").strip()
    q = (request.GET.get("q") or "").strip()

    if account_id:
        qs = qs.filter(account_id=account_id)

    if week_index.isdigit():
        qs = qs.filter(week_index=int(week_index))

    if q:
        qs = qs.filter(
            Q(item_1__market_hash_name__icontains=q)
            | Q(item_1__name__icontains=q)
            | Q(item_1__name_ru__icontains=q)
            | Q(item_2__market_hash_name__icontains=q)
            | Q(item_2__name__icontains=q)
            | Q(item_2__name_ru__icontains=q)
            | Q(account__title__icontains=q)
        )

    if q and not qs.exists():
        reset_params = {}
        if account_id:
            reset_params["account"] = account_id
        if week_index:
            reset_params["week"] = week_index
        reset_url = reverse("farm:drops_list")
        if reset_params:
            reset_url = f"{reset_url}?{urlencode(reset_params)}"
        return render_search_not_found(
            request,
            query=q,
            title="Legacy weekly logs не найдены",
            hint="По этому запросу не найдено ни одного weekly drop. Попробуй изменить строку поиска или сбросить поиск.",
            reset_url=reset_url,
        )

    return render_dashboard_page(
        request,
        full_template="farm/drops_list.html",
        partial_template="farm/partials/pages/drops_list_content.html",
        context={
            "farm_access": access,
            "drops": qs[:100],
            "accounts": accounts,
            "selected_account": account_id,
            "selected_week": week_index,
            "q": q,
        },
        page_title=build_page_title("Farm Legacy Drops"),
        active_menu_key="farm_legacy_logs",
    )


@farm_required
def drops_add(request):
    access = get_farm_access(request.user)

    catalog_scope = ((request.POST.get("catalog_scope") if request.method == "POST" else request.GET.get("catalog_scope")) or "active").strip()
    if catalog_scope not in {"active", "all"}:
        catalog_scope = "active"

    item_type_filter = ((request.POST.get("item_type_filter") if request.method == "POST" else request.GET.get("item_type_filter")) or "").strip()
    search = ((request.POST.get("search") if request.method == "POST" else request.GET.get("search")) or "").strip()

    item_queryset = build_drop_item_queryset(
        scope=catalog_scope,
        item_type=item_type_filter,
        search=search,
    )

    if request.method == "GET" and search and not item_queryset.exists():
        reset_params = {"catalog_scope": catalog_scope}
        if item_type_filter:
            reset_params["item_type_filter"] = item_type_filter
        reset_url = reverse("farm:drops_add")
        if reset_params:
            reset_url = f"{reset_url}?{urlencode(reset_params)}"
        return render_search_not_found(
            request,
            query=search,
            title="Предметы для legacy-log не найдены",
            hint="Фильтр предметов не нашёл совпадений по этому запросу. Попробуй изменить строку поиска или сбросить поиск.",
            reset_url=reset_url,
        )

    if request.method == "POST":
        form = WeeklyDropForm(
            request.POST,
            user=request.user,
            access=access,
            item_queryset=item_queryset,
        )
        if form.is_valid():
            drop = form.save(created_by=request.user)
            messages.success(request, f"Legacy weekly drop за неделю #{drop.week_index:02d} сохранён.")
            return redirect("farm:drops_list")
    else:
        form = WeeklyDropForm(
            user=request.user,
            access=access,
            item_queryset=item_queryset,
        )

    selected_week_preview = None
    week_index_preview = request.GET.get("week") or request.POST.get("week_index")
    if week_index_preview and str(week_index_preview).isdigit():
        try:
            selected_week_preview = get_drop_week_by_index(int(week_index_preview))
        except ValueError:
            selected_week_preview = None

    return render(
        request,
        "farm/drops_add.html",
        {
            "farm_access": access,
            "form": form,
            "catalog_scope": catalog_scope,
            "item_type_filter": item_type_filter,
            "search": search,
            "available_items_count": item_queryset.count(),
            "active_pool_count": ActiveDropPoolEntry.objects.filter(is_active=True).count(),
            "selected_week_preview": selected_week_preview,
        },
    )


@farm_required
def sessions_list(request):
    access = get_farm_access(request.user)
    feature_state = get_farmer_feature_state(request.user)
    config = get_farm_module_config()

    qs = _get_session_queryset_for_user(request.user, access).order_by("-collected_at", "-id")
    account_id = (request.GET.get("account") or "").strip()
    q = (request.GET.get("q") or "").strip()
    if account_id:
        qs = qs.filter(account_id=account_id)
    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(notes__icontains=q)
            | Q(items__market_hash_name_snapshot__icontains=q)
            | Q(account__title__icontains=q)
        ).distinct()

    if q and not qs.exists():
        reset_params = {}
        if account_id:
            reset_params["account"] = account_id
        reset_url = reverse("farm:sessions_list")
        if reset_params:
            reset_url = f"{reset_url}?{urlencode(reset_params)}"
        return render_search_not_found(
            request,
            query=q,
            title="Farm drops не найдены",
            hint="Журнал дропов не нашёл совпадений по этому запросу. Попробуй изменить строку поиска или сбросить поиск.",
            reset_url=reset_url,
        )

    analytics_locked = not feature_state.deep_analytics
    sessions = list(qs[:50])
    session_rows = (
        build_locked_session_rows(sessions)
        if analytics_locked
        else [calculate_session_analytics(session) for session in sessions]
    )
    return render_dashboard_page(
        request,
        full_template="farm/sessions_list.html",
        partial_template="farm/partials/pages/sessions_list_content.html",
        context={
            "farm_access": access,
            "farmer_features": feature_state,
            "analytics_locked": analytics_locked,
            "analytics_plan_name": _get_analytics_plan_name(feature_state),
            "sessions": session_rows,
            "accounts": _get_account_queryset_for_user(request.user, access).order_by("sort_order", "title"),
            "selected_account": account_id,
            "q": q,
            "default_caseup_fee_percent": config.default_caseup_fee_percent,
            "subscribed_caseup_fee_percent": config.subscribed_caseup_fee_percent,
        },
        page_title=build_page_title("Farm Sessions"),
        active_menu_key="farm_drops",
    )


DROP_PICKER_LIMIT = 24
ITEM_SEARCH_LIMIT = 20


def _drop_picker_items(user):
    """Active drop pool first, then items this user logs most often."""
    pool = list(
        SteamItem.objects.filter(
            active_drop_entries__is_active=True,
            is_active=True,
            item_type__in=ALLOWED_DROP_ITEM_TYPES,
        )
        .order_by("active_drop_entries__sort_order", "market_hash_name")
        .distinct()[:DROP_PICKER_LIMIT]
    )
    seen = {item.id for item in pool}
    frequent = [item for item in frequent_drop_items(user=user) if item.id not in seen]
    return pool, frequent


def _next_pending_account(*, user, access):
    done_ids = drop_week_account_ids(user)
    candidates = list(
        _get_account_queryset_for_user(user, access)
        .filter(owner=user, status=FarmAccount.Status.ACTIVE, role=FarmAccount.Role.FARM)
        .exclude(pk__in=done_ids)
        .order_by("sort_order", "title", "id")
    )
    return candidates[0] if candidates else None


@farm_required
def item_search(request):
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse({"query": query, "results": []})
    items = build_drop_item_queryset(scope="all", search=query)[:ITEM_SEARCH_LIMIT]
    return JsonResponse(
        {
            "query": query,
            "results": [
                {
                    "id": item.id,
                    "name": item.group_name if item.item_type != SteamItem.ItemType.SKIN else item.display_name,
                    "market_hash_name": item.market_hash_name,
                    "type": item.get_item_type_display(),
                    "price": f"{item.price_rub:.2f}" if item.price_rub is not None else "",
                    "icon": item.icon_cdn(96),
                }
                for item in items
            ],
        }
    )


@farm_required
def sessions_add(request):
    access = get_farm_access(request.user)
    feature_state = get_farmer_feature_state(request.user)
    item_errors: list[str] = []
    selected_ids: set[int] = set()

    if request.method == "POST":
        form = FarmDropSessionForm(request.POST, user=request.user, access=access)
        payloads, item_errors = parse_drop_entry_items(request.POST)
        selected_ids = {payload["steam_item"].id for payload in payloads if payload.get("steam_item")}
        if form.is_valid() and not item_errors:
            try:
                session = create_drop_session(
                    account=form.cleaned_data["account"],
                    collected_at=form.cleaned_data["collected_at"],
                    source_type=form.cleaned_data["source_type"],
                    notes=form.cleaned_data.get("notes") or "",
                    title=form.cleaned_data.get("title") or "",
                    created_by=request.user,
                    items_payload=payloads,
                )
            except Exception as exc:
                messages.error(request, f"Не удалось сохранить дроп: {exc}")
            else:
                messages.success(request, f"Дроп для «{session.account.title}» сохранён.")
                if request.POST.get("after") == "next":
                    next_account = _next_pending_account(user=request.user, access=access)
                    if next_account is not None:
                        params = urlencode({"account": next_account.id, "source": session.source_type})
                        return redirect(f"{reverse('farm:sessions_add')}?{params}")
                    messages.info(request, "Все активные фарм-боты собрали дроп на этой неделе.")
                    return redirect("farm:dashboard")
                return redirect("farm:sessions_detail", session_id=session.id)
    else:
        initial = {}
        preselected_account_id = (request.GET.get("account") or "").strip()
        account = None
        if preselected_account_id.isdigit():
            account = _get_account_queryset_for_user(request.user, access).filter(pk=int(preselected_account_id)).first()
        if account is None:
            account = _next_pending_account(user=request.user, access=access)
        if account is not None:
            initial["account"] = account
        source = (request.GET.get("source") or "").strip()
        if source in FarmDropSession.SourceType.values:
            initial["source_type"] = source
        form = FarmDropSessionForm(user=request.user, access=access, initial=initial)

    pool_items, frequent_items = _drop_picker_items(request.user)
    picker_ids = {item.id for item in pool_items} | {item.id for item in frequent_items}
    extra_items = list(SteamItem.objects.filter(pk__in=selected_ids - picker_ids)) if selected_ids else []
    posted = request.POST if request.method == "POST" else None

    def tiles(items):
        return [
            {
                "item": item,
                "selected": item.id in selected_ids,
                "qty": (posted.get(f"qty_{item.id}") if posted else "") or "1",
                "price": (posted.get(f"price_{item.id}") if posted else "")
                or (f"{item.price_rub:.2f}" if item.price_rub is not None else ""),
            }
            for item in items
        ]

    manual_rows = []
    if posted:
        for name, qty, price in zip(posted.getlist("manual_name"), posted.getlist("manual_qty"), posted.getlist("manual_price")):
            if name.strip():
                manual_rows.append({"name": name, "qty": qty, "price": price})

    week_done_ids = drop_week_account_ids(request.user)
    # Bots still waiting for this week's drop first, storages and paused last.
    accounts = sorted(
        form.fields["account"].queryset,
        key=lambda account: (
            account.role != FarmAccount.Role.FARM or account.status != FarmAccount.Status.ACTIVE,
            account.id in week_done_ids,
            account.sort_order,
            account.title,
        ),
    )
    selected_account_value = str(form["account"].value() or "")

    return render_dashboard_page(
        request,
        full_template="farm/sessions_add.html",
        partial_template="farm/partials/pages/drop_add_content.html",
        context={
            "farm_access": access,
            "farmer_features": feature_state,
            "analytics_plan_name": _get_analytics_plan_name(feature_state),
            "form": form,
            "item_errors": item_errors,
            "accounts": accounts,
            "selected_account_value": selected_account_value,
            "week": current_week(),
            "week_done_ids": week_done_ids,
            "week_pending_count": sum(
                1
                for account in accounts
                if account.id not in week_done_ids
                and account.status == FarmAccount.Status.ACTIVE
                and account.role == FarmAccount.Role.FARM
            ),
            "pool_tiles": tiles(pool_items),
            "frequent_tiles": tiles(frequent_items),
            "extra_tiles": tiles(extra_items),
            "manual_rows": manual_rows,
            "max_items": MAX_DROP_ENTRY_ITEMS,
            "source_choices": FarmDropSession.SourceType.choices,
            "selected_source": str(form["source_type"].value() or FarmDropSession.SourceType.WEEKLY),
        },
        page_title=build_page_title("Добавить дроп"),
        active_menu_key="farm_drops",
    )


@farm_required
def sessions_detail(request, session_id: int):
    access = get_farm_access(request.user)
    feature_state = get_farmer_feature_state(request.user)
    config = get_farm_module_config()
    session = _get_session_for_request(request, access, session_id)
    analytics_locked = not feature_state.deep_analytics
    analytics = None if analytics_locked else calculate_session_analytics(session)
    item_rows = build_locked_item_rows(session) if analytics_locked else analytics.item_analytics
    remaining_quantity = sum(row.remaining_quantity for row in item_rows)

    if request.method == "POST":
        form = FarmDropSaleForm(request.POST, session=session, user=session.owner)
        if form.is_valid():
            try:
                record_drop_sale(
                    item=form.cleaned_data["item"],
                    quantity=form.cleaned_data["quantity"],
                    platform=form.cleaned_data["platform"],
                    gross_sale_rub=form.cleaned_data["gross_sale_rub"],
                    sold_at=form.cleaned_data["sold_at"],
                    notes=form.cleaned_data.get("notes") or "",
                )
            except Exception as exc:
                messages.error(request, f"Не удалось записать продажу: {exc}")
            else:
                messages.success(request, "Продажа добавлена в farm-модуль.")
                return redirect("farm:sessions_detail", session_id=session.id)
    else:
        form = FarmDropSaleForm(session=session, user=session.owner)

    return render(
        request,
        "farm/sessions_detail.html",
        {
            "farm_access": access,
            "farmer_features": feature_state,
            "analytics_locked": analytics_locked,
            "analytics_plan_name": _get_analytics_plan_name(feature_state),
            "session": session,
            "analytics": analytics,
            "item_rows": item_rows,
            "item_rows_count": len(item_rows),
            "remaining_quantity": remaining_quantity,
            "sale_form": form,
            "sales": session.sales.select_related("item").order_by("-sold_at", "-id"),
            "default_caseup_fee_percent": config.default_caseup_fee_percent,
            "subscribed_caseup_fee_percent": config.subscribed_caseup_fee_percent,
            "current_caseup_fee_percent": resolve_sale_fee_percent(user=session.owner, platform=FarmDropSale.Platform.CASEUP),
        },
    )


@farm_staff_required
def drop_pool_manage(request):
    access = get_farm_access(request.user)

    if request.method == "POST":
        form = ActiveDropPoolEntryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Предмет добавлен в активный пул.")
            return redirect("farm:drop_pool")
    else:
        form = ActiveDropPoolEntryForm()

    entries = ActiveDropPoolEntry.objects.select_related("item")[:100]

    return render(
        request,
        "farm/drop_pool.html",
        {
            "farm_access": access,
            "form": form,
            "entries": entries,
        },
    )


@farm_staff_required
def drop_pool_remove(request, entry_id: int):
    if request.method != "POST":
        return redirect("farm:drop_pool")

    entry = get_object_or_404(ActiveDropPoolEntry, pk=entry_id)
    entry.delete()
    messages.success(request, "Предмет удалён из активного пула.")
    return redirect("farm:drop_pool")


@farm_staff_required
def settings_page(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Farm settings доступны только admin+.")

    config = get_farm_module_config()
    if request.method == "POST":
        form = FarmModuleConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Farm module config обновлён.")
            return redirect("farm:settings")
    else:
        form = FarmModuleConfigForm(instance=config)

    return render_dashboard_page(
        request,
        full_template="farm/settings.html",
        partial_template="farm/partials/pages/settings_content.html",
        context={
            "form": form,
            "reward_tiers": FarmerRewardTier.objects.order_by("sort_order", "threshold_turnover_rub", "id"),
            "config": config,
        },
        page_title=build_page_title("Farm Settings"),
        active_menu_key="farm_settings",
    )


@farm_staff_required
@require_POST
def settings_dispatch_thresholds(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Farm settings доступны только admin+.")

    events = dispatch_price_threshold_notifications()
    messages.success(request, f"Threshold dispatch: {len(events)} event(s).")
    return redirect("farm:settings")


@farm_staff_required
@require_POST
def settings_dispatch_advantage(request):
    role_access = get_user_role(request.user)
    if not role_access.is_admin_plus:
        return HttpResponseForbidden("Farm settings доступны только admin+.")

    events = dispatch_caseup_advantage_notifications()
    messages.success(request, f"CaseUP advantage dispatch: {len(events)} event(s).")
    return redirect("farm:settings")
