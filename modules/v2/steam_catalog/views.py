from __future__ import annotations

import hashlib
import logging
import math
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.views.decorators.http import require_POST

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

from modules.v2.ads.services import get_feed_every_n

from .forms import AddSteamItemForm, SteamItemNameRuForm, StockMovementCreateForm, UserPriceAlertRuleForm, alert_limit_error, lock_alert_quota
from .models import CS2ReferenceItem, SteamItem, UserPriceAlertEvent, UserPriceAlertRule
from .services.bulk_import import read_status as read_bulk_import_status, write_status as write_bulk_import_status
from .services import MarketSearchError, refresh_item_prices, search_market_items, upsert_from_market_input
from .services import price_queue
from .services.helpers import canonical_listing_url, rarity_ru_label_from_key
from .services.reference import catalog_ids_for_reference, fill_catalog_from_reference
from . import seo

logger = logging.getLogger(__name__)


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


def _guest_cache_key(request, page: str) -> str | None:
    """Cache key for data shown to guests: same for every guest, so many visits cost one query."""
    if request.user.is_authenticated or getattr(settings, "STEAM_CATALOG_GUEST_CACHE_SECONDS", 0) <= 0:
        return None
    query = urlencode(sorted(request.GET.items()))
    digest = hashlib.md5(f"{page}|{get_language()}|{query}".encode()).hexdigest()
    return f"steam-catalog:guest:{digest}"


def _guest_cache_get(key: str | None):
    if not key:
        return None
    try:
        return cache.get(key)
    except Exception:  # noqa: BLE001 - without the cache guests get fresh data, not an error
        logger.warning("Guest catalog cache read failed", exc_info=True)
        return None


def _guest_cache_set(key: str | None, value) -> None:
    if not key:
        return
    try:
        cache.set(key, value, settings.STEAM_CATALOG_GUEST_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("Guest catalog cache write failed", exc_info=True)


def _base_template(request) -> str:
    """Guests see the catalog inside the public site layout; signed-in users inside their dashboard."""
    return "layouts/base_dashboard.html" if request.user.is_authenticated else "steam_catalog/base_guest.html"


def _locked_for_guest(request, **locked_kwargs):
    """Without access: a guest is sent to sign in (and comes back), a signed-in user sees the plans card."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return render_feature_locked_response(request, feature_code=STEAM_CATALOG_VIEW, **locked_kwargs)


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

_CHART_DOTS_THRESHOLD = 60
_CHART_TICK_COUNT = 4
_CHART_X_TICK_COUNT = 5
_CHART_VIEW_WIDTH = 1000
_CHART_VIEW_HEIGHT = 300
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


def _svg_number(value: float) -> str:
    text = f"{value:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _nice_chart_step(raw_step: float) -> float:
    if raw_step <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step >= raw_step:
            return step
    return 10 * magnitude


def _format_chart_tick(value: float, step: float) -> str:
    if step >= 1 and float(step).is_integer():
        return f"{value:,.0f}".replace(",", " ")
    if step >= 0.1:
        return f"{value:,.1f}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


def _build_chart_scale(values):
    low = float(min(values))
    high = float(max(values))
    spread = high - low
    if spread == 0:
        pad = max(abs(high) * 0.05, 1.0)
    else:
        pad = spread * 0.15
    low = max(low - pad, 0.0) if low >= 0 else low - pad
    high = high + pad

    step = _nice_chart_step((high - low) / _CHART_TICK_COUNT)
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step
    if high == low:
        high = low + step

    ticks = []
    value = low
    while value <= high + step / 1000:
        ticks.append(value)
        value += step
    return low, high, step, ticks


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
    scale_low, scale_high, scale_step, scale_ticks = _build_chart_scale(chart_values)
    scale_span = scale_high - scale_low

    n = len(chart_rows)
    dot_radius = 4 if n <= 48 else (3 if n <= 150 else 2)
    min_idx = chart_values.index(chart_min_value)
    max_idx = len(chart_values) - 1 - chart_values[::-1].index(chart_max_value)

    timestamps = [row["entry"].created_at.timestamp() for row in chart_rows]
    time_span = timestamps[-1] - timestamps[0]

    def y_percent(value) -> float:
        return round((1 - (float(value) - scale_low) / scale_span) * 100, 3)

    points = []
    previous_value = None
    for index, row in enumerate(chart_rows):
        entry = row["entry"]
        if n == 1:
            x = 50.0
        elif time_span > 0:
            x = (timestamps[index] - timestamps[0]) / time_span * 100
        else:
            x = index / (n - 1) * 100
        x = round(x, 3)
        y = y_percent(entry.price_rub)

        change_label = ""
        change_direction = "flat"
        if previous_value is not None:
            delta = entry.price_rub - previous_value
            change_label = _format_signed_decimal(delta)
            change_direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
        previous_value = entry.price_rub

        points.append(
            {
                "x": x,
                "y": y,
                "x_svg": _svg_number(x * _CHART_VIEW_WIDTH / 100),
                "y_svg": _svg_number(y * _CHART_VIEW_HEIGHT / 100),
                "value": entry.price_rub,
                "value_label": f"{entry.price_rub:.2f}",
                "label": row["label"],
                "created_label": timezone.localtime(entry.created_at).strftime("%d.%m.%Y %H:%M"),
                "change_label": change_label,
                "change_direction": change_direction,
            }
        )

    if n == 1:
        only = points[0]
        line_path = f"M0 {only['y_svg']} H{_CHART_VIEW_WIDTH}"
    else:
        line_path = "M" + " L".join(f"{p['x_svg']} {p['y_svg']}" for p in points)
    first_x = "0" if n == 1 else points[0]["x_svg"]
    last_x = str(_CHART_VIEW_WIDTH) if n == 1 else points[-1]["x_svg"]
    area_path = f"{line_path} L{last_x} {_CHART_VIEW_HEIGHT} L{first_x} {_CHART_VIEW_HEIGHT} Z"

    y_ticks = [
        {"y": y_percent(value), "label": _format_chart_tick(value, scale_step)}
        for value in reversed(scale_ticks)
    ]

    if n == 1:
        x_tick_indices = [0]
    else:
        wanted = min(n, _CHART_X_TICK_COUNT)
        x_tick_indices = sorted({round(i * (n - 1) / (wanted - 1)) for i in range(wanted)})
    x_ticks = []
    for index in x_tick_indices:
        point = points[index]
        align = "start" if point["x"] < 8 else ("end" if point["x"] > 92 else "center")
        x_ticks.append({"x": point["x"], "label": point["label"], "align": align})

    last_point = points[-1]
    markers = []
    if n > 1 and chart_max_value != chart_min_value:
        # The last point already carries the price tag, so it gets no extra marker.
        markers = [
            {"kind": kind, "point": points[index], "title": title}
            for kind, index, title in (("max", max_idx, "Макс"), ("min", min_idx, "Мин"))
            if index != n - 1
        ]

    return {
        "has_data": True,
        "is_single": n == 1,
        "points": points,
        "points_json": [
            {
                "x": point["x"],
                "y": point["y"],
                "price": point["value_label"],
                "date": point["created_label"],
                "change": point["change_label"],
                "dir": point["change_direction"],
            }
            for point in points
        ],
        "line_path": line_path,
        "area_path": area_path,
        "view_width": _CHART_VIEW_WIDTH,
        "view_height": _CHART_VIEW_HEIGHT,
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
        "markers": markers,
        "last_point": last_point,
        "show_dots": n <= _CHART_DOTS_THRESHOLD,
        "first_label": chart_rows[0]["label"],
        "last_label": chart_rows[-1]["label"],
        "dot_radius": dot_radius,
    }


def catalog_list(request):
    if not _catalog_view_access(request.user):
        return _locked_for_guest(
            request,
            title="Steam Catalog закрыт тарифом",
            description="Для просмотра каталога нужен активный подписочный уровень с визуальным доступом.",
        )

    from .services import browse

    can_edit = _catalog_staff_access(request.user)
    filters = browse.parse_filters(request.GET, can_edit=can_edit)
    fav_keys = browse.favorite_keys(request.user)
    guest_key = _guest_cache_key(request, "list")
    cached = _guest_cache_get(guest_key)
    if cached is not None:
        # A paginator over a range gives the same page numbers without counting the rows again.
        page_obj = Paginator(range(cached["count"]), 24).get_page(request.GET.get("page") or 1)
    else:
        rows = browse.family_rows(filters, fav_keys=fav_keys)
        page_obj = Paginator(rows, 24).get_page(request.GET.get("page") or 1)

    if filters.q and not page_obj.paginator.count and not filters.favorites:
        params = request.GET.copy()
        for key in ("q", "page"):
            params.pop(key, None)
        reset_url = reverse("steam_catalog:list") + (f"?{params.urlencode()}" if params else "")
        return render_search_not_found(
            request,
            query=filters.q,
            title="Предметы не найдены",
            hint="Steam Catalog не нашёл совпадений. Попробуй другое написание (например, «ak redline») или сбрось фильтры.",
            reset_url=reset_url,
        )

    if cached is not None:
        cards, options = cached["cards"], cached["options"]
    else:
        auto_update_categories = set(PriceAutoUpdateRule.objects.filter(is_enabled=True).values_list("category", flat=True))
        cards = browse.build_cards(page_obj.object_list, filters, fav_keys=fav_keys, auto_update_categories=auto_update_categories)
        options = browse.filter_options(filters)
        _guest_cache_set(guest_key, {"count": page_obj.paginator.count, "cards": cards, "options": options})

    configured_columns = getattr(settings, "STEAM_CATALOG_GRID_COLUMNS", 7)
    try:
        configured_columns = int(configured_columns)
    except (TypeError, ValueError):
        configured_columns = 7
    configured_columns = max(5, min(configured_columns, 7))

    def query_without(*names, **extra):
        params = request.GET.copy()
        for name in (*names, "page"):
            params.pop(name, None)
        for key, value in extra.items():
            params[key] = value
        return params.urlencode()

    for chip in filters.chips:
        chip["url"] = "?" + query_without(*chip["drop"])

    return render_dashboard_page(
        request,
        full_template="steam_catalog/list.html",
        partial_template="steam_catalog/partials/list_content.html",
        context={
            "page_obj": page_obj,
            "cards": cards,
            "filters": filters,
            "options": options,
            "favorites_count": len(fav_keys),
            "type_tab_query": query_without("type", "fav"),
            "fav_tab_query": query_without("fav", fav="1"),
            "pagination_query": query_without(),
            "catalog_grid_columns": configured_columns,
            "catalog_can_edit": can_edit,
            "catalog_view_access_state": get_feature_access_state(request.user, STEAM_CATALOG_VIEW),
            "ads_feed_every_n": get_feed_every_n(request.user, request=request),
            **seo.list_meta(request, filters, page_obj),
            "catalog_base_template": _base_template(request),
        },
        page_title=build_page_title("Steam Catalog"),
        active_menu_key="steam_catalog",
    )


@login_required
@require_POST
def toggle_favorite(request, item_id: int):
    from .services import browse

    if not _catalog_view_access(request.user):
        return HttpResponseForbidden("Steam Catalog закрыт тарифом.")
    item = get_object_or_404(SteamItem, pk=item_id)
    state = browse.toggle_favorite(request.user, item)
    if request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({"favorite": state, "count": len(browse.favorite_keys(request.user))})
    target = request.POST.get("next") or ""
    if not target.startswith("/steam-catalog/") and not target.startswith(reverse("steam_catalog:list")):
        target = item.get_absolute_url()
    return redirect(target)


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
            return redirect(result.anchor_item)
    else:
        form = AddSteamItemForm()

    return render(request, "steam_catalog/add.html", {"form": form})


@login_required
def catalog_market_search(request):
    if not _catalog_staff_access(request.user):
        return JsonResponse({"error": "Поиск по Steam Market доступен только staff/admin."}, status=403)

    query = (request.GET.get("q") or "").strip()
    try:
        results = search_market_items(query=query)
    except MarketSearchError as exc:
        return JsonResponse({"query": query, "results": [], "error": str(exc)}, status=502)

    return JsonResponse({
        "query": query,
        "results": [
            {
                **result.as_dict(),
                "catalog_url": reverse("steam_catalog:detail", args=[result.catalog_item_id]) if result.catalog_item_id else "",
            }
            for result in results
        ],
    })


REFERENCE_RARITY_ORDER = ["consumer", "industrial", "milspec", "restricted", "classified", "covert", "extraordinary", "contraband"]


@login_required
def catalog_reference(request):
    if not (_catalog_staff_access(request.user) or _catalog_view_access(request.user)):
        return render_feature_locked_response(
            request,
            feature_code=STEAM_CATALOG_VIEW,
            title="Справочник CS2 закрыт тарифом",
            description="Справочник предметов открывается вместе с доступом к Steam Catalog.",
        )

    kind = request.GET.get("kind") or CS2ReferenceItem.Kind.SKIN
    if kind not in CS2ReferenceItem.Kind.values:
        kind = CS2ReferenceItem.Kind.SKIN
    q = (request.GET.get("q") or "").strip()
    weapon = (request.GET.get("weapon") or "").strip()
    collection = (request.GET.get("collection") or "").strip()
    rarity = (request.GET.get("rarity") or "").strip()
    category = (request.GET.get("category") or "").strip()

    base_qs = CS2ReferenceItem.objects.filter(kind=kind)
    queryset = base_qs
    if q:
        queryset = queryset.filter(Q(name__icontains=q) | Q(collection_name__icontains=q))
    if weapon:
        queryset = queryset.filter(weapon=weapon)
    if collection:
        queryset = queryset.filter(collection_name=collection)
    if rarity:
        queryset = queryset.filter(rarity_key=rarity)
    if category:
        queryset = queryset.filter(category=category)

    ordering = ("-release_date", "name") if kind == CS2ReferenceItem.Kind.CASE else ("weapon", "name")
    paginator = Paginator(queryset.order_by(*ordering), 48)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    page_items = list(page_obj.object_list)
    catalog_ids = catalog_ids_for_reference(page_items)
    for item in page_items:
        item.catalog_item_id = catalog_ids.get(item.id)

    present_rarities = set(base_qs.exclude(rarity_key="").values_list("rarity_key", flat=True).distinct())
    filter_query = urlencode({key: value for key, value in {
        "kind": kind, "q": q, "weapon": weapon, "collection": collection, "rarity": rarity, "category": category,
    }.items() if value})

    return render_dashboard_page(
        request,
        full_template="steam_catalog/reference.html",
        partial_template="steam_catalog/partials/reference_content.html",
        context={
            "page_obj": page_obj,
            "reference_items": page_items,
            "reference_kinds": [
                (value, label, CS2ReferenceItem.objects.filter(kind=value).count())
                for value, label in CS2ReferenceItem.Kind.choices
            ],
            "selected_kind": kind,
            "q": q,
            "selected_weapon": weapon,
            "selected_collection": collection,
            "selected_rarity": rarity,
            "selected_category": category,
            "category_options": list(base_qs.exclude(category="").values_list("category", flat=True).distinct().order_by("category")) if kind != CS2ReferenceItem.Kind.SKIN else [],
            "weapon_options": list(base_qs.exclude(weapon="").values_list("weapon", flat=True).distinct().order_by("weapon")),
            "collection_options": list(base_qs.exclude(collection_name="").values_list("collection_name", flat=True).distinct().order_by("collection_name")),
            "rarity_options": [(key, rarity_ru_label_from_key(key) or key) for key in REFERENCE_RARITY_ORDER if key in present_rarities],
            "filter_query": filter_query,
            "has_reference_filters": bool(q or weapon or collection or rarity or category),
            "reference_total": base_qs.count(),
            "catalog_can_edit": _catalog_staff_access(request.user),
            "bulk_import": read_bulk_import_status() if _catalog_staff_access(request.user) else None,
        },
        page_title=build_page_title("Справочник CS2"),
        active_menu_key="steam_catalog",
    )


@login_required
def catalog_reference_import_all(request):
    """One button: refresh the reference (stickers included) and add every item to the catalog in the background."""
    from .tasks import import_reference_to_catalog_task

    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")
    if request.method != "POST":
        return redirect("steam_catalog:reference")
    status = read_bulk_import_status() or {}
    if status.get("state") == "running":
        messages.info(request, "Выгрузка уже идёт — обновите страницу через пару минут.")
        return redirect("steam_catalog:reference")
    write_bulk_import_status({"state": "queued"})
    import_reference_to_catalog_task.delay()
    messages.success(request, "Выгрузка справочника запущена. Предметы появятся в каталоге через несколько минут, цены подтянутся очередью автообновления.")
    return redirect("steam_catalog:reference")


@login_required
def catalog_reference_add(request, reference_id: int):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")
    if request.method != "POST":
        return redirect("steam_catalog:reference")

    reference = get_object_or_404(CS2ReferenceItem, pk=reference_id)
    result = upsert_from_market_input(value=reference.catalog_input_value, stock=0)
    fill_catalog_from_reference(reference=reference)
    if result.variants_created:
        messages.success(request, f"{reference.name}: добавлено {result.variants_created} вариантов.")
    else:
        messages.success(request, f"{reference.name} добавлен в Steam Catalog.")
    return redirect(result.anchor_item)


def _is_favorite(user, item) -> bool:
    from .services.browse import is_favorite

    return is_favorite(user, item)


def catalog_detail(request, item_id: int, slug: str | None = None):
    if not _catalog_view_access(request.user):
        return _locked_for_guest(
            request,
            title="Карточка Steam Catalog закрыта тарифом",
            description="Доступ к карточкам каталога открывается только при активной подписке с feature Steam Catalog View.",
        )

    item = get_object_or_404(SteamItem, pk=item_id)
    if slug != item.slug:
        # One address per item for search engines: the old /<id>/ and outdated names redirect here.
        target = item.get_absolute_url()
        if request.GET:
            target = f"{target}?{request.GET.urlencode()}"
        return redirect(target, permanent=True)
    price_queue.note_item_view(request, item)

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

    can_view_deep_analytics = _catalog_analytics_access(request.user)
    guest_key = _guest_cache_key(request, f"detail:{item.pk}")
    history_data = _guest_cache_get(guest_key)
    if history_data is None:
        history_qs = item.price_history.all()
        if range_start is not None:
            history_qs = history_qs.filter(created_at__gte=range_start)

        filtered_history_asc = list(history_qs.order_by("created_at"))
        chart_entries = _build_raw_chart_entries(filtered_history_asc, selected_price_history_range)
        history_data = {
            # Guests never see the raw history table, so only the latest entry is kept for them.
            "price_history": list(reversed(filtered_history_asc[-50:] if not guest_key else filtered_history_asc[-1:])),
            "price_history_total": len(filtered_history_asc),
            "price_chart": _build_price_chart(chart_entries),
            "price_analytics": _build_price_analytics(filtered_history_asc),
        }
        _guest_cache_set(guest_key, history_data)
    price_history = history_data["price_history"]
    price_chart = history_data["price_chart"]
    price_analytics = history_data["price_analytics"]

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
            "is_favorite": _is_favorite(request.user, item),
            "family_items": family_items,
            "family_min_price": family_min_price,
            "family_max_price": family_max_price,
            "family_total_stock": family_total_stock,
            "stock_form": stock_form,
            "name_ru_form": name_ru_form,
            "moves": item.stock_moves.select_related("reason", "created_by")[:20],
            "price_history": price_history,
            "price_history_total": history_data["price_history_total"],
            "price_chart": price_chart,
            "price_analytics": price_analytics,
            "latest_price_entry": price_history[0] if price_history else None,
            "price_history_range_choices": PRICE_HISTORY_RANGE_CHOICES,
            "selected_price_history_range": selected_price_history_range,
            "price_history_range_note": _get_range_note(selected_price_history_range),
            "auto_update_rule": auto_update_rule,
            "auto_update_enabled": auto_update_rule is not None,
            "catalog_can_edit": _catalog_staff_access(request.user),
            "catalog_can_view_deep_analytics": can_view_deep_analytics,
            "catalog_analytics_access_state": get_feature_access_state(request.user, STEAM_CATALOG_DEEP_ANALYTICS),
            "steam_market_url": canonical_listing_url(appid=730, market_hash_name=item.market_hash_name),
            "steam_catalog_detail_content_template": detail_content_template,
            "steam_catalog_detail_mode_template": detail_mode_template,
            **seo.item_meta(request, item, family_min_price=family_min_price, family_max_price=family_max_price),
            "catalog_base_template": _base_template(request),
        },
        page_title=seo.item_title(item),
        active_menu_key="steam_catalog",
    )


@login_required
def update_name_ru(request, item_id: int):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")

    item = get_object_or_404(SteamItem, pk=item_id)
    if request.method != "POST":
        return redirect(item)

    form = SteamItemNameRuForm(request.POST, instance=item)
    if not form.is_valid():
        messages.error(request, "Не удалось сохранить РУ_нейм.")
        return redirect(item)

    name_ru = (form.cleaned_data.get("name_ru") or "").strip() or None

    if item.item_type == SteamItem.ItemType.SKIN:
        SteamItem.objects.filter(family_name=item.family_name).update(name_ru=name_ru)
        messages.success(request, "РУ_нейм обновлён для всех вариантов скина.")
    else:
        item.name_ru = name_ru
        item.save(update_fields=["name_ru", "updated_at"])
        messages.success(request, "РУ_нейм обновлён.")

    return redirect(item)


@login_required
def update_price(request, item_id: int):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")

    item = get_object_or_404(SteamItem, pk=item_id)

    if request.method != "POST":
        return redirect(item)

    result = refresh_item_prices(
        item,
        include_family=(item.item_type == SteamItem.ItemType.SKIN),
        source="manual",
    )

    if result.touched_count == 0:
        messages.error(request, "Не удалось получить актуальную цену из Steam.")
        return redirect(item)

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

    return redirect(item)


@login_required
def add_stock_move(request, item_id: int):
    if not _catalog_staff_access(request.user):
        return HttpResponseForbidden("Steam Catalog доступен только staff/admin.")

    item = get_object_or_404(SteamItem, pk=item_id)

    if request.method != "POST":
        return redirect(item)

    form = StockMovementCreateForm(request.POST)
    if form.is_valid():
        form.save(item=item, created_by=request.user)
        messages.success(request, "Движение склада сохранено.")
    else:
        messages.error(request, "Не удалось сохранить движение склада.")

    return redirect(item)


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
        with transaction.atomic():
            lock_alert_quota(request.user)
            saved = form.is_valid() and bool(form.save())
        if saved:
            messages.success(request, "Правило уведомлений сохранено.")
            return redirect("steam_catalog:alert_rules")
    else:
        initial = {}
        item_param = request.GET.get("item", "")
        prefill_item = SteamItem.objects.filter(pk=int(item_param), is_active=True).first() if item_param.isdigit() else None
        if prefill_item is not None:
            initial = {
                "scope_type": UserPriceAlertRule.ScopeType.ITEM,
                "item": prefill_item.pk,
                "name": prefill_item.market_hash_name,
            }
        form = UserPriceAlertRuleForm(user=request.user, initial=initial)
    return render(request, "steam_catalog/alert_rule_form.html", {"form": form, "role_access": get_user_role(request.user), "is_create": True})


@login_required
def user_alert_rule_edit(request, rule_id: int):
    rule = get_object_or_404(UserPriceAlertRule, pk=rule_id, user=request.user)
    if request.method == "POST":
        with transaction.atomic():
            lock_alert_quota(request.user)
            # Re-read the rule under the lock so the form sees its current status.
            rule = get_object_or_404(UserPriceAlertRule.objects.select_for_update(), pk=rule_id, user=request.user)
            form = UserPriceAlertRuleForm(request.POST, instance=rule, user=request.user)
            saved = form.is_valid() and bool(form.save())
        if saved:
            messages.success(request, "Правило уведомлений обновлено.")
            return redirect("steam_catalog:alert_rules")
    else:
        form = UserPriceAlertRuleForm(instance=rule, user=request.user)
    return render(request, "steam_catalog/alert_rule_form.html", {"form": form, "role_access": get_user_role(request.user), "is_create": False, "rule": rule})


@login_required
def user_alert_rule_toggle(request, rule_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Method not allowed.")
    with transaction.atomic():
        lock_alert_quota(request.user)
        rule = get_object_or_404(UserPriceAlertRule.objects.select_for_update(), pk=rule_id, user=request.user)
        if rule.status != UserPriceAlertRule.Status.ACTIVE:
            limit_message = alert_limit_error(request.user)
            if limit_message:
                messages.error(request, limit_message)
                return redirect("steam_catalog:alert_rules")
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
