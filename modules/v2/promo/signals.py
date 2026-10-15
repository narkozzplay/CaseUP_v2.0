"""A real top-up pays a pending top-up promo code (after the payment transaction commits)."""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def _after_ledger_entry(sender, instance, created, **kwargs):
    from modules.v2.profiles.models import LedgerReason

    if created and instance.reason == LedgerReason.TOPUP:
        entry_id = instance.pk
        transaction.on_commit(lambda: _apply(entry_id))


def _apply(entry_id: int) -> None:
    from modules.v2.profiles.models import LedgerEntry

    from .services import apply_topup_bonus

    try:
        entry = LedgerEntry.objects.filter(pk=entry_id).first()
        if entry is not None:
            apply_topup_bonus(entry)
    except Exception:  # noqa: BLE001 - the top-up itself is already committed
        logger.exception("Top-up promo bonus failed for ledger entry %s", entry_id)


def connect_ledger_signals() -> None:
    from modules.v2.profiles.models import LedgerEntry

    post_save.connect(_after_ledger_entry, sender=LedgerEntry, dispatch_uid="promo_ledger_entry")
