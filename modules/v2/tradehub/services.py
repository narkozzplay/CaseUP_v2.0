from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from modules.v2.farm.models import FarmAccount, FarmAccountInventoryItem
from modules.v2.farm.services import _build_sellability
from modules.v2.profiles.models import LedgerReason
from modules.v2.profiles.services.ledger import apply_ledger_delta
from modules.v2.steam_catalog.models import StockMovement, StockReason

from .models import SaleOrder, SaleOrderItem, TradeBotAccount, TradeEventLog, TradeOffer, TradeUserPenalty


class TradeHubError(ValidationError):
    pass


@dataclass(frozen=True)
class SaleStats:
    orders_count: int
    items_count: int
    total_payout_rub: Decimal


TERMINAL_ORDER_STATUSES = {
    SaleOrder.Status.COMPLETED,
    SaleOrder.Status.CANCELLED_BY_USER,
    SaleOrder.Status.CANCELLED_BY_SYSTEM,
    SaleOrder.Status.FAILED,
}


def _normalize_ids(item_ids) -> list[int]:
    values: list[int] = []
    for raw in item_ids:
        try:
            values.append(int(raw))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(values))


def log_trade_event(*, order: SaleOrder, event: str, message: str = "", payload: dict | None = None, offer: TradeOffer | None = None, created_by=None) -> TradeEventLog:
    return TradeEventLog.objects.create(
        order=order,
        offer=offer,
        event=event,
        message=message,
        payload=payload or {},
        created_by=created_by,
    )


def get_active_penalty(user) -> TradeUserPenalty | None:
    return TradeUserPenalty.objects.filter(user=user, type=TradeUserPenalty.Type.BAN, is_active=True).first()


def select_trade_bot() -> TradeBotAccount:
    bot = TradeBotAccount.objects.filter(
        status=TradeBotAccount.Status.ACTIVE,
        is_enabled=True,
    ).order_by("-is_default", "sort_order", "id").first()
    if bot is None:
        raise TradeHubError("Нет активного трейд-бота для покупки предметов.")
    return bot


def _validate_inventory_item(item: FarmAccountInventoryItem, *, expected_owner_id: int, expected_account_id: int):
    if item.account.owner_id != expected_owner_id:
        raise TradeHubError("Нельзя продавать предметы другого пользователя.")
    if item.account_id != expected_account_id:
        raise TradeHubError("Все выбранные предметы должны принадлежать одному farm-аккаунту.")
    if item.is_missing:
        raise TradeHubError(f"Предмет {item.market_hash_name} отсутствует в последнем inventory snapshot.")
    if item.sale_state != FarmAccountInventoryItem.SaleState.AVAILABLE:
        raise TradeHubError(f"Предмет {item.market_hash_name} уже участвует в продаже или продан.")
    if item.matched_item_id is None:
        raise TradeHubError(f"Предмет {item.market_hash_name} не найден в Steam Catalog.")
    if not item.tradable:
        raise TradeHubError(f"Предмет {item.market_hash_name} нетрейдабелен.")

    payout_snapshot = item.payout_price_rub_snapshot or Decimal("0.00")
    if payout_snapshot <= Decimal("0.00"):
        raise TradeHubError(f"Для предмета {item.market_hash_name} не рассчитан payout.")

    matched_item = item.matched_item
    is_sellable, block_reason, catalog_price, payout_price = _build_sellability(
        matched_item=matched_item,
        tradable=item.tradable,
    )
    if not is_sellable:
        raise TradeHubError(block_reason or f"Предмет {item.market_hash_name} нельзя продать.")

    return catalog_price or Decimal("0.00"), payout_price or Decimal("0.00")


@transaction.atomic
def create_sale_order(*, seller, account: FarmAccount, inventory_item_ids, created_by=None) -> SaleOrder:
    if get_active_penalty(seller):
        raise TradeHubError("Пользователь заблокирован в tradehub и не может создавать новые продажи.")

    item_ids = _normalize_ids(inventory_item_ids)
    if not item_ids:
        raise TradeHubError("Нужно выбрать хотя бы один предмет для продажи.")

    account = FarmAccount.objects.select_for_update().select_related("owner").get(pk=account.pk)
    if account.owner_id != seller.id:
        raise TradeHubError("Можно продавать предметы только со своего farm-аккаунта.")

    items = list(
        FarmAccountInventoryItem.objects.select_for_update()
        .select_related("account")
        .filter(account=account, pk__in=item_ids)
        .order_by("id")
    )
    if len(items) != len(item_ids):
        raise TradeHubError("Часть выбранных предметов не найдена.")

    total_catalog = Decimal("0.00")
    total_payout = Decimal("0.00")
    validated_items: list[tuple[FarmAccountInventoryItem, Decimal, Decimal]] = []

    for item in items:
        catalog_price, payout_price = _validate_inventory_item(
            item,
            expected_owner_id=seller.id,
            expected_account_id=account.id,
        )
        total_catalog += catalog_price
        total_payout += payout_price
        validated_items.append((item, catalog_price, payout_price))

    bot = select_trade_bot()
    now = timezone.now()

    order = SaleOrder.objects.create(
        seller=seller,
        farm_account=account,
        bot_account=bot,
        status=SaleOrder.Status.OFFER_PENDING,
        items_count=len(validated_items),
        total_catalog_price_rub=total_catalog,
        total_payout_rub=total_payout,
    )
    offer = TradeOffer.objects.create(order=order, bot_account=bot, status=TradeOffer.Status.NEW)

    order_items: list[SaleOrderItem] = []
    for item, catalog_price, payout_price in validated_items:
        item.sale_state = FarmAccountInventoryItem.SaleState.RESERVED
        item.sale_locked_at = now
        item.save(update_fields=["sale_state", "sale_locked_at", "last_seen_at"])
        order_items.append(
            SaleOrderItem(
                order=order,
                inventory_item=item,
                steam_item=item.matched_item,
                asset_id_snapshot=item.asset_id,
                class_id_snapshot=item.class_id,
                instance_id_snapshot=item.instance_id,
                market_hash_name_snapshot=item.market_hash_name,
                icon_url_snapshot=item.icon_url,
                amount_snapshot=item.amount,
                catalog_price_rub_snapshot=catalog_price,
                payout_price_rub_snapshot=payout_price,
            )
        )
    SaleOrderItem.objects.bulk_create(order_items)

    bot.last_offer_at = now
    bot.save(update_fields=["last_offer_at", "updated_at"])

    log_trade_event(order=order, offer=offer, event="sale_created", message="Создан новый ордер продажи.", payload={"items_count": len(validated_items)}, created_by=created_by)
    log_trade_event(order=order, offer=offer, event="bot_assigned", message=f"Назначен бот {bot.title}.", payload={"bot_id": bot.id}, created_by=created_by)
    return order


@transaction.atomic
def mark_offer_sent(*, order: SaleOrder, external_offer_id: str, payload: dict | None = None, created_by=None) -> TradeOffer:
    order = SaleOrder.objects.select_for_update().get(pk=order.pk)
    offer = TradeOffer.objects.select_for_update().get(order=order)

    if order.status in TERMINAL_ORDER_STATUSES:
        raise TradeHubError("Нельзя отправить оффер для завершённой или отменённой продажи.")
    if offer.status in {TradeOffer.Status.ACCEPTED, TradeOffer.Status.CANCELLED, TradeOffer.Status.FAILED}:
        raise TradeHubError("Статус оффера не позволяет повторно отметить его как отправленный.")

    offer.status = TradeOffer.Status.SENT
    offer.external_offer_id = (external_offer_id or "").strip()
    offer.response_payload = payload or {}
    offer.last_checked_at = timezone.now()
    offer.save(update_fields=["status", "external_offer_id", "response_payload", "last_checked_at", "updated_at"])

    order.status = SaleOrder.Status.OFFER_SENT
    order.save(update_fields=["status", "updated_at"])
    log_trade_event(order=order, offer=offer, event="offer_sent", message="Трейд-оффер отправлен.", payload={"external_offer_id": offer.external_offer_id}, created_by=created_by)
    return offer


@transaction.atomic
def mark_offer_accepted(*, order: SaleOrder, payload: dict | None = None, created_by=None) -> TradeOffer:
    order = SaleOrder.objects.select_for_update().get(pk=order.pk)
    offer = TradeOffer.objects.select_for_update().get(order=order)

    if order.status in TERMINAL_ORDER_STATUSES:
        raise TradeHubError("Нельзя подтвердить оффер для завершённой или отменённой продажи.")
    if offer.status in {TradeOffer.Status.CANCELLED, TradeOffer.Status.FAILED}:
        raise TradeHubError("Оффер уже закрыт и не может быть отмечен как принятый.")

    if offer.status == TradeOffer.Status.ACCEPTED and order.status in {
        SaleOrder.Status.OFFER_ACCEPTED,
        SaleOrder.Status.PAYOUT_PENDING,
    }:
        return offer

    offer.status = TradeOffer.Status.ACCEPTED
    offer.response_payload = payload or offer.response_payload
    offer.last_checked_at = timezone.now()
    offer.closed_at = timezone.now()
    offer.save(update_fields=["status", "response_payload", "last_checked_at", "closed_at", "updated_at"])

    order.status = SaleOrder.Status.PAYOUT_PENDING
    order.save(update_fields=["status", "updated_at"])
    log_trade_event(order=order, offer=offer, event="offer_accepted", message="Steam подтвердил принятие оффера.", created_by=created_by)
    return offer


def get_or_create_trade_stock_reason() -> StockReason:
    reason, _ = StockReason.objects.get_or_create(
        name="farm_purchase",
        defaults={
            "direction": StockReason.Direction.IN,
            "is_active": True,
            "sort_order": 30,
        },
    )
    if reason.direction != StockReason.Direction.IN:
        reason.direction = StockReason.Direction.IN
        reason.is_active = True
        reason.save(update_fields=["direction", "is_active"])
    return reason


@transaction.atomic
def finalize_sale_order(*, order: SaleOrder, created_by=None) -> SaleOrder:
    order = SaleOrder.objects.select_for_update().select_related("seller", "farm_account").get(pk=order.pk)
    if order.status == SaleOrder.Status.COMPLETED and order.payout_paid:
        return order

    offer = TradeOffer.objects.select_for_update().get(order=order)
    if offer.status != TradeOffer.Status.ACCEPTED:
        raise TradeHubError("Нельзя завершить продажу, пока оффер не принят.")

    order_items = list(
        SaleOrderItem.objects.select_related("inventory_item", "steam_item").filter(order=order).order_by("id")
    )
    if not order_items:
        raise TradeHubError("У продажи нет предметов.")

    stock_reason = get_or_create_trade_stock_reason()
    ledger_key = f"tradehub-sale-complete-{order.id}"
    apply_ledger_delta(
        user=order.seller,
        delta=order.total_payout_rub,
        reason=LedgerReason.ITEM_SALE,
        amount_snapshot=order.total_payout_rub,
        idempotency_key=ledger_key,
    )

    now = timezone.now()
    for order_item in order_items:
        inventory_item = FarmAccountInventoryItem.objects.select_for_update().get(pk=order_item.inventory_item_id)
        inventory_item.sale_state = FarmAccountInventoryItem.SaleState.SOLD
        inventory_item.sale_locked_at = None
        inventory_item.sold_at = now
        inventory_item.is_missing = True
        inventory_item.is_sellable = False
        inventory_item.sell_block_reason = "Предмет продан через tradehub."
        inventory_item.save(
            update_fields=[
                "sale_state",
                "sale_locked_at",
                "sold_at",
                "is_missing",
                "is_sellable",
                "sell_block_reason",
                "last_seen_at",
            ]
        )

        if order_item.steam_item_id:
            StockMovement.objects.create(
                item=order_item.steam_item,
                delta=order_item.amount_snapshot,
                reason=stock_reason,
                note=f"TradeHub purchase from order #{order.id}",
                created_by=created_by,
            )

    order.status = SaleOrder.Status.COMPLETED
    order.payout_paid = True
    order.payout_paid_at = now
    order.completed_at = now
    order.failure_reason = ""
    order.save(update_fields=["status", "payout_paid", "payout_paid_at", "completed_at", "failure_reason", "updated_at"])
    log_trade_event(order=order, offer=offer, event="sale_completed", message="Продажа завершена, выплата начислена.", created_by=created_by)
    return order


@transaction.atomic
def cancel_sale_order(*, order: SaleOrder, reason: str, cancelled_by_user: bool = True, created_by=None) -> SaleOrder:
    order = SaleOrder.objects.select_for_update().select_related("seller").get(pk=order.pk)
    if order.status == SaleOrder.Status.COMPLETED:
        raise TradeHubError("Нельзя отменить уже завершённую продажу.")
    if order.status in {SaleOrder.Status.CANCELLED_BY_USER, SaleOrder.Status.CANCELLED_BY_SYSTEM}:
        raise TradeHubError("Продажа уже отменена.")
    if order.status == SaleOrder.Status.FAILED:
        raise TradeHubError("Нельзя отменить продажу в статусе failed.")

    offer = TradeOffer.objects.select_for_update().get(order=order)
    now = timezone.now()
    offer.status = TradeOffer.Status.CANCELLED
    offer.closed_at = now
    offer.last_checked_at = now
    offer.save(update_fields=["status", "closed_at", "last_checked_at", "updated_at"])

    order.status = SaleOrder.Status.CANCELLED_BY_USER if cancelled_by_user else SaleOrder.Status.CANCELLED_BY_SYSTEM
    order.cancelled_reason = (reason or "").strip() or "Оффер отменён."
    order.cancelled_at = now
    order.save(update_fields=["status", "cancelled_reason", "cancelled_at", "updated_at"])

    for order_item in SaleOrderItem.objects.select_related("inventory_item").filter(order=order):
        inventory_item = FarmAccountInventoryItem.objects.select_for_update().get(pk=order_item.inventory_item_id)
        if inventory_item.sale_state == FarmAccountInventoryItem.SaleState.RESERVED:
            inventory_item.sale_state = FarmAccountInventoryItem.SaleState.AVAILABLE
            inventory_item.sale_locked_at = None
            if not inventory_item.is_missing:
                is_sellable, block_reason, catalog_price, payout_price = _build_sellability(
                    matched_item=inventory_item.matched_item,
                    tradable=inventory_item.tradable,
                )
                inventory_item.is_sellable = is_sellable
                inventory_item.sell_block_reason = block_reason
                inventory_item.catalog_price_rub_snapshot = catalog_price
                inventory_item.payout_price_rub_snapshot = payout_price
            inventory_item.save(
                update_fields=[
                    "sale_state",
                    "sale_locked_at",
                    "is_sellable",
                    "sell_block_reason",
                    "catalog_price_rub_snapshot",
                    "payout_price_rub_snapshot",
                    "last_seen_at",
                ]
            )

    penalty = None
    if cancelled_by_user:
        warnings_count = TradeUserPenalty.objects.filter(
            user=order.seller,
            type=TradeUserPenalty.Type.WARNING,
        ).count()
        if warnings_count >= 1:
            penalty = TradeUserPenalty.objects.create(
                user=order.seller,
                type=TradeUserPenalty.Type.BAN,
                order=order,
                offer=offer,
                reason=(reason or "").strip() or "Повторная отмена трейд-оффера пользователем.",
                is_active=True,
                created_by=created_by,
            )
        else:
            penalty = TradeUserPenalty.objects.create(
                user=order.seller,
                type=TradeUserPenalty.Type.WARNING,
                order=order,
                offer=offer,
                reason=(reason or "").strip() or "Отмена трейд-оффера пользователем.",
                is_active=True,
                created_by=created_by,
            )

    log_trade_event(
        order=order,
        offer=offer,
        event="offer_cancelled",
        message=order.cancelled_reason,
        payload={"cancelled_by_user": cancelled_by_user, "penalty_id": penalty.id if penalty else None},
        created_by=created_by,
    )
    return order


def _stats_qs(*, user=None):
    qs = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED)
    if user is not None:
        qs = qs.filter(seller=user)
    return qs


def _aggregate_sales(qs) -> SaleStats:
    data = qs.aggregate(
        orders_count=Count("id"),
        items_count=Sum("items_count"),
        total_payout_rub=Sum("total_payout_rub"),
    )
    return SaleStats(
        orders_count=data["orders_count"] or 0,
        items_count=data["items_count"] or 0,
        total_payout_rub=data["total_payout_rub"] or Decimal("0.00"),
    )


def get_sales_stats(*, user=None) -> dict[str, SaleStats]:
    now = timezone.now()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=31)
    year_start = now - timedelta(days=366)

    base = _stats_qs(user=user)
    return {
        "week": _aggregate_sales(base.filter(completed_at__gte=week_start)),
        "month": _aggregate_sales(base.filter(completed_at__gte=month_start)),
        "year": _aggregate_sales(base.filter(completed_at__gte=year_start)),
        "all": _aggregate_sales(base),
    }


def get_tradehub_dashboard_context(*, user=None) -> dict:
    stats = get_sales_stats(user=user)
    recent_orders = _stats_qs(user=user).select_related("seller", "farm_account", "bot_account")[:10]
    active_orders = SaleOrder.objects.filter(
        ~Q(status=SaleOrder.Status.COMPLETED),
        ~Q(status=SaleOrder.Status.CANCELLED_BY_USER),
        ~Q(status=SaleOrder.Status.CANCELLED_BY_SYSTEM),
        ~Q(status=SaleOrder.Status.FAILED),
    )
    if user is not None:
        active_orders = active_orders.filter(seller=user)
    return {
        "stats": stats,
        "recent_orders": recent_orders,
        "active_orders_total": active_orders.count(),
        "bots_total": TradeBotAccount.objects.filter(is_enabled=True).count(),
        "penalties_total": TradeUserPenalty.objects.filter(is_active=True).count() if user is None else TradeUserPenalty.objects.filter(user=user, is_active=True).count(),
    }
