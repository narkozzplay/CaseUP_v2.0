"""The seller's «Вывод» wallet: what market sales earned, what is still held by open deals and what
may be withdrawn.

Sales are credited to the regular balance (LedgerReason.MARKET_SALE), so the site can be paid with them.
Only market money is withdrawable: top-ups, bonuses, promo codes and case items never are. The
withdrawable part is the unpaid earnings capped by the current balance — money spent on the site is gone.

The payout method itself is not chosen yet (PAYOUTS_ENABLED). When it is, a payout debits the balance
with LedgerReason.MARKET_PAYOUT and this wallet already accounts for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum

from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile

from .models import Deal, Listing

PAYOUTS_ENABLED = False
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class SellerWallet:
    earned: Decimal  # all completed sales
    withdrawn: Decimal  # paid out so far
    held: Decimal  # open deals: paid by buyers, not released yet
    balance: Decimal  # the regular site balance

    @property
    def unpaid(self) -> Decimal:
        return max(self.earned - self.withdrawn, ZERO)

    @property
    def available(self) -> Decimal:
        return max(min(self.unpaid, self.balance), ZERO)

    @property
    def spent(self) -> Decimal:
        """Earnings already spent on the site, so no longer withdrawable."""
        return self.unpaid - self.available


def is_market_seller(user) -> bool:
    return bool(user.is_authenticated) and Listing.objects.filter(seller=user).exists()


def _ledger_sum(user, reason) -> Decimal:
    return LedgerEntry.objects.filter(user=user, reason=reason).aggregate(total=Sum("delta"))["total"] or ZERO


def seller_wallet(user) -> SellerWallet:
    profile = UserProfile.objects.filter(user=user).only("balance_coins").first()
    held = Deal.objects.filter(seller=user, status__in=Deal.OPEN_STATUSES).aggregate(total=Sum("price"))["total"] or ZERO
    return SellerWallet(
        earned=_ledger_sum(user, LedgerReason.MARKET_SALE),
        withdrawn=-_ledger_sum(user, LedgerReason.MARKET_PAYOUT),
        held=held,
        balance=profile.balance_coins if profile else ZERO,
    )


def held_deals(user):
    """Open sales with what the money waits for."""
    rows = []
    for deal in Deal.objects.filter(seller=user, status__in=Deal.OPEN_STATUSES).select_related("listing", "buyer").order_by("created_at"):
        if deal.status == Deal.Status.HOLD:
            unlock = deal.hold_until
            waits = "защита обмена Steam"
        elif deal.status == Deal.Status.DISPUTED:
            unlock, waits = None, "решение администрации"
        elif deal.status == Deal.Status.SENT:
            unlock, waits = None, "подтверждение покупателя"
        else:
            unlock, waits = None, "отправка трейда"
        rows.append({"deal": deal, "unlock": unlock, "waits": waits})
    return rows


def sales_history(user, *, limit: int = 100):
    return Deal.objects.filter(seller=user, status=Deal.Status.COMPLETED).select_related("listing", "buyer").order_by("-completed_at")[:limit]


def payout_history(user, *, limit: int = 100):
    return LedgerEntry.objects.filter(user=user, reason=LedgerReason.MARKET_PAYOUT).order_by("-created_at")[:limit]
