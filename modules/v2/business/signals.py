from __future__ import annotations

from django.db.models.signals import post_save


def _after_ledger_entry(sender, instance, created, **kwargs):
    from modules.v2.profiles.models import LedgerReason

    # Subscriptions are booked by the purchase itself: only it knows the switch-fee share.
    if created and instance.reason != LedgerReason.SUBSCRIPTION_PURCHASE:
        from .revenue import record_ledger_entry

        record_ledger_entry(instance)


def connect_ledger_signals() -> None:
    from modules.v2.profiles.models import LedgerEntry

    post_save.connect(_after_ledger_entry, sender=LedgerEntry, dispatch_uid="business_ledger_entry")
