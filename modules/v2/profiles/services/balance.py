from __future__ import annotations

from decimal import Decimal

from modules.v2.profiles.models import LedgerEntry
from modules.v2.profiles.services.ledger import LedgerError, apply_ledger_delta


class BalanceError(LedgerError):
    pass


def adjust_balance(
    *,
    user,
    delta: Decimal,
    reason: str,
    amount_snapshot: Decimal | None = None,
    case_opening_id: int | None = None,
    inventory_item_id: int | None = None,
    withdraw_request_id: int | None = None,
    idempotency_key: str | None = None,
) -> LedgerEntry:
    try:
        result = apply_ledger_delta(
            user=user,
            delta=delta,
            reason=reason,
            amount_snapshot=amount_snapshot,
            idempotency_key=idempotency_key,
            case_opening_id=case_opening_id,
            inventory_item_id=inventory_item_id,
            withdraw_request_id=withdraw_request_id,
        )
    except LedgerError as exc:
        raise BalanceError(str(exc)) from exc

    return result.entry
