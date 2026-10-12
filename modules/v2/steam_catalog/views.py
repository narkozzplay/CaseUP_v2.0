from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from common.search import render_search_not_found

from modules.v2.profiles.rbac import get_user_role
from modules.v2.celery_tasks.models import PriceAutoUpdateRule
from modules.v2.store.services import (
    DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE,
    STEAM_CATALOG_DETAIL_MODE_SLOT,
    STEAM_CATALOG_DETAIL_CONTENT_SLOT,
    resolve_store_module_slot_template,
)
from modules.v2.subscriptions.feature_codes import (
    STEAM_CATALOG_DEEP_ANALYTICS,
    STEAM_CATALOG_VIEW,
)
from modules.v2.subscriptions.responses import render_feature_locked_response
from modules.v2.subscriptions.services import get_feature_access_state, user_has_feature
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .forms import AddSteamItemForm, SteamItemNameRuForm, StockMovementCreateForm, UserPriceAlertRuleForm
from .models import SteamItem, UserPriceAlertEvent, UserPriceAlertRule
from .services import refresh_item_prices, upsert_from_market_input
from .services.helpers import canonical_listing_url


def _catalog_staff_access(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return get_user_role(user).is_admin_plus


def _catalog_view_access(user) -> bool:
    return user_has_feature(user, STEAM_CATALOG_VIEW)


def _catalog_analytics_access(user) -> bool:
    return user_has_feature(user, STEAM_CATALOG_DEEP_ANALYTICS)


PRICE_HISTORY_RANGE_CHOICES = (
    ("day", "День"),
    ("week", "Неделя"),
    ("month", "Месяц"),
    ("year", "Год"),
    ("all", "Всё время"),
)


def _normalize_price_history_range(value: str) -> str:
    allowed = {key for key, _ in PRICE_HISTORY_RANGE_CHOICES}
    return value if value in allowed else "week"


def _get_price_history_range_start(range_key: str):
    now = timezone.now()

    if range_key == "day":
        return now - timedelta(days=1)
    if range_key == "week":
        return now - timedelta(days=7)
    if range_key == "month":
        return now - timedelta(days=31)
    if range_key == "year":
        return now - timedelta(days=366)
    return None


def _get_bucket_start(local_dt, range_key: str):
    if range_key == "day":
        return local_dt.replace(minute=0, second=0, microsecond=0)

    if range_key == "week":
        return local_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    if range_key == "month":
        day_start = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start - timedelta(days=day_start.weekday())

    if range_key == "year":
        return local_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    return local_dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _get_bucket_label(bucket_start, range_key: str) -> str:
    if range_key == "day":
        return bucket_start.strftime("%H:00")

    if range_key == "week":
        return bucket_start.strftime("%d.%m")

    if range_key == "month":
        bucket_end = bucket_start + timedelta(days=6)
        return f"{bucket_start.strftime('%d.%m')}—{bucket_end.strftime('%d.%m')}"

    if range_key == "year":
        return bucket_start.strftime("%m.%Y")

    return bucket_start.strftime("%Y")


_RAW_RANGES = frozenset({"day", "week", "month", "year", "all"})

_CHART_LABEL_THRESHOLD = 30
_MAX_CHART_POINTS = 400


def _get_range_note(range_key: str) -> str:
    if range_key == "day":
        return "На графике — все точки за последние 24 часа."
    if range_key == "week":
        return "На графике — все точки за 7 дней (~24 точки/сутки)."
    if range_key == "month":
        return "На графике — все точки за месяц."
    if range_key == "year":
        return f"На графике — все точки за год (при >{ _MAX_CHART_POINTS} выборка с сохранением min/max)."
    return f"На графике — все точки за всё время (при >{_MAX_CHART_POINTS} выборка с сохранением min/max)."


def _group_history_entries(history_entries, range_key: str):
    if not history_entries:
        return []

    tz = timezone.get_current_timezone()
    buckets = {}

    for entry in history_entries:
        local_dt = timezone.localtime(entry.created_at, tz)
        bucket_start = _get_bucket_start(local_dt, range_key)

        existing = buckets.get(bucket_start)
        should_replace = (
            existing is None
            or entry.price_rub > existing["entry"].price_rub
            or (
                entry.price_rub == existing["entry"].price_rub
                and entry.created_at > existing["entry"].created_at
            )
        )

        if should_replace:
            buckets[bucket_start] = {
                "entry": entry,
                "bucket_start": bucket_start,
                "label": _get_bucket_label(bucket_start, range_key),
            }

    return [buckets[key] for key in sorted(buckets.keys())]


def _build_raw_chart_entries(history_entries, range_key: str):
    if not history_entries:
        return []
    tz = timezone.get_current_timezone()
    result = []
    for entry in history_entries:
        local_dt = timezone.localtime(entry.created_at, tz)
        if range_key == "day":
            label = local_dt.strftime("%H:%M")
        elif range_key in ("week", "month"):
            label = local_dt.strftime("%d.%m %H:%M")
        else:
            label = local_dt.strftime("%d.%m.%y")
        result.append({"entry": entry, "label": label})
    return result


def _format_signed_decimal(value: Decimal) -> str:
    if value > 0:
        return f"+{value:.2f}"
    if value < 0:
        return f"{value:.2f}"
    return "0.00"


def _build_price_analytics(stats_entries):
    rows = list(stats_entries or [])

    if not rows:
        return {
            "has_data": False,
            "min_value": None,
            "max_value": None,
            "first_value": None,
            "current_value": None,
            "first_created_at": None,
            "current_created_at": None,
            "peak_created_at": None,
            "peak_created_label": "",
            "trend_delta": Decimal("0"),
            "trend_percent": Decimal("0"),
            "trend_delta_label": "0.00",
            "trend_direction": "flat",
            "trend_icon": "→",
            "trend_label": "Без изменений",
            "drawdown_delta": Decimal("0"),
            "drawdown_percent": Decimal("0"),
            "drawdown_delta_label": "0.00",
            "drawdown_direction": "flat",
            "drawdown_icon": "→",
            "drawdown_label": "От пика изменений нет",
            "period_span_label": "",
        }

    first_entry = rows[0]
    current_entry = rows[-1]

    min_entry = min(
        rows,
        key=lambda entry: (entry.price_rub, entry.created_at),
    )
    peak_entry = max(
        rows,
        key=lambda entry: (entry.price_rub, entry.created_at),
    )

    min_value = min_entry.price_rub
    max_value = peak_entry.price_rub
    first_value = first_entry.price_rub
    current_value = current_entry.price_rub

    trend_delta = current_value - first_value
    if trend_delta > 0:
        trend_direction = "up"
        trend_icon = "↑"
        trend_label = "Рост от старта периода"
    elif trend_delta < 0:
        trend_direction = "down"
        trend_icon = "↓"
        trend_label = "Падение от старта периода"
    else:
        trend_direction = "flat"
        trend_icon = "→"
        trend_label = "Без изменений от старта периода"

    trend_percent = Decimal("0")
    if first_value not in (None, Decimal("0")):
        trend_percent = (trend_delta / first_value) * Decimal("100")

    drawdown_delta = current_value - max_value
    if drawdown_delta > 0:
        drawdown_direction = "up"
        drawdown_icon = "↑"
        drawdown_label = "Текущая выше прошлого пика"
    elif drawdown_delta < 0:
        drawdown_direction = "down"
        drawdown_icon = "↓"
        drawdown_label = "Падение от пика периода"
    else:
        drawdown_direction = "flat"
        drawdown_icon = "→"
        drawdown_label = "Текущая равна пику периода"

    drawdown_percent = Decimal("0")
    if max_value not in (None, Decimal("0")):
        drawdown_percent = (drawdown_delta / max_value) * Decimal("100")

    first_local = timezone.localtime(first_entry.created_at)
    current_local = timezone.localtime(current_entry.created_at)
    peak_local = timezone.localtime(peak_entry.created_at)

    return {
        "has_data": True,
        "min_value": min_value,
        "max_value": max_value,
        "first_value": first_value,
        "current_value": current_value,
        "first_created_at": first_entry.created_at,
        "current_created_at": current_entry.created_at,
        "peak_created_at": peak_entry.created_at,
        "peak_created_label": peak_local.strftime("%d.%m.%Y %H:%M"),
        "trend_delta": trend_delta,
        "trend_percent": trend_percent,
        "trend_delta_label": _format_signed_decimal(trend_delta),
        "trend_direction": trend_direction,
        "trend_icon": trend_icon,
        "trend_label": trend_label,
        "drawdown_delta": drawdown_delta,
        "drawdown_percent": drawdown_percent,
        "drawdown_delta_label": _format_signed_decimal(drawdown_delta),
        "drawdown_direction": drawdown_direction,
        "drawdown_icon": drawdown_icon,
        "drawdown_label": drawdown_label,
        "period_span_label": f"{first_local.strftime('%d.%m.%Y %H:%M')} → {current_local.strftime('%d.%m.%Y %H:%M')}",
    }


def _build_catalog_cards(items_qs, *, auto_update_categories=None):
    auto_update_categories = set(auto_update_categories or [])
    items = list(items_qs.order_by("family_name", "is_stattrak", "wear_order", "-updated_at"))

    grouped = {}
    for item in items:
        key = item.family_name or item.market_hash_name
        bucket = grouped.setdefault(
            key,
            {
                "representative": None,
                "variants": [],
                "min_price": None,
                "max_price": None,
                "total_stock": 0,
            },
        )

        bucket["variants"].append(item)
        bucket["total_stock"] += item.stock

        if bucket["representative"] is None:
            bucket["representative"] = item

        if item.price_rub is not None:
            bucket["min_price"] = item.price_rub if bucket["min_price"] is None else min(bucket["min_price"], item.price_rub)
            bucket["max_price"] = item.price_rub if bucket["max_price"] is None else max(bucket["max_price"], item.price_rub)

    cards = []
    for family_name, bucket in grouped.items():
        rep = bucket["representative"]
        cards.append(
            {
                "family_name": family_name,
                "item": rep,
                "variants_count": len(bucket["variants"]),
                "total_stock": bucket["total_stock"],
                "min_price": bucket["min_price"],
                "max_price": bucket["max_price"],
                "auto_update_enabled": rep.item_type in auto_update_categories,
            }
        )

    cards.sort(key=lambda row: row["item"].updated_at, reverse=True)
    return cards


def _svg_number(value: float) -> str:
    text = f"{value:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _build_price_chart(chart_entries):
    chart_rows = list(chart_entries)

    if not chart_rows:
        return {
            "has_data": False,
            "points": [],
            "first_label": "",
            "last_label": "",
            "dot_radius": 4,
        }

    if len(chart_rows) > _MAX_CHART_POINTS:
        all_values = [row["entry"].price_rub for row in chart_rows]
        min_idx = all_values.index(min(all_values))
        max_idx = all_values.index(max(all_values))
        keep = {0, len(chart_rows) - 1, min_idx, max_idx}
        step = max(1, len(chart_rows) // (_MAX_CHART_POINTS - len(keep)))
        sampled = sorted(keep | set(range(0, len(chart_rows), step)))
        chart_rows = [chart_rows[i] for i in sampled]

    chart_values = [row["entry"].price_rub for row in chart_rows]
    chart_min_value = min(chart_values)
    chart_max_value = max(chart_values)

    chart_width = 640
    chart_height = 240
    pad_x = 28
    pad_y = 22
    usable_width = chart_width - (pad_x * 2)
    usable_height = chart_height - (pad_y * 2)

    spread = chart_max_value - chart_min_value
    if spread == 0:
        spread = Decimal("1")

    n = len(chart_rows)
    dot_radius = 4 if n <= 48 else (3 if n <= 150 else 2)

    if n > _CHART_LABEL_THRESHOLD:
        min_idx = chart_values.index(chart_min_value)
        max_idx = chart_values.index(chart_max_value)
        label_indices = {0, n - 1, min_idx, max_idx}
    else:
        label_indices = set(range(n))

    points = []

    for index, row in enumerate(chart_rows):
        entry = row["entry"]

        if n == 1:
            x = chart_width / 2
        else:
            x = pad_x + (usable_width * index / (n - 1))

        relative = float((entry.price_rub - chart_min_value) / spread)
        y = pad_y + ((1 - relative) * usable_height)

        x = round(x, 2)
        y = round(y, 2)
        label_y = max(pad_y + 12, y - 14)

        points.append(
            {
                "x": x,
                "y": y,
                "x_svg": _svg_number(x),
                "y_svg": _svg_number(y),
                "label_y_svg": _svg_number(label_y),
                "value": entry.price_rub,
                "value_label": f"{entry.price_rub:.2f}",
                "label": row["label"],
                "created_label": timezone.localtime(entry.created_at).strftime("%d.%m.%Y %H:%M"),
                "show_label": index in label_indices,
            }
        )

    return {
        "has_data": True,
        "points": points,
        "first_label": chart_rows[0]["label"],
        "last_label": chart_rows[-1]["label"],
        "dot_radius": dot_radius,
    }


@login_required
def catalog_list(request):
    if not _catalog_view_access(request.user):
        return render_feature_locked_response(
            request,
            feature_code=STEAM_CATALOG_VIEW,
            title="Steam Catalog закрыт тарифом",
            description="Для просмотра каталога нужен активный подписочный уровень с визуальным доступом.",
        )

    q = (request.GET.get("q") or "").strip()
    item_type = (request.GET.get("type") or "").strip()
    review = (request.GET.get("review") or "").strip()

    qs = SteamItem.objects.all()

    if q:
        qs = qs.filter(
            Q(market_hash_name__icontains=q)
            | Q(name__icontains=q)
            | Q(name_ru__icontains=q)
            | Q(weapon__icontains=q)
            | Q(family_name__icontains=q)
        )

    if item_type:
        qs = qs.filter(item_type=item_type)

    if review == "1":
        qs = qs.filter(needs_review=True)
    elif review == "0":
        qs = qs.filter(needs_review=False)

    if q and not qs.exists():
        reset_params = {}
        if item_type:
            reset_params["type"] = item_type
        if review:
            reset_params["review"] = review
        reset_url = reverse("steam_catalog:list")
        if reset_params:
            reset_url = f"{reset_url}?{urlencode(reset_params)}"
        return render_search_not_found(
            request,
            query=q,
            title="Предметы не найдены",
            hint="Steam Catalog не нашёл совпадений по твоему запросу. Попробуй убрать часть строки поиска или сбросить поиск.",
            reset_url=reset_url,
        )

    auto_update_categories = set(
        PriceAutoUpdateRule.objects
        .filter(is_enabled=True)
        .values_list("category", flat=True)
    )

    cards = _build_catalog_cards(qs, auto_update_categories=auto_update_categories)
    paginator = Paginator(cards, 24)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    configured_columns = getattr(settings, "STEAM_CATALOG_GRID_COLUMNS", 7)
    try:
        configured_columns = int(configured_columns)
    except (TypeError, ValueError):
        configured_columns = 7

    configured_columns = max(5, min(configured_columns, 7))

    return render_dashboard_page(
        request,
        full_template="steam_catalog/list.html",
        partial_template="steam_catalog/partials/list_content.html",
        context={
            "page_obj": page_obj,
            "cards": page_obj.object_list,
            "types": SteamItem.ItemType.choices,
            "selected_type": item_type,
            "selected_review": review,
            "q": q,
            "catalog_grid_columns": configured_columns,
            "catalog_can_edit": _catalog_staff_access(request.user),
            "catalog_view_access_state": get_feature_access_state(request.user, STEAM_CATALOG_VIEW),
        },
        page_title=build_page_title("Steam Catalog"),
        active_menu_key="steam_catalog",
    )


@login_required
def catalog_add(request):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")

    if request.method == "POST":
        form = AddSteamItemForm(request.POST)
        if form.is_valid():
            result = upsert_from_market_input(
                value=form.cleaned_data["value"],
                stock=form.cleaned_data["stock"],
            )
            if result.variants_created:
                messages.success(
                    request,
                    f"Добавлено/обновлено {result.variants_created} skin-вариантов.",
                )
            else:
                messages.success(request, "Предмет добавлен в Steam Catalog.")
            return redirect("steam_catalog:detail", item_id=result.anchor_item.id)
    else:
        form = AddSteamItemForm()

    return render(request, "steam_catalog/add.html", {"form": form})


@login_required
def catalog_detail(request, item_id: int):
    if not _catalog_view_access(request.user):
        return render_feature_locked_response(
            request,
            feature_code=STEAM_CATALOG_VIEW,
            title="Карточка Steam Catalog закрыта тарифом",
            description="Доступ к карточкам каталога открывается только при активной подписке с feature Steam Catalog View.",
        )

    item = get_object_or_404(SteamItem, pk=item_id)

    family_items = (
        SteamItem.objects
        .filter(family_name=item.family_name or item.market_hash_name)
        .order_by("is_stattrak", "wear_order", "market_hash_name")
    )

    stock_form = StockMovementCreateForm()
    name_ru_form = SteamItemNameRuForm(instance=item)

    family_prices = [v.price_rub for v in family_items if v.price_rub is not None]
    family_min_price = min(family_prices) if family_prices else None
    family_max_price = max(family_prices) if family_prices else None
    family_total_stock = sum(v.stock for v in family_items)

    selected_price_history_range = _normalize_price_history_range(
        (request.GET.get("range") or "").strip()
    )
    range_start = _get_price_history_range_start(selected_price_history_range)

    history_qs = item.price_history.all()
    if range_start is not None:
        history_qs = history_qs.filter(created_at__gte=range_start)

    filtered_history_asc = list(history_qs.order_by("created_at"))
    price_history = list(reversed(filtered_history_asc[-50:]))

    chart_entries = _build_raw_chart_entries(filtered_history_asc, selected_price_history_range)
    price_chart = _build_price_chart(chart_entries)
    price_analytics = _build_price_analytics(filtered_history_asc)

    auto_update_rule = (
        PriceAutoUpdateRule.objects
        .filter(category=item.item_type, is_enabled=True)
        .first()
    )
    detail_mode_template = resolve_store_module_slot_template(
        user=request.user,
        slot=STEAM_CATALOG_DETAIL_MODE_SLOT,
        request_scope="steam_catalog",
        default_template="",
    )
    detail_content_template = detail_mode_template or resolve_store_module_slot_template(
        user=request.user,
        slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT,
        request_scope="steam_catalog",
        default_template=DEFAULT_STEAM_CATALOG_DETAIL_TEMPLATE,
    )

    return render_dashboard_page(
        request,
        full_template="steam_catalog/detail.html",
        partial_template=detail_content_template,
        context={
            "item": item,
            "family_items": family_items,
            "family_min_price": family_min_price,
            "family_max_price": family_max_price,
            "family_total_stock": family_total_stock,
            "stock_form": stock_form,
            "name_ru_form": name_ru_form,
            "moves": item.stock_moves.select_related("reason", "created_by")[:20],
            "price_history": price_history,
            "price_history_total": len(filtered_history_asc),
            "price_chart": price_chart,
            "price_analytics": price_analytics,
            "latest_price_entry": price_history[0] if price_history else None,
            "price_history_range_choices": PRICE_HISTORY_RANGE_CHOICES,
            "selected_price_history_range": selected_price_history_range,
            "price_history_range_note": _get_range_note(selected_price_history_range),
            "auto_update_rule": auto_update_rule,
            "auto_update_enabled": auto_update_rule is not None,
            "catalog_can_edit": _catalog_staff_access(request.user),
            "catalog_can_view_deep_analytics": _catalog_analytics_access(request.user),
            "catalog_analytics_access_state": get_feature_access_state(request.user, STEAM_CATALOG_DEEP_ANALYTICS),
            "steam_market_url": canonical_listing_url(appid=730, market_hash_name=item.market_hash_name),
            "steam_catalog_detail_content_template": detail_content_template,
            "steam_catalog_detail_mode_template": detail_mode_template,
        },
        page_title=f"{item.group_name} | Steam Catalog",
        active_menu_key="steam_catalog",
    )


@login_required
def update_name_ru(request, item_id: int):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")

    item = get_object_or_404(SteamItem, pk=item_id)
    if request.method != "POST":
        return redirect("steam_catalog:detail", item_id=item.id)

    form = SteamItemNameRuForm(request.POST, instance=item)
    if not form.is_valid():
        messages.error(request, "Не удалось сохранить РУ_нейм.")
        return redirect("steam_catalog:detail", item_id=item.id)

    name_ru = (form.cleaned_data.get("name_ru") or "").strip() or None

    if item.item_type == SteamItem.ItemType.SKIN:
        SteamItem.objects.filter(family_name=item.family_name).update(name_ru=name_ru)
        messages.success(request, "РУ_нейм обновлён для всех вариантов скина.")
    else:
        item.name_ru = name_ru
        item.save(update_fields=["name_ru", "updated_at"])
        messages.success(request, "РУ_нейм обновлён.")

    return redirect("steam_catalog:detail", item_id=item.id)


@login_required
def update_price(request, item_id: int):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")

    item = get_object_or_404(SteamItem, pk=item_id)

    if request.method != "POST":
        return redirect("steam_catalog:detail", item_id=item.id)

    result = refresh_item_prices(
        item,
        include_family=(item.item_type == SteamItem.ItemType.SKIN),
        source="manual",
    )

    if result.touched_count == 0:
        messages.error(request, "Не удалось получить актуальную цену из Steam.")
        return redirect("steam_catalog:detail", item_id=item.id)

    if result.updated_count:
        messages.success(
            request,
            f"Цены обновлены: {result.updated_count} шт. История записана: {result.history_written}.",
        )
    else:
        messages.info(
            request,
            f"Цена уже актуальна. Проверено: {result.touched_count} шт. Новых записей истории: {result.history_written}.",
        )

    return redirect("steam_catalog:detail", item_id=item.id)


@login_required
def add_stock_move(request, item_id: int):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")

    item = get_object_or_404(SteamItem, pk=item_id)

    if request.method != "POST":
        return redirect("steam_catalog:detail", item_id=item.id)

    form = StockMovementCreateForm(request.POST)
    if form.is_valid():
        form.save(item=item, created_by=request.user)
        messages.success(request, "Движение склада сохранено.")
    else:
        messages.error(request, "Не удалось сохранить движение склада.")

    return redirect("steam_catalog:detail", item_id=item.id)


@login_required
def user_alert_rule_list(request):
    rules = UserPriceAlertRule.objects.filter(user=request.user).select_related("item").order_by("-updated_at", "-id")
    return render(request, "steam_catalog/alert_rules_list.html", {
        "role_access": get_user_role(request.user),
        "rules": rules,
    })


@login_required
def user_alert_rule_create(request):
    if request.method == "POST":
        form = UserPriceAlertRuleForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Правило уведомлений сохранено.")
            return redirect("steam_catalog:alert_rules")
    else:
        form = UserPriceAlertRuleForm(user=request.user)
    return render(request, "steam_catalog/alert_rule_form.html", {"form": form, "role_access": get_user_role(request.user), "is_create": True})


@login_required
def user_alert_rule_edit(request, rule_id: int):
    rule = get_object_or_404(UserPriceAlertRule, pk=rule_id, user=request.user)
    if request.method == "POST":
        form = UserPriceAlertRuleForm(request.POST, instance=rule, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Правило уведомлений обновлено.")
            return redirect("steam_catalog:alert_rules")
    else:
        form = UserPriceAlertRuleForm(instance=rule, user=request.user)
    return render(request, "steam_catalog/alert_rule_form.html", {"form": form, "role_access": get_user_role(request.user), "is_create": False, "rule": rule})


@login_required
def user_alert_rule_toggle(request, rule_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Method not allowed.")
    rule = get_object_or_404(UserPriceAlertRule, pk=rule_id, user=request.user)
    rule.status = UserPriceAlertRule.Status.DISABLED if rule.status == UserPriceAlertRule.Status.ACTIVE else UserPriceAlertRule.Status.ACTIVE
    rule.save(update_fields=["status", "updated_at"])
    messages.success(request, "Статус правила обновлен.")
    return redirect("steam_catalog:alert_rules")


@login_required
def user_alert_event_history(request, rule_id: int | None = None):
    events = UserPriceAlertEvent.objects.filter(user=request.user).select_related("item", "rule")
    rule = None
    if rule_id is not None:
        rule = get_object_or_404(UserPriceAlertRule, pk=rule_id, user=request.user)
        events = events.filter(rule=rule)
    events = events.order_by("-created_at", "-id")[:100]
    return render(request, "steam_catalog/alert_event_history.html", {"events": events, "rule": rule, "role_access": get_user_role(request.user)})


@login_required
def user_alert_help(request):
    return render(request, "steam_catalog/alert_help.html", {"role_access": get_user_role(request.user)})
