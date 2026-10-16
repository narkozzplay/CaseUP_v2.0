from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, IntegerField, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from modules.v2.steam_catalog.services import _telegram_send_message

from .models import SteamPurchase, SteamPurchaseSale


ZERO = Decimal("0.00")
STEAM_COMMISSION_RATE = Decimal("0.15")
STEAM_NET_RATE = Decimal("1.00") - STEAM_COMMISSION_RATE


class SteamTradingSaleError(ValidationError):
    pass


@dataclass(frozen=True)
class PurchaseSummary:
    total_spent_sum: Decimal
    current_total_sum: Decimal
    total_profit_sum: Decimal
    total_profit_percent: Decimal
    realized_profit_sum: Decimal
    sold_quantity_sum: int
    remaining_quantity_sum: int


@dataclass(frozen=True)
class SalePreview:
    quantity: int
    sale_price_rub: Decimal
    total_sale_rub: Decimal
    cost_basis_rub: Decimal
    profit_rub: Decimal
    profit_percent: Decimal
    result_label: str


@dataclass(frozen=True)
class PurchaseRow:
    purchase: SteamPurchase
    current_price_rub: Decimal
    current_total_rub: Decimal
    current_total_after_fee_rub: Decimal
    profit_rub: Decimal
    profit_percent: Decimal
    profit_after_fee_rub: Decimal
    unlock_status_label: str
    sold_quantity_total: int
    remaining_quantity: int
    realized_profit_rub: Decimal
    sale_status_label: str
    can_sell: bool


@dataclass(frozen=True)
class UnlockDispatchSummary:
    processed_count: int
    sent_count: int
    error_count: int
    limit: int


def quantize_money(value) -> Decimal:
    if value in (None, ""):
        return ZERO
    if isinstance(value, Decimal):
        decimal_value = value
    else:
        decimal_value = Decimal(str(value))
    return decimal_value.quantize(Decimal("0.01"))


def calculate_steam_net_amount(value: Decimal) -> Decimal:
    return quantize_money((value or ZERO) * STEAM_NET_RATE)


def preview_purchase_sale(*, purchase: SteamPurchase, quantity: int, sale_price_rub: Decimal) -> SalePreview:
    quantity = int(quantity or 0)
    sale_price_rub = quantize_money(sale_price_rub)
    total_sale_rub = quantize_money(Decimal(quantity) * sale_price_rub)
    cost_basis_rub = quantize_money(Decimal(quantity) * (purchase.purchase_price_rub or ZERO))
    profit_rub = quantize_money(total_sale_rub - cost_basis_rub)
    if cost_basis_rub == ZERO:
        profit_percent = ZERO
    else:
        profit_percent = quantize_money((profit_rub / cost_basis_rub) * Decimal("100"))

    if profit_rub > ZERO:
        result_label = "Сделка в плюс"
    elif profit_rub < ZERO:
        result_label = "Сделка в минус"
    else:
        result_label = "Сделка в ноль"

    return SalePreview(
        quantity=quantity,
        sale_price_rub=sale_price_rub,
        total_sale_rub=total_sale_rub,
        cost_basis_rub=cost_basis_rub,
        profit_rub=profit_rub,
        profit_percent=profit_percent,
        result_label=result_label,
    )


def with_purchase_sales_annotations(queryset):
    return queryset.annotate(
        sold_quantity_total_annotated=Coalesce(
            Sum("sales__quantity"),
            Value(0),
            output_field=IntegerField(),
        ),
        realized_profit_rub_annotated=Coalesce(
            Sum("sales__profit_rub"),
            Value(ZERO),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )


def build_purchase_row(purchase: SteamPurchase) -> PurchaseRow:
    current_price = quantize_money(purchase.current_price_rub)
    current_total = quantize_money(purchase.current_total_rub)
    current_total_after_fee = calculate_steam_net_amount(current_total)
    profit = quantize_money(purchase.profit_rub)
    profit_percent = quantize_money(purchase.profit_percent)
    profit_after_fee = quantize_money(current_total_after_fee - purchase.total_spent_rub)
    sold_quantity_total = int(purchase.sold_quantity_total)
    remaining_quantity = int(purchase.remaining_quantity)
    realized_profit_rub = quantize_money(purchase.realized_profit_rub)
    unlock_status_label = "Готово к продаже" if purchase.is_unlocked else "В блокировке"
    can_sell = purchase.is_unlocked and remaining_quantity > 0
    return PurchaseRow(
        purchase=purchase,
        current_price_rub=current_price,
        current_total_rub=current_total,
        current_total_after_fee_rub=current_total_after_fee,
        profit_rub=profit,
        profit_percent=profit_percent,
        profit_after_fee_rub=profit_after_fee,
        unlock_status_label=unlock_status_label,
        sold_quantity_total=sold_quantity_total,
        remaining_quantity=remaining_quantity,
        realized_profit_rub=realized_profit_rub,
        sale_status_label=purchase.sale_status_label,
        can_sell=can_sell,
    )



def build_purchase_rows(purchases) -> list[PurchaseRow]:
    return [build_purchase_row(purchase) for purchase in purchases]



def build_purchase_summary(rows: list[PurchaseRow]) -> PurchaseSummary:
    total_spent = quantize_money(sum((row.purchase.total_spent_rub for row in rows), ZERO))
    current_total = quantize_money(sum((row.current_total_rub for row in rows), ZERO))
    total_profit = quantize_money(current_total - total_spent)
    realized_profit_sum = quantize_money(sum((row.realized_profit_rub for row in rows), ZERO))
    sold_quantity_sum = sum(row.sold_quantity_total for row in rows)
    remaining_quantity_sum = sum(row.remaining_quantity for row in rows)

    if total_spent == ZERO:
        total_profit_percent = ZERO
    else:
        total_profit_percent = quantize_money((total_profit / total_spent) * Decimal("100"))

    return PurchaseSummary(
        total_spent_sum=total_spent,
        current_total_sum=current_total,
        total_profit_sum=total_profit,
        total_profit_percent=total_profit_percent,
        realized_profit_sum=realized_profit_sum,
        sold_quantity_sum=sold_quantity_sum,
        remaining_quantity_sum=remaining_quantity_sum,
    )


@transaction.atomic
def create_purchase_sale(*, purchase: SteamPurchase, sold_at, quantity: int, sale_price_rub: Decimal, created_by=None) -> SteamPurchaseSale:
    locked_purchase = SteamPurchase.objects.select_for_update().get(pk=purchase.pk)

    if not locked_purchase.is_unlocked:
        raise SteamTradingSaleError("Нельзя продавать закупку, пока она в трейд-бане.")

    quantity = int(quantity or 0)
    if quantity <= 0:
        raise SteamTradingSaleError({"quantity": "Количество продажи должно быть больше нуля."})

    sale_price_rub = quantize_money(sale_price_rub)
    if sale_price_rub < ZERO:
        raise SteamTradingSaleError({"sale_price_rub": "Цена продажи не может быть отрицательной."})

    sold_total = int(
        SteamPurchaseSale.objects.select_for_update()
        .filter(purchase=locked_purchase)
        .aggregate(total=Sum("quantity"))
        .get("total")
        or 0
    )
    remaining_quantity = max(int(locked_purchase.quantity or 0) - sold_total, 0)
    if quantity > remaining_quantity:
        raise SteamTradingSaleError({"quantity": f"Нельзя продать больше остатка. Доступно: {remaining_quantity}."})

    sale = SteamPurchaseSale(
        purchase=locked_purchase,
        sold_at=sold_at,
        quantity=quantity,
        sale_price_rub=sale_price_rub,
        created_by=created_by,
    )
    sale.save()
    return sale


def get_due_unlock_notification_purchases(*, now=None, limit: int = 20):
    now = now or timezone.now()
    return list(
        SteamPurchase.objects
        .select_related("item", "created_by")
        .filter(unlocked_at__isnull=False, unlocked_at__lte=now, unlock_notified_at__isnull=True)
        .order_by("unlocked_at", "id")[:limit]
    )


def _format_money(value: Decimal) -> str:
    return f"{quantize_money(value):.2f} ₽"


def _format_signed_money(value: Decimal) -> str:
    value = quantize_money(value)
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f} ₽"


def _format_signed_percent(value: Decimal) -> str:
    value = quantize_money(value)
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f}%"


def _build_purchase_absolute_url(purchase: SteamPurchase) -> str:
    from modules.v2.system.services import get_public_base_url

    return f"{get_public_base_url()}{reverse('steam_catalog:detail', args=[purchase.item_id])}"


def build_unlock_notification_message(purchase: SteamPurchase) -> str:
    row = build_purchase_row(purchase)
    purchased_label = timezone.localtime(purchase.purchased_at).strftime("%d.%m.%Y %H:%M")
    unlocked_label = timezone.localtime(purchase.unlocked_at).strftime("%d.%m.%Y %H:%M") if purchase.unlocked_at else "—"
    item_name = html.escape(purchase.item.group_name)
    item_url = html.escape(_build_purchase_absolute_url(purchase))

    return (
        "🔓 <b>Предметы сняты с трейд-бана</b>\n\n"
        f"<b>Предмет:</b> <a href=\"{item_url}\">{item_name}</a>\n"
        f"<b>Дата покупки:</b> {purchased_label} МСК\n"
        f"<b>Дата разблокировки:</b> {unlocked_label} МСК\n\n"
        f"<b>Количество:</b> {purchase.quantity}\n"
        f"<b>Цена покупки:</b> {_format_money(purchase.purchase_price_rub)}\n"
        f"<b>Затрачено:</b> {_format_money(purchase.total_spent_rub)}\n\n"
        f"<b>Текущая цена:</b> {_format_money(row.current_price_rub)}\n"
        f"<b>Текущая стоимость:</b> {_format_money(row.current_total_rub)}\n"
        f"<b>Стоимость после комиссии Steam (15%):</b> {_format_money(row.current_total_after_fee_rub)}\n\n"
        f"<b>Прибыль:</b> {_format_signed_money(row.profit_rub)} ({_format_signed_percent(row.profit_percent)})\n"
        f"<b>Прибыль после комиссии:</b> {_format_signed_money(row.profit_after_fee_rub)}\n\n"
        "<b>Статус:</b> готовы к продаже"
    )


def _telegram_enabled() -> bool:
    from modules.v2.tg_notifier.services import is_enabled

    try:
        return is_enabled()
    except Exception:  # noqa: BLE001
        return False


def _notify_unlock_on_site(purchase: SteamPurchase) -> bool:
    """The owner of the purchase learns on the site (and by push) that the items can be sold."""
    if not purchase.created_by_id:
        return False
    from modules.v2.notifications.services import notify
    from modules.v2.steam_trading.notifications import KIND_INVESTMENT_UNLOCKED

    row = build_purchase_row(purchase)
    profit = f", прибыль после комиссии {_format_signed_money(row.profit_after_fee_rub)}" if row.profit_after_fee_rub is not None else ""
    notify(
        purchase.created_by,
        KIND_INVESTMENT_UNLOCKED,
        object_id=purchase.pk,
        payload={
            "title": f"🔓 Трейд-бан снят: {purchase.item.group_name}",
            "body": f"{purchase.quantity} шт. готовы к продаже{profit}.",
            "url": reverse("steam_trading:investments"),
        },
    )
    return True


def dispatch_due_unlock_notifications(*, limit: int = 20, now=None) -> UnlockDispatchSummary:
    now = now or timezone.now()

    due_purchase_ids = list(
        SteamPurchase.objects
        .filter(
            unlocked_at__isnull=False,
            unlocked_at__lte=now,
            unlock_notified_at__isnull=True,
        )
        .order_by("unlocked_at", "id")
        .values_list("id", flat=True)[:limit]
    )

    processed_count = 0
    sent_count = 0
    error_count = 0

    for purchase_id in due_purchase_ids:
        with transaction.atomic():
            purchase = (
                SteamPurchase.objects
                .select_related("item")
                .select_for_update(skip_locked=True)
                .filter(
                    pk=purchase_id,
                    unlocked_at__isnull=False,
                    unlocked_at__lte=now,
                    unlock_notified_at__isnull=True,
                )
                .first()
            )
            if purchase is None:
                continue

            processed_count += 1

            on_site = _notify_unlock_on_site(purchase)
            telegram_error = ""
            if _telegram_enabled():
                try:
                    _telegram_send_message(
                        text=build_unlock_notification_message(purchase),
                        parse_mode="HTML",
                    )
                except Exception as exc:  # noqa: BLE001 - recorded on the purchase
                    telegram_error = str(exc)[:4000]

            if not on_site and telegram_error:
                # Nobody was told: keep the purchase in the queue for the next run.
                updated = (
                    SteamPurchase.objects
                    .filter(pk=purchase.pk, unlock_notified_at__isnull=True)
                    .update(unlock_notification_error=telegram_error, updated_at=timezone.now())
                )
                if updated:
                    error_count += 1
                continue

            updated = (
                SteamPurchase.objects
                .filter(pk=purchase.pk, unlock_notified_at__isnull=True)
                .update(
                    unlock_notified_at=timezone.now(),
                    unlock_notification_error=telegram_error,
                    updated_at=timezone.now(),
                )
            )
            if updated:
                sent_count += 1
                if telegram_error:
                    error_count += 1

    return UnlockDispatchSummary(
        processed_count=processed_count,
        sent_count=sent_count,
        error_count=error_count,
        limit=limit,
    )
