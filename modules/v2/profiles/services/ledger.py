from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile


ZERO = Decimal("0.00")


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class LedgerResult:
    entry: LedgerEntry
    profile: UserProfile


@transaction.atomic
def apply_ledger_delta(
    *,
    user,
    delta: Decimal,
    reason: str,
    amount_snapshot: Decimal | None = None,
    idempotency_key: str | None = None,
    case_opening_id: int | None = None,
    inventory_item_id: int | None = None,
    withdraw_request_id: int | None = None,
) -> LedgerResult:
    if delta is None:
        raise LedgerError("Delta must not be empty")

    delta = Decimal(str(delta)).quantize(Decimal("0.01"))
    if delta == ZERO:
        raise LedgerError("Delta must not be zero")

    profile, _ = UserProfile.objects.select_for_update().get_or_create(
        user=user,
        defaults={"balance_coins": ZERO},
    )

    if idempotency_key:
        existing = (
            LedgerEntry.objects
            .filter(idempotency_key=idempotency_key)
            .select_related(None)
            .first()
        )
        if existing:
            return LedgerResult(entry=existing, profile=profile)

    current_balance = Decimal(profile.balance_coins or ZERO).quantize(Decimal("0.01"))
    new_balance = (current_balance + delta).quantize(Decimal("0.01"))

    if new_balance < ZERO:
        raise LedgerError("Insufficient balance")

    normalized_reason = reason if reason in LedgerReason.values else LedgerReason.ADMIN_ADJUST

    profile.balance_coins = new_balance
    profile.save(update_fields=["balance_coins", "updated_at"])

    entry = LedgerEntry.objects.create(
        user=user,
        delta=delta,
        balance_after=new_balance,
        reason=normalized_reason,
        amount_snapshot=amount_snapshot,
        idempotency_key=idempotency_key,
        case_opening_id=case_opening_id,
        inventory_item_id=inventory_item_id,
        withdraw_request_id=withdraw_request_id,
    )

    return LedgerResult(entry=entry, profile=profile)
