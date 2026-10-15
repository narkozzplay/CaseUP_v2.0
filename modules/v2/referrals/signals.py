"""Hooks on balance operations: a top-up may qualify a referral, an item sale pays a turnover bonus.

Both run after the surrounding transaction commits, so they never slow down or roll back the
operation itself; the daily referrals.reconcile task catches anything lost in between.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def _after_ledger_entry(sender, instance, created, **kwargs):
    from modules.v2.profiles.models import LedgerReason

    if not created:
        return
    if instance.reason == LedgerReason.TOPUP:
        user_id = instance.user_id
        transaction.on_commit(lambda: _run(_qualify, user_id))
    elif instance.reason == LedgerReason.ITEM_SALE and instance.case_opening_id is not None:
        entry_id = instance.pk
        transaction.on_commit(lambda: _run(_award, entry_id))


def _qualify(user_id: int) -> None:
    from django.contrib.auth import get_user_model

    from .services import check_qualification

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is not None:
        check_qualification(user)


def _award(entry_id: int) -> None:
    from modules.v2.profiles.models import LedgerEntry

    from .services import award_turnover_bonus

    entry = LedgerEntry.objects.filter(pk=entry_id).first()
    if entry is not None:
        award_turnover_bonus(entry)


def _run(func, arg) -> None:
    try:
        func(arg)
    except Exception:  # noqa: BLE001 - the money operation is already committed; reconcile retries
        logger.exception("Referral hook %s failed for %s", func.__name__, arg)


def connect_ledger_signals() -> None:
    from modules.v2.profiles.models import LedgerEntry

    post_save.connect(_after_ledger_entry, sender=LedgerEntry, dispatch_uid="referrals_ledger_entry")
