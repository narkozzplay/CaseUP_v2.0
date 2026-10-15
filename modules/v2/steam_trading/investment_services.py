from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import QuerySet
from django.utils import timezone

from .models import SteamPurchase
from .services import ZERO, quantize_money, with_purchase_sales_annotations


@dataclass(frozen=True)
class InvestmentRow:
    purchase: SteamPurchase
    current_price_rub: Decimal
    remaining_quantity: int
    sold_quantity_total: int
    invested_active_rub: Decimal
    current_value_rub: Decimal
    unrealized_pnl_rub: Decimal
    unrealized_pnl_percent: Decimal
    realized_pnl_rub: Decimal
    status_code: str
    status_label: str
    days_to_unlock: int | None
    is_price_missing: bool


@dataclass(frozen=True)
class InvestmentKpi:
    total_spent_sum: Decimal
    active_cost_basis_sum: Decimal
    current_active_value_sum: Decimal
    unrealized_pnl_sum: Decimal
    realized_pnl_sum: Decimal
    active_purchases_count: int
    ready_count: int
    hold_count: int
    nearest_unlock_at: object


@dataclass(frozen=True)
class UpcomingUnlocks:
    today: list[InvestmentRow]
    tomorrow: list[InvestmentRow]
    next_7_days: list[InvestmentRow]


@dataclass(frozen=True)
class InvestmentAnalytics:
    kpi: InvestmentKpi
    active_rows: list[InvestmentRow]
    ready_rows: list[InvestmentRow]
    upcoming_unlocks: UpcomingUnlocks
    top_gainers: list[InvestmentRow]
    top_losers: list[InvestmentRow]


@dataclass(frozen=True)
class InvestmentFilterState:
    query: str
    status: str
    sort: str


@dataclass(frozen=True)
class InvestmentDashboard:
    analytics: InvestmentAnalytics
    filtered_active_rows: list[InvestmentRow]
    filter_state: InvestmentFilterState
    total_active_count: int
    filtered_active_count: int

    @property
    def has_active_filters(self) -> bool:
        return bool(
            self.filter_state.query
            or self.filter_state.status != FILTER_STATUS_ALL
            or self.filter_state.sort != SORT_PURCHASED_DESC
        )


STATUS_HOLD = "hold"
STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_CLOSED = "closed"

STATUS_LABELS = {
    STATUS_HOLD: "В трейд-бане",
    STATUS_READY: "Можно продать",
    STATUS_PARTIAL: "Частично продано",
    STATUS_CLOSED: "Закрыто",
}

FILTER_STATUS_ALL = "all"

STATUS_FILTER_OPTIONS = (
    (FILTER_STATUS_ALL, "Все"),
    (STATUS_READY, "Можно продать"),
    (STATUS_HOLD, "В трейд-бане"),
    (STATUS_PARTIAL, "Частично проданы"),
)

SORT_PURCHASED_DESC = "purchased_desc"
SORT_UNLOCK_ASC = "unlock_asc"
SORT_PNL_DESC = "pnl_desc"
SORT_PNL_ASC = "pnl_asc"
SORT_PNL_PERCENT_DESC = "pnl_percent_desc"
SORT_VALUE_DESC = "value_desc"
SORT_REMAINING_DESC = "remaining_desc"

SORT_OPTIONS = (
    (SORT_PURCHASED_DESC, "Сначала новые"),
    (SORT_UNLOCK_ASC, "Скоро разблокируются"),
    (SORT_PNL_DESC, "Больше прибыль, ₽"),
    (SORT_PNL_ASC, "Больше убыток, ₽"),
    (SORT_PNL_PERCENT_DESC, "Выше доходность, %"),
    (SORT_VALUE_DESC, "Дороже сейчас"),
    (SORT_REMAINING_DESC, "Больше штук на руках"),
)


def get_investment_purchase_queryset(*, user=None) -> QuerySet[SteamPurchase]:
    qs = with_purchase_sales_annotations(
        SteamPurchase.objects.select_related("item", "created_by")
    ).order_by("-purchased_at", "-id")

    if user is not None:
        qs = qs.filter(created_by=user)

    return qs


def _is_purchase_unlocked(purchase: SteamPurchase, *, now) -> bool:
    if purchase.unlocked_at is None:
        return False
    return purchase.unlocked_at <= now


def _calculate_days_to_unlock(purchase: SteamPurchase, *, now):
    if purchase.unlocked_at is None or _is_purchase_unlocked(purchase, now=now):
        return None

    now_local = timezone.localtime(now)
    unlock_local = timezone.localtime(purchase.unlocked_at)
    return max((unlock_local.date() - now_local.date()).days, 0)


def _resolve_status(
    *,
    purchase: SteamPurchase,
    remaining_quantity: int,
    sold_quantity_total: int,
    now,
) -> tuple[str, str]:
    if remaining_quantity <= 0:
        return STATUS_CLOSED, STATUS_LABELS[STATUS_CLOSED]
    if sold_quantity_total > 0:
        return STATUS_PARTIAL, STATUS_LABELS[STATUS_PARTIAL]
    if _is_purchase_unlocked(purchase, now=now):
        return STATUS_READY, STATUS_LABELS[STATUS_READY]
    return STATUS_HOLD, STATUS_LABELS[STATUS_HOLD]


def build_investment_row(purchase: SteamPurchase, *, now=None) -> InvestmentRow:
    now = now or timezone.now()

    remaining_quantity = int(purchase.remaining_quantity)
    sold_quantity_total = int(purchase.sold_quantity_total)

    current_price_rub = quantize_money(purchase.current_price_rub)
    invested_active_rub = quantize_money((purchase.purchase_price_rub or ZERO) * Decimal(remaining_quantity))
    current_value_rub = quantize_money(current_price_rub * Decimal(remaining_quantity))
    unrealized_pnl_rub = quantize_money(current_value_rub - invested_active_rub)
    unrealized_pnl_percent = ZERO
    if invested_active_rub != ZERO:
        unrealized_pnl_percent = quantize_money((unrealized_pnl_rub / invested_active_rub) * Decimal("100"))

    status_code, status_label = _resolve_status(
        purchase=purchase,
        remaining_quantity=remaining_quantity,
        sold_quantity_total=sold_quantity_total,
        now=now,
    )

    return InvestmentRow(
        purchase=purchase,
        current_price_rub=current_price_rub,
        remaining_quantity=remaining_quantity,
        sold_quantity_total=sold_quantity_total,
        invested_active_rub=invested_active_rub,
        current_value_rub=current_value_rub,
        unrealized_pnl_rub=unrealized_pnl_rub,
        unrealized_pnl_percent=unrealized_pnl_percent,
        realized_pnl_rub=quantize_money(purchase.realized_profit_rub),
        status_code=status_code,
        status_label=status_label,
        days_to_unlock=_calculate_days_to_unlock(purchase, now=now),
        is_price_missing=purchase.item.price_rub is None,
    )


def build_investment_rows(purchases, *, now=None) -> list[InvestmentRow]:
    now = now or timezone.now()
    return [build_investment_row(purchase, now=now) for purchase in purchases]


def build_investment_analytics(purchases) -> InvestmentAnalytics:
    now = timezone.now()
    rows = build_investment_rows(purchases, now=now)
    active_rows = [row for row in rows if row.remaining_quantity > 0]
    ready_rows = [row for row in active_rows if row.status_code == STATUS_READY]
    hold_rows = [row for row in active_rows if row.status_code == STATUS_HOLD]

    now_local = timezone.localtime(now)
    tomorrow_date = now_local.date() + timedelta(days=1)
    seven_days_date = now_local.date() + timedelta(days=7)

    upcoming_rows = [
        row
        for row in active_rows
        if row.purchase.unlocked_at and not _is_purchase_unlocked(row.purchase, now=now)
    ]
    upcoming_rows.sort(key=lambda row: (row.purchase.unlocked_at, row.purchase.id))

    today_rows = []
    tomorrow_rows = []
    next_7_days_rows = []

    for row in upcoming_rows:
        unlock_date = timezone.localtime(row.purchase.unlocked_at).date()
        if unlock_date == now_local.date():
            today_rows.append(row)
        elif unlock_date == tomorrow_date:
            tomorrow_rows.append(row)
        elif tomorrow_date < unlock_date <= seven_days_date:
            next_7_days_rows.append(row)

    active_cost_basis_sum = quantize_money(sum((row.invested_active_rub for row in active_rows), ZERO))
    current_active_value_sum = quantize_money(sum((row.current_value_rub for row in active_rows), ZERO))
    unrealized_pnl_sum = quantize_money(sum((row.unrealized_pnl_rub for row in active_rows), ZERO))
    realized_pnl_sum = quantize_money(sum((row.realized_pnl_rub for row in rows), ZERO))
    nearest_unlock_at = upcoming_rows[0].purchase.unlocked_at if upcoming_rows else None

    gainers = sorted(
        active_rows,
        key=lambda row: (row.unrealized_pnl_percent, row.unrealized_pnl_rub, row.purchase.id),
        reverse=True,
    )[:5]

    losers = sorted(
        active_rows,
        key=lambda row: (row.unrealized_pnl_percent, row.unrealized_pnl_rub, row.purchase.id),
    )[:5]

    return InvestmentAnalytics(
        kpi=InvestmentKpi(
            total_spent_sum=quantize_money(sum((purchase.total_spent_rub for purchase in purchases), ZERO)),
            active_cost_basis_sum=active_cost_basis_sum,
            current_active_value_sum=current_active_value_sum,
            unrealized_pnl_sum=unrealized_pnl_sum,
            realized_pnl_sum=realized_pnl_sum,
            active_purchases_count=len(active_rows),
            ready_count=len(ready_rows),
            hold_count=len(hold_rows),
            nearest_unlock_at=nearest_unlock_at,
        ),
        active_rows=active_rows,
        ready_rows=ready_rows,
        upcoming_unlocks=UpcomingUnlocks(
            today=today_rows,
            tomorrow=tomorrow_rows,
            next_7_days=next_7_days_rows,
        ),
        top_gainers=gainers,
        top_losers=losers,
    )


def normalize_investment_filter_state(*, query: str = "", status: str = "", sort: str = "") -> InvestmentFilterState:
    normalized_query = (query or "").strip()
    normalized_status = status if status in {value for value, _ in STATUS_FILTER_OPTIONS} else FILTER_STATUS_ALL
    normalized_sort = sort if sort in {value for value, _ in SORT_OPTIONS} else SORT_PURCHASED_DESC
    return InvestmentFilterState(
        query=normalized_query,
        status=normalized_status,
        sort=normalized_sort,
    )


def _row_matches_query(row: InvestmentRow, query: str) -> bool:
    if not query:
        return True

    haystack = " ".join(
        filter(
            None,
            [
                row.purchase.item.group_name,
                row.purchase.item.market_hash_name,
                row.purchase.item.name,
                row.purchase.item.name_ru or "",
                str(row.purchase.id),
            ],
        )
    ).lower()

    return query.lower() in haystack


def filter_investment_rows(rows: list[InvestmentRow], filter_state: InvestmentFilterState) -> list[InvestmentRow]:
    filtered = rows

    if filter_state.status != FILTER_STATUS_ALL:
        filtered = [row for row in filtered if row.status_code == filter_state.status]

    if filter_state.query:
        filtered = [row for row in filtered if _row_matches_query(row, filter_state.query)]

    return filtered


def sort_investment_rows(rows: list[InvestmentRow], sort_code: str) -> list[InvestmentRow]:
    if sort_code == SORT_UNLOCK_ASC:
        return sorted(
            rows,
            key=lambda row: (
                row.days_to_unlock is None,
                row.days_to_unlock if row.days_to_unlock is not None else 10**6,
                row.purchase.unlocked_at or timezone.make_aware(timezone.datetime.max.replace(year=9999)),
                row.purchase.id,
            ),
        )

    if sort_code == SORT_PNL_DESC:
        return sorted(
            rows,
            key=lambda row: (row.unrealized_pnl_rub, row.unrealized_pnl_percent, row.purchase.id),
            reverse=True,
        )

    if sort_code == SORT_PNL_ASC:
        return sorted(
            rows,
            key=lambda row: (row.unrealized_pnl_rub, row.unrealized_pnl_percent, row.purchase.id),
        )

    if sort_code == SORT_PNL_PERCENT_DESC:
        return sorted(
            rows,
            key=lambda row: (row.unrealized_pnl_percent, row.unrealized_pnl_rub, row.purchase.id),
            reverse=True,
        )

    if sort_code == SORT_VALUE_DESC:
        return sorted(
            rows,
            key=lambda row: (row.current_value_rub, row.remaining_quantity, row.purchase.id),
            reverse=True,
        )

    if sort_code == SORT_REMAINING_DESC:
        return sorted(
            rows,
            key=lambda row: (row.remaining_quantity, row.current_value_rub, row.purchase.id),
            reverse=True,
        )

    return sorted(
        rows,
        key=lambda row: (row.purchase.purchased_at, row.purchase.id),
        reverse=True,
    )


def build_investment_dashboard(
    purchases,
    *,
    query: str = "",
    status: str = "",
    sort: str = "",
) -> InvestmentDashboard:
    analytics = build_investment_analytics(purchases)
    filter_state = normalize_investment_filter_state(query=query, status=status, sort=sort)
    filtered_active_rows = sort_investment_rows(
        filter_investment_rows(analytics.active_rows, filter_state),
        filter_state.sort,
    )

    return InvestmentDashboard(
        analytics=analytics,
        filtered_active_rows=filtered_active_rows,
        filter_state=filter_state,
        total_active_count=len(analytics.active_rows),
        filtered_active_count=len(filtered_active_rows),
    )