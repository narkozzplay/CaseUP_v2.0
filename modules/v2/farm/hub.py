"""Read models for the farm board and the "Ферма + Инвестиции" overview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .connect import ConnectionCheck, build_connection_checks, connection_state
from .models import (
    FarmAccount,
    FarmAccountInventoryItem,
    FarmAccountInventorySync,
    FarmDropItem,
    FarmDropSale,
    FarmDropSession,
)
from .services import DropWeek, get_current_drop_week, get_drop_week_by_index, q_money

ZERO = Decimal("0.00")
WEEK_HISTORY_LENGTH = 8
SOURCE_LABELS_RU = {"weekly": "недельный", "bonus": "бонус", "event": "ивент", "other": "другое"}


@dataclass(frozen=True)
class AccountBoardRow:
    account: FarmAccount
    week_session: FarmDropSession | None
    drops_total: int
    last_drop_at: object
    inventory_items: int
    inventory_value_rub: Decimal
    latest_sync: FarmAccountInventorySync | None
    checks: tuple[ConnectionCheck, ...]
    connection: str

    @property
    def has_week_drop(self) -> bool:
        return self.week_session is not None

    @property
    def is_farming(self) -> bool:
        return self.account.status == FarmAccount.Status.ACTIVE and self.account.role == FarmAccount.Role.FARM


@dataclass(frozen=True)
class WeekBar:
    week: DropWeek
    accounts_with_drop: int
    percent: int
    is_current: bool


@dataclass(frozen=True)
class WeekProgress:
    week: DropWeek
    done: int
    target: int
    percent: int
    next_reset_at: datetime
    pending: tuple[AccountBoardRow, ...]
    history: tuple[WeekBar, ...]

    @property
    def is_complete(self) -> bool:
        return self.target > 0 and self.done >= self.target


@dataclass(frozen=True)
class ActivityEntry:
    kind: str  # drop / farm_sale / purchase / purchase_sale
    at: datetime
    title: str
    subtitle: str
    amount_rub: Decimal | None
    url: str


def week_bounds(week: DropWeek) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(week.start, time.min), tz)
    end = timezone.make_aware(datetime.combine(week.end + timedelta(days=1), time.min), tz)
    return start, end


def current_week() -> DropWeek:
    return get_current_drop_week(today=timezone.localdate())


def _inventory_value_expression():
    return ExpressionWrapper(
        Coalesce(F("matched_item__price_rub"), F("catalog_price_rub_snapshot"), Decimal("0.00")) * F("amount"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def build_account_board(accounts, *, week: DropWeek | None = None) -> list[AccountBoardRow]:
    week = week or current_week()
    start, end = week_bounds(week)

    latest_sync_id = (
        FarmAccountInventorySync.objects.filter(account=OuterRef("pk")).order_by("-started_at", "-id").values("id")[:1]
    )
    accounts = list(
        accounts.annotate(
            board_drops_total=Count("drop_sessions", distinct=True),
            board_latest_sync_id=Subquery(latest_sync_id),
        )
    )
    if not accounts:
        return []

    account_ids = [account.id for account in accounts]

    week_sessions: dict[int, FarmDropSession] = {}
    for session in FarmDropSession.objects.filter(
        account_id__in=account_ids,
        collected_at__gte=start,
        collected_at__lt=end,
    ).order_by("collected_at", "id"):
        week_sessions.setdefault(session.account_id, session)

    last_drops = dict(
        FarmDropSession.objects.filter(account_id__in=account_ids)
        .values("account_id")
        .annotate(last=Max("collected_at"))
        .values_list("account_id", "last")
    )

    inventory = {
        row["account_id"]: row
        for row in FarmAccountInventoryItem.objects.filter(account_id__in=account_ids, is_missing=False)
        .values("account_id")
        .annotate(items=Sum("amount"), value=Sum(_inventory_value_expression()))
    }

    syncs = FarmAccountInventorySync.objects.in_bulk(
        [account.board_latest_sync_id for account in accounts if account.board_latest_sync_id]
    )

    rows: list[AccountBoardRow] = []
    for account in accounts:
        latest_sync = syncs.get(account.board_latest_sync_id)
        checks = tuple(build_connection_checks(account, latest_sync))
        inventory_row = inventory.get(account.id) or {}
        rows.append(
            AccountBoardRow(
                account=account,
                week_session=week_sessions.get(account.id),
                drops_total=account.board_drops_total,
                last_drop_at=last_drops.get(account.id),
                inventory_items=int(inventory_row.get("items") or 0),
                inventory_value_rub=q_money(inventory_row.get("value") or ZERO),
                latest_sync=latest_sync,
                checks=checks,
                connection=connection_state(list(checks)),
            )
        )
    return rows


def build_week_progress(rows: list[AccountBoardRow], *, week: DropWeek | None = None) -> WeekProgress:
    week = week or current_week()
    farming = [row for row in rows if row.is_farming]
    done = sum(1 for row in farming if row.has_week_drop)
    target = len(farming)
    _, next_reset = week_bounds(week)

    history: list[WeekBar] = []
    account_ids = [row.account.id for row in rows]
    first_index = max(week.index - WEEK_HISTORY_LENGTH + 1, 1)
    history_start, _ = week_bounds(get_drop_week_by_index(first_index))
    per_week: dict[int, set[int]] = {}
    if account_ids:
        for session in FarmDropSession.objects.filter(account_id__in=account_ids, collected_at__gte=history_start).values(
            "account_id", "collected_at"
        ):
            local_date = timezone.localtime(session["collected_at"]).date()
            delta_days = (local_date - get_drop_week_by_index(1).start).days
            index = delta_days // 7 + 1
            per_week.setdefault(index, set()).add(session["account_id"])
    scale = max([target] + [len(ids) for ids in per_week.values()] + [1])
    for index in range(first_index, week.index + 1):
        count = len(per_week.get(index, ()))
        history.append(
            WeekBar(
                week=get_drop_week_by_index(index),
                accounts_with_drop=count,
                percent=round(count * 100 / scale),
                is_current=index == week.index,
            )
        )

    return WeekProgress(
        week=week,
        done=done,
        target=target,
        percent=round(done * 100 / target) if target else 0,
        next_reset_at=next_reset,
        pending=tuple(row for row in farming if not row.has_week_drop),
        history=tuple(history),
    )


def summarize_board(rows: list[AccountBoardRow]) -> dict[str, object]:
    return {
        "accounts_total": len(rows),
        "accounts_active": sum(1 for row in rows if row.account.status == FarmAccount.Status.ACTIVE),
        "accounts_attention": sum(1 for row in rows if row.connection == "fail"),
        "storage_accounts": [row.account for row in rows if row.account.role == FarmAccount.Role.STORAGE],
        "inventory_items": sum(row.inventory_items for row in rows),
        "inventory_value_rub": q_money(sum((row.inventory_value_rub for row in rows), ZERO)),
    }


def build_recent_activity(*, user, include_investments: bool, limit: int = 8) -> list[ActivityEntry]:
    from django.urls import reverse

    entries: list[ActivityEntry] = []
    sessions = (
        FarmDropSession.objects.filter(owner=user)
        .select_related("account")
        .annotate(items_qty=Coalesce(Sum("items__quantity"), 0))
        .order_by("-collected_at", "-id")[:limit]
    )
    for session in sessions:
        entries.append(
            ActivityEntry(
                kind="drop",
                at=session.collected_at,
                title=session.title or f"Дроп · {SOURCE_LABELS_RU.get(session.source_type, session.source_type)}",
                subtitle=f"{session.account.title} · {session.items_qty} шт.",
                amount_rub=None,
                url=reverse("farm:sessions_detail", args=[session.id]),
            )
        )

    for sale in FarmDropSale.objects.filter(owner=user).select_related("item").order_by("-sold_at", "-id")[:limit]:
        entries.append(
            ActivityEntry(
                kind="farm_sale",
                at=sale.sold_at,
                title=sale.item.name_snapshot or sale.item.market_hash_name_snapshot,
                subtitle=f"Продажа · {sale.get_platform_display()} · {sale.quantity} шт.",
                amount_rub=sale.net_sale_rub,
                url=reverse("farm:sessions_detail", args=[sale.session_id]),
            )
        )

    if include_investments:
        from modules.v2.steam_trading.models import SteamPurchase, SteamPurchaseSale

        for purchase in SteamPurchase.objects.filter(created_by=user).select_related("item").order_by("-purchased_at", "-id")[:limit]:
            entries.append(
                ActivityEntry(
                    kind="purchase",
                    at=purchase.purchased_at,
                    title=purchase.item.group_name,
                    subtitle=f"Закупка · {purchase.quantity} шт.",
                    amount_rub=-purchase.total_spent_rub,
                    url=reverse("steam_trading:sell", args=[purchase.id]),
                )
            )
        for sale in (
            SteamPurchaseSale.objects.filter(purchase__created_by=user)
            .select_related("purchase__item")
            .order_by("-sold_at", "-id")[:limit]
        ):
            entries.append(
                ActivityEntry(
                    kind="purchase_sale",
                    at=sale.sold_at,
                    title=sale.purchase.item.group_name,
                    subtitle=f"Продажа закупки · {sale.quantity} шт.",
                    amount_rub=sale.total_sale_rub,
                    url=reverse("steam_trading:sell", args=[sale.purchase_id]),
                )
            )

    entries.sort(key=lambda entry: entry.at, reverse=True)
    return entries[:limit]


def frequent_drop_items(*, user, limit: int = 8):
    """Catalog items this user logs most often, for one-click drop entry."""
    from modules.v2.steam_catalog.models import SteamItem

    rows = (
        FarmDropItem.objects.filter(owner=user, steam_item__isnull=False, steam_item__is_active=True)
        .values("steam_item_id")
        .annotate(times=Count("id"))
        .order_by("-times")[:limit]
    )
    ids = [row["steam_item_id"] for row in rows]
    items = SteamItem.objects.in_bulk(ids)
    return [items[item_id] for item_id in ids if item_id in items]


def accounts_for_user(user, *, staff: bool = False):
    qs = FarmAccount.objects.select_related("owner")
    if not staff:
        qs = qs.filter(owner=user)
    return qs.order_by("sort_order", "title", "id")


def drop_week_account_ids(user, *, week: DropWeek | None = None) -> set[int]:
    week = week or current_week()
    start, end = week_bounds(week)
    return set(
        FarmDropSession.objects.filter(owner=user, collected_at__gte=start, collected_at__lt=end).values_list(
            "account_id", flat=True
        )
    )
