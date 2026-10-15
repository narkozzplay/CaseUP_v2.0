"""Writing platform income into the revenue journal.

Live path: a post_save hook on LedgerEntry (store, cases), an explicit call from the subscription purchase
(it alone knows the switch fee) and from the tradehub order completion (catalog price minus payout).
``sync()`` re-reads the ledger and completed tradehub orders and adds whatever is missing; it backs the
history up on install and runs hourly, so a lost hook never loses income.

``sync()`` takes the models as arguments so the data migration can call it with historical models.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from modules.v2.profiles.models import LedgerReason

from .models import SOURCE_CATEGORY, RevenueSource

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
STORE_REASONS = (LedgerReason.STORE_PURCHASE, LedgerReason.THEME_PURCHASE, LedgerReason.BUNDLE_PURCHASE)
# Must match the note written by subscriptions.services.purchase_subscription_with_balance on upgrades.
SWITCH_FEE_NOTE = "RUB (includes switch fee)"


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


def rows_for_entry(entry, *, switch_fee=ZERO) -> list[dict]:
    """Revenue rows for one ledger entry (empty when the entry is not income)."""
    reason, delta = entry.reason, _money(entry.delta)
    base = {"user_id": entry.user_id, "ref": f"ledger:{entry.pk}", "ledger_entry_id": entry.pk, "occurred_at": entry.created_at}
    if reason in STORE_REASONS and delta < 0:
        return [{**base, "source": RevenueSource.STORE, "amount": -delta}]
    if reason == LedgerReason.CASE_OPEN and delta < 0:
        return [{**base, "source": RevenueSource.CASE_OPEN, "amount": -delta}]
    if reason == LedgerReason.ITEM_SALE and entry.case_opening_id is not None and delta > 0:
        return [{**base, "source": RevenueSource.CASE_BUYBACK, "amount": -delta}]
    if reason == LedgerReason.SUBSCRIPTION_PURCHASE and delta < 0:
        charge = -delta
        fee = min(max(_money(switch_fee), ZERO), charge)
        rows = []
        if charge - fee > 0:
            rows.append({**base, "source": RevenueSource.SUBSCRIPTION, "amount": charge - fee})
        if fee > 0:
            rows.append({**base, "source": RevenueSource.SWITCH_FEE, "amount": fee, "note": "Плата за смену тарифа"})
        return rows
    return []


def income_ledger_filter() -> Q:
    """Ledger entries that always produce at least one revenue row."""
    spending = (LedgerReason.CASE_OPEN, LedgerReason.SUBSCRIPTION_PURCHASE, *STORE_REASONS)
    return Q(reason__in=spending, delta__lt=0) | Q(reason=LedgerReason.ITEM_SALE, case_opening_id__isnull=False, delta__gt=0)


def _create(revenue_model, rows: list[dict]) -> int:
    if not rows:
        return 0
    objs = [revenue_model(category=SOURCE_CATEGORY[row["source"]], **row) for row in rows]
    return len(revenue_model.objects.bulk_create(objs, ignore_conflicts=True))


def _safely(func, *args, **kwargs) -> int:
    """Revenue bookkeeping never breaks the money operation it describes; sync() fills any gap."""
    try:
        with transaction.atomic():
            return func(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("Revenue journal write failed (%s)", func.__name__)
        return 0


def _record_ledger_entry(entry, switch_fee=ZERO) -> int:
    from .models import RevenueEntry

    return _create(RevenueEntry, rows_for_entry(entry, switch_fee=switch_fee))


def record_ledger_entry(entry, *, switch_fee=ZERO) -> int:
    return _safely(_record_ledger_entry, entry, switch_fee)


def _tradehub_row(order) -> dict | None:
    spread = _money(order.total_catalog_price_rub) - _money(order.total_payout_rub)
    if spread <= 0:
        return None
    return {
        "source": RevenueSource.TRADEHUB,
        "amount": spread,
        "user_id": order.seller_id,
        "ref": f"tradehub:{order.pk}",
        "occurred_at": order.completed_at or order.updated_at,
        "note": f"Заказ #{order.pk}: каталог {_money(order.total_catalog_price_rub)} ₽, выплата {_money(order.total_payout_rub)} ₽",
    }


def _record_tradehub_order(order) -> int:
    from .models import RevenueEntry

    row = _tradehub_row(order)
    return _create(RevenueEntry, [row] if row else [])


def record_tradehub_order(order) -> int:
    return _safely(_record_tradehub_order, order)


def _record_commission(*, source, amount, ref, user=None, occurred_at=None, note="") -> int:
    from django.utils import timezone

    from .models import RevenueCategory, RevenueEntry

    if SOURCE_CATEGORY[source] != RevenueCategory.COMMISSION:
        raise ValueError(f"{source} is not a commission source")
    row = {"source": source, "amount": _money(amount), "user_id": getattr(user, "pk", user), "ref": ref, "occurred_at": occurred_at or timezone.now(), "note": note}
    return _create(RevenueEntry, [row] if row["amount"] > 0 else [])


def record_commission(*, source, amount, ref, user=None, occurred_at=None, note="") -> int:
    """Generic entry point for commissions (the market will book its 10% fee here)."""
    return _safely(_record_commission, source=source, amount=amount, ref=ref, user=user, occurred_at=occurred_at, note=note)


def infer_switch_fee(entry, subscription_model) -> Decimal:
    """For history only: an upgrade left a «... for X RUB (includes switch fee)» note on the new subscription."""
    from modules.v2.subscriptions.services import UPGRADE_SWITCH_FEE_RUB

    charge = -_money(entry.delta)
    window = timedelta(minutes=10)
    upgraded = subscription_model.objects.filter(
        user_id=entry.user_id,
        notes__contains=f"for {charge:.2f} {SWITCH_FEE_NOTE}",
        created_at__gte=entry.created_at - window,
        created_at__lte=entry.created_at + window,
    ).exists()
    return min(UPGRADE_SWITCH_FEE_RUB, charge) if upgraded else ZERO


def sync(*, ledger_model=None, revenue_model=None, subscription_model=None, order_model=None, batch_size: int = 1000) -> dict:
    """Add revenue rows for every income operation that has none yet. Safe to run any time."""
    if ledger_model is None:
        from modules.v2.profiles.models import LedgerEntry as ledger_model
    if revenue_model is None:
        from .models import RevenueEntry as revenue_model
    if subscription_model is None:
        from modules.v2.subscriptions.models import UserSubscription as subscription_model
    if order_model is None:
        from modules.v2.tradehub.models import SaleOrder as order_model

    done_ledger = revenue_model.objects.filter(ledger_entry_id__isnull=False).values("ledger_entry_id")
    pending = ledger_model.objects.filter(income_ledger_filter()).exclude(pk__in=done_ledger).order_by("pk")
    ledger_rows = 0
    last_pk = 0
    while True:
        chunk = list(pending.filter(pk__gt=last_pk)[:batch_size])
        if not chunk:
            break
        rows = []
        for entry in chunk:
            fee = infer_switch_fee(entry, subscription_model) if entry.reason == LedgerReason.SUBSCRIPTION_PURCHASE else ZERO
            rows.extend(rows_for_entry(entry, switch_fee=fee))
        ledger_rows += _create(revenue_model, rows)
        last_pk = chunk[-1].pk

    done_refs = set(revenue_model.objects.filter(source=RevenueSource.TRADEHUB).values_list("ref", flat=True))
    completed = order_model.objects.filter(status="completed", payout_paid=True).order_by("pk")
    order_rows = [row for order in completed.iterator() if f"tradehub:{order.pk}" not in done_refs and (row := _tradehub_row(order))]
    tradehub_rows = _create(revenue_model, order_rows)
    return {"ledger": ledger_rows, "tradehub": tradehub_rows}
