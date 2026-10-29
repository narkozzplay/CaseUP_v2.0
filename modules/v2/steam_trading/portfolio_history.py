"""Portfolio over time: its value and cost basis day by day, and the week's summary.

Nothing is stored: each day is rebuilt from the purchases, the sales and the catalog price history, so a purchase
added with an old date shows up in the past too. A day's price is the last history price before the day ends; the
last day takes the catalog's current price, so the chart ends where the KPI cards are.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.db.models import Max, OuterRef, Subquery
from django.utils import timezone

from modules.v2.steam_catalog.models import SWEEP_SOURCE, SteamItem, SteamItemPriceHistory

from .models import SteamPurchase, SteamPurchaseSale
from .services import quantize_money

ZERO = Decimal("0.00")
# Steam keeps 15 % of the buyer's price (5 % Steam + 10 % CS2), so the seller gets price / 1.15.
STEAM_FEE_DIVISOR = Decimal("1.15")

RANGE_7D = "7d"
RANGE_30D = "30d"
RANGE_90D = "90d"
RANGE_ALL = "all"
RANGE_CHOICES = ((RANGE_7D, "7 дней"), (RANGE_30D, "30 дней"), (RANGE_90D, "90 дней"), (RANGE_ALL, "Всё время"))
RANGE_DAYS = {RANGE_7D: 7, RANGE_30D: 30, RANGE_90D: 90, RANGE_ALL: None}
DEFAULT_RANGE = RANGE_30D

CACHE_SECONDS = 15 * 60


@dataclass(frozen=True)
class PortfolioDay:
    day: date
    value_rub: Decimal
    invested_rub: Decimal
    # Profit from sales up to the end of the day.
    realized_rub: Decimal

    @property
    def unrealized_rub(self) -> Decimal:
        return self.value_rub - self.invested_rub

    @property
    def total_pnl_rub(self) -> Decimal:
        return self.unrealized_rub + self.realized_rub


@dataclass(frozen=True)
class WeekMover:
    item_id: int
    name: str
    quantity: int
    change_rub: Decimal
    change_percent: Decimal


@dataclass(frozen=True)
class WeekSummary:
    start: date
    end: date
    value_rub: Decimal
    change_rub: Decimal
    change_percent: Decimal | None
    gainers: list[WeekMover] = field(default_factory=list)
    losers: list[WeekMover] = field(default_factory=list)
    sold_quantity: int = 0
    sold_total_rub: Decimal = ZERO
    sold_profit_rub: Decimal = ZERO


def after_steam_fee(value: Decimal) -> Decimal:
    """What the seller gets for items listed at `value` on the Steam market."""
    return quantize_money((value or ZERO) / STEAM_FEE_DIVISOR)


def normalize_range(value: str) -> str:
    return value if value in RANGE_DAYS else DEFAULT_RANGE


def _day_end(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), timezone.get_current_timezone())


class _PriceBook:
    """Each item's price at the end of any day, from its history and its current catalog price."""

    def __init__(self, purchases, *, start: date, today: date):
        items = {purchase.item_id: purchase.item for purchase in purchases}
        self.today = today
        self.current = {item_id: item.price_rub for item_id, item in items.items()}
        self._times: dict[int, list[datetime]] = {}
        self._prices: dict[int, list[Decimal]] = {}
        start_at = _day_end(start - timedelta(days=1))
        rows = defaultdict(list)
        # The last median and the last sweep price before the range, so the first day is not empty: one query.
        earlier = SteamItemPriceHistory.objects.filter(item_id=OuterRef("pk"), created_at__lt=start_at).order_by("-created_at", "-id")
        medians, sweeps = earlier.exclude(source=SWEEP_SOURCE), earlier.filter(source=SWEEP_SOURCE)
        for item_id, *values in SteamItem.objects.filter(pk__in=list(items)).annotate(
            median_at=Subquery(medians.values("created_at")[:1]),
            median_price=Subquery(medians.values("price_rub")[:1]),
            sweep_at=Subquery(sweeps.values("created_at")[:1]),
            sweep_price=Subquery(sweeps.values("price_rub")[:1]),
        ).values_list("pk", "median_at", "median_price", "sweep_at", "sweep_price"):
            median_at, median_price, sweep_at, sweep_price = values
            before = []
            if median_at:
                before.append((median_at, median_price, "median"))
            if sweep_at:
                before.append((sweep_at, sweep_price, SWEEP_SOURCE))
            rows[item_id].extend(sorted(before, key=lambda row: row[0]))
        for item_id, created_at, price, source in (
            SteamItemPriceHistory.objects.filter(item_id__in=list(items), created_at__gte=start_at)
            .order_by("created_at", "id")
            .values_list("item_id", "created_at", "price_rub", "source")
        ):
            rows[item_id].append((created_at, price, source))
        for item_id, entries in rows.items():
            # The sweep's lowest listing is not comparable with the median; use it only for items without a median.
            median = [entry for entry in entries if entry[2] != SWEEP_SOURCE]
            chosen = median or entries
            self._times[item_id] = [entry[0] for entry in chosen]
            self._prices[item_id] = [entry[1] for entry in chosen]

    def price(self, item_id: int, day: date) -> Decimal | None:
        if day >= self.today and self.current.get(item_id) is not None:
            return self.current[item_id]
        times = self._times.get(item_id)
        if not times:
            return self.current.get(item_id) if day >= self.today else None
        index = bisect_left(times, _day_end(day)) - 1
        return self._prices[item_id][index] if index >= 0 else None


def _sales_by_purchase(purchases, *, after_fee: bool = False) -> dict[int, list[tuple[datetime, int, Decimal, Decimal]]]:
    """{purchase id: [(sold at, quantity, total, profit)]}; after_fee takes Steam's cut off the sale price as well."""
    sales = defaultdict(list)
    for purchase_id, sold_at, quantity, total, profit, cost in SteamPurchaseSale.objects.filter(
        purchase_id__in=[purchase.pk for purchase in purchases]
    ).values_list("purchase_id", "sold_at", "quantity", "total_sale_rub", "profit_rub", "cost_basis_rub"):
        if after_fee:
            total = total / STEAM_FEE_DIVISOR
            profit = total - cost
        sales[purchase_id].append((sold_at, int(quantity), total, profit))
    return sales


def _held(purchase: SteamPurchase, sales, until: datetime) -> int:
    if purchase.purchased_at >= until:
        return 0
    sold = sum(quantity for sold_at, quantity, _total, _profit in sales if sold_at < until)
    return max(int(purchase.quantity) - sold, 0)


def _first_day(purchases, days: int | None, today: date) -> date:
    first_purchase = min(timezone.localtime(purchase.purchased_at).date() for purchase in purchases)
    if days is None:
        return min(first_purchase, today)
    return min(max(today - timedelta(days=days - 1), first_purchase), today)


def build_portfolio_days(purchases, *, days: int | None, now: datetime | None = None, after_fee: bool = False) -> list[PortfolioDay]:
    """One row per day from the range start (or the first purchase, if later) through today."""
    purchases = list(purchases)
    if not purchases:
        return []
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    start = _first_day(purchases, days, today)
    book = _PriceBook(purchases, start=start, today=today)
    sales = _sales_by_purchase(purchases, after_fee=after_fee)

    result = []
    day = start
    while day <= today:
        until = now if day == today else _day_end(day)
        value = invested = realized = ZERO
        for purchase in purchases:
            purchase_sales = sales.get(purchase.pk, [])
            realized += sum((profit for sold_at, _q, _t, profit in purchase_sales if sold_at < until), ZERO)
            held = _held(purchase, purchase_sales, until)
            if not held:
                continue
            cost = purchase.purchase_price_rub or ZERO
            price = book.price(purchase.item_id, day)
            if price is None:
                # No price known yet: count it at cost, so it neither adds nor hides profit.
                price = cost
            elif after_fee:
                price = price / STEAM_FEE_DIVISOR
            invested += cost * held
            value += price * held
        result.append(
            PortfolioDay(day=day, value_rub=quantize_money(value), invested_rub=quantize_money(invested), realized_rub=quantize_money(realized))
        )
        day += timedelta(days=1)
    return result


def _percent(part: Decimal, whole: Decimal) -> Decimal | None:
    if not whole:
        return None
    return (part / whole * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def build_week_summary(purchases, *, now: datetime | None = None, after_fee: bool = False, movers: int = 3) -> WeekSummary | None:
    """The last seven days: how much the portfolio earned (price moves plus sales), its movers and its sales."""
    purchases = list(purchases)
    if not purchases:
        return None
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    week_start = today - timedelta(days=7)
    days = build_portfolio_days(purchases, days=8, now=now, after_fee=after_fee)
    last = days[-1]
    first = days[0] if days[0].day == week_start else None
    base_pnl = first.total_pnl_rub if first else ZERO
    change = quantize_money(last.total_pnl_rub - base_pnl)
    base_value = first.value_rub if first and first.value_rub else last.invested_rub

    # Movers: what each item's price did to the units on hand, since the week start or since it was bought.
    book = _PriceBook(purchases, start=week_start, today=today)
    sales = _sales_by_purchase(purchases, after_fee=after_fee)
    since = _day_end(week_start)
    per_item: dict[int, dict] = {}
    for purchase in purchases:
        held = _held(purchase, sales.get(purchase.pk, []), now)
        now_price = book.price(purchase.item_id, today)
        if not held or now_price is None:
            continue
        if purchase.purchased_at < since:
            then_price = book.price(purchase.item_id, week_start)
        else:
            then_price = None
        if then_price is None:
            then_price = purchase.purchase_price_rub or ZERO
        if after_fee:
            now_price = now_price / STEAM_FEE_DIVISOR
            if purchase.purchased_at < since:
                then_price = then_price / STEAM_FEE_DIVISOR
        row = per_item.setdefault(
            purchase.item_id, {"name": purchase.item.group_name, "quantity": 0, "change": ZERO, "base": ZERO}
        )
        row["quantity"] += held
        row["change"] += (now_price - then_price) * held
        row["base"] += then_price * held
    item_movers = [
        WeekMover(
            item_id=item_id,
            name=row["name"],
            quantity=row["quantity"],
            change_rub=quantize_money(row["change"]),
            change_percent=_percent(row["change"], row["base"]) or Decimal("0.0"),
        )
        for item_id, row in per_item.items()
    ]
    gainers = sorted((m for m in item_movers if m.change_rub > 0), key=lambda m: m.change_rub, reverse=True)[:movers]
    losers = sorted((m for m in item_movers if m.change_rub < 0), key=lambda m: m.change_rub)[:movers]

    week_sales = [sale for rows in sales.values() for sale in rows if since <= sale[0] <= now]
    return WeekSummary(
        start=week_start,
        end=today,
        value_rub=last.value_rub,
        change_rub=change,
        change_percent=_percent(change, base_value),
        gainers=gainers,
        losers=losers,
        sold_quantity=sum(sale[1] for sale in week_sales),
        sold_total_rub=quantize_money(sum((sale[2] for sale in week_sales), ZERO)),
        sold_profit_rub=quantize_money(sum((sale[3] for sale in week_sales), ZERO)),
    )


def item_week_changes(purchases, *, now: datetime | None = None) -> dict[int, tuple[Decimal, Decimal]]:
    """{item id: (change in ₽, change in %)} of each item's price over the last seven days."""
    purchases = list(purchases)
    if not purchases:
        return {}
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    week_start = today - timedelta(days=7)
    book = _PriceBook(purchases, start=week_start, today=today)
    changes = {}
    for item_id in {purchase.item_id for purchase in purchases}:
        then, current = book.price(item_id, week_start), book.price(item_id, today)
        if then and current is not None:
            rub = quantize_money(current - then)
            changes[item_id] = (rub, _percent(rub, then))
    return changes


def _cache_key(user, kind: str, *parts) -> str | None:
    stamp = SteamPurchase.objects.filter(created_by=user).aggregate(
        purchase=Max("updated_at"), sale=Max("sales__updated_at")
    )
    if not stamp["purchase"]:
        return None
    tail = ":".join(str(part) for part in parts)
    return f"steam_trading:portfolio:{kind}:{user.pk}:{stamp['purchase'].timestamp()}:{stamp['sale'] and stamp['sale'].timestamp()}:{tail}"


def _cached(user, kind: str, parts: tuple, build):
    key = _cache_key(user, kind, *parts, timezone.localdate())
    if key:
        hit = cache.get(key)
        if hit is not None:
            return hit
    value = build()
    if key:
        cache.set(key, value, CACHE_SECONDS)
    return value


def cached_portfolio_days(user, purchases, *, range_code: str, after_fee: bool = False) -> list[PortfolioDay]:
    """build_portfolio_days for the user's page, cached for a few minutes (prices move every couple of hours)."""
    range_code = normalize_range(range_code)
    return _cached(
        user, "days", (range_code, int(after_fee)),
        lambda: build_portfolio_days(purchases, days=RANGE_DAYS[range_code], after_fee=after_fee),
    )


def cached_week_summary(user, purchases, *, after_fee: bool = False):
    return _cached(user, "week", (int(after_fee),), lambda: build_week_summary(purchases, after_fee=after_fee) or False) or None


def cached_item_week_changes(user, purchases) -> dict:
    return _cached(user, "items", (), lambda: item_week_changes(purchases))


def _money_label(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _signed_label(value: Decimal) -> str:
    return ("+" if value > 0 else "") + _money_label(value)


def build_portfolio_chart(days: list[PortfolioDay]) -> dict:
    """SVG paths and axis ticks for the value line and the dashed cost line, drawn like the item price chart."""
    from modules.v2.steam_catalog.views import (
        _CHART_VIEW_HEIGHT,
        _CHART_VIEW_WIDTH,
        _CHART_X_TICK_COUNT,
        _build_chart_scale,
        _format_chart_tick,
        _svg_number,
    )

    if not days:
        return {"has_data": False}
    n = len(days)
    low, high, step, ticks = _build_chart_scale([d.value_rub for d in days] + [d.invested_rub for d in days])
    span = high - low

    def y_percent(value) -> float:
        return round((1 - (float(value) - low) / span) * 100, 3)

    def x_percent(index: int) -> float:
        return 50.0 if n == 1 else round(index / (n - 1) * 100, 3)

    def path(values) -> str:
        if n == 1:
            return f"M0 {_svg_number(y_percent(values[0]) * _CHART_VIEW_HEIGHT / 100)} H{_CHART_VIEW_WIDTH}"
        return "M" + " L".join(
            f"{_svg_number(x_percent(i) * _CHART_VIEW_WIDTH / 100)} {_svg_number(y_percent(v) * _CHART_VIEW_HEIGHT / 100)}"
            for i, v in enumerate(values)
        )

    value_path = path([d.value_rub for d in days])
    invested_path = path([d.invested_rub for d in days])
    area_path = f"{value_path} L{_CHART_VIEW_WIDTH} {_CHART_VIEW_HEIGHT} L0 {_CHART_VIEW_HEIGHT} Z"

    points = []
    for index, day in enumerate(days):
        pnl = day.unrealized_rub
        points.append(
            {
                "x": x_percent(index),
                "y": y_percent(day.value_rub),
                "price": _money_label(day.value_rub),
                "date": day.day.strftime("%d.%m.%Y"),
                "note": f"вложено {_money_label(day.invested_rub)} · прибыль {_signed_label(pnl)}",
                "dir": "up" if pnl > 0 else ("down" if pnl < 0 else "flat"),
            }
        )

    wanted = min(n, _CHART_X_TICK_COUNT)
    tick_indices = [0] if n == 1 else sorted({round(i * (n - 1) / (wanted - 1)) for i in range(wanted)})
    x_ticks = []
    for index in tick_indices:
        x = x_percent(index)
        x_ticks.append({"x": x, "label": days[index].day.strftime("%d.%m"), "align": "start" if x < 8 else ("end" if x > 92 else "center")})

    last = days[-1]
    return {
        "has_data": True,
        "is_single": n == 1,
        "points_json": points,
        "value_path": value_path,
        "invested_path": invested_path,
        "area_path": area_path,
        "view_width": _CHART_VIEW_WIDTH,
        "view_height": _CHART_VIEW_HEIGHT,
        "y_ticks": [{"y": y_percent(v), "label": _format_chart_tick(v, step)} for v in reversed(ticks)],
        "x_ticks": x_ticks,
        "last_point": {"x": points[-1]["x"], "y": points[-1]["y"], "label": points[-1]["price"]},
        "first": days[0],
        "last": last,
        "period_change_rub": quantize_money(last.total_pnl_rub - days[0].total_pnl_rub),
    }
