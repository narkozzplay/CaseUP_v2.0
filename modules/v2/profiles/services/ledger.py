from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile


ZERO = Decimal("0.00")


class LedgerError(ValueError):
    pass


class LedgerIdempotencyConflictError(LedgerError):
    pass


@dataclass(frozen=True)
class LedgerResult:
    entry: LedgerEntry
    profile: UserProfile


def _normalize_optional_decimal(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _validate_idempotent_replay(
    *,
    existing: LedgerEntry,
    user,
    delta: Decimal,
    normalized_reason: str,
    amount_snapshot: Decimal | None,
    case_opening_id: int | None,
    inventory_item_id: int | None,
    withdraw_request_id: int | None,
) -> None:
    mismatches: list[str] = []
    if existing.user_id != user.id:
        mismatches.append("user")
    if existing.delta != delta:
        mismatches.append("delta")
    if existing.reason != normalized_reason:
        mismatches.append("reason")
    if _normalize_optional_decimal(existing.amount_snapshot) != amount_snapshot:
        mismatches.append("amount_snapshot")
    if existing.case_opening_id != case_opening_id:
        mismatches.append("case_opening_id")
    if existing.inventory_item_id != inventory_item_id:
        mismatches.append("inventory_item_id")
    if existing.withdraw_request_id != withdraw_request_id:
        mismatches.append("withdraw_request_id")

    if mismatches:
        raise LedgerIdempotencyConflictError(
            "Idempotency key is already bound to another ledger operation: " + ", ".join(mismatches)
        )


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

    amount_snapshot = _normalize_optional_decimal(amount_snapshot)
    normalized_reason = reason if reason in LedgerReason.values else LedgerReason.ADMIN_ADJUST

    profile, _ = UserProfile.objects.select_for_update().get_or_create(
        user=user,
        defaults={"balance_coins": ZERO},
    )

    if idempotency_key:
        existing = (
            LedgerEntry.objects.select_for_update()
            .filter(user=user, idempotency_key=idempotency_key)
            .first()
        )
        if existing:
            _validate_idempotent_replay(
                existing=existing,
                user=user,
                delta=delta,
                normalized_reason=normalized_reason,
                amount_snapshot=amount_snapshot,
                case_opening_id=case_opening_id,
                inventory_item_id=inventory_item_id,
                withdraw_request_id=withdraw_request_id,
            )
            return LedgerResult(entry=existing, profile=profile)

    current_balance = Decimal(profile.balance_coins or ZERO).quantize(Decimal("0.01"))
    new_balance = (current_balance + delta).quantize(Decimal("0.01"))

    if new_balance < ZERO:
        raise LedgerError("Insufficient balance")

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
