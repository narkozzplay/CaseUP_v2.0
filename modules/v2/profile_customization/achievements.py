"""Achievements that unlock reward effects.

Each check is a cheap count over data the site already has. `sync_achievements` grants
whatever the user now qualifies for; grants are permanent, so a check that later turns
false (a deleted alert, say) does not take the effect away.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from django.utils import timezone

from .effects import SLOT_FRAME, SLOT_HERO, SLOT_NAME, SLOT_TITLE


def _alerts(user) -> int:
    from modules.v2.steam_catalog.models import UserPriceAlertRule

    return UserPriceAlertRule.objects.filter(user=user).count()


def _favorites(user) -> int:
    from modules.v2.steam_catalog.models import SteamItemFavorite

    return SteamItemFavorite.objects.filter(user=user).count()


def _investments(user) -> int:
    from modules.v2.steam_trading.models import SteamPurchase

    return SteamPurchase.objects.filter(created_by=user).count()


def _completed_deals(user) -> int:
    from django.db.models import Q

    from modules.v2.market.models import Deal

    return Deal.objects.filter(Q(buyer=user) | Q(seller=user), status=Deal.Status.COMPLETED).count()


def _best_streak(user) -> int:
    from modules.v2.daily_bonus.models import DailyBonusState

    return DailyBonusState.objects.filter(user=user).values_list("best_streak", flat=True).first() or 0


def _qualified_referrals(user) -> int:
    from modules.v2.referrals.models import Referral

    return Referral.objects.filter(referrer=user, qualified_at__isnull=False, review=Referral.Review.OK).count()


def _days_on_site(user) -> int:
    joined = getattr(user, "date_joined", None)
    return (timezone.now() - joined).days if joined else 0


@dataclass(frozen=True)
class Achievement:
    key: str
    counter: Callable[[object], int]
    need: int
    slot: str
    effect_key: str


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("price_hunter", _alerts, 3, SLOT_TITLE, "price_hunter"),
    Achievement("collector", _favorites, 10, SLOT_TITLE, "collector"),
    Achievement("investor", _investments, 5, SLOT_TITLE, "investor"),
    Achievement("trader", _completed_deals, 1, SLOT_TITLE, "trader"),
    Achievement("dealmaker", _completed_deals, 10, SLOT_NAME, "case_hardened"),
    Achievement("regular", _best_streak, 7, SLOT_TITLE, "regular"),
    Achievement("streak30", _best_streak, 30, SLOT_FRAME, "case_hardened"),
    Achievement("recruiter", _qualified_referrals, 1, SLOT_TITLE, "recruiter"),
    Achievement("recruiter3", _qualified_referrals, 3, SLOT_HERO, "dust2"),
    Achievement("veteran", _days_on_site, 365, SLOT_TITLE, "veteran"),
)


def achievement_for(slot: str, key: str) -> Achievement | None:
    return next((a for a in ACHIEVEMENTS if a.slot == slot and a.effect_key == key), None)


def progress(user, owned: set[tuple[str, str]]) -> dict[tuple[str, str], tuple[int, int]]:
    """(have, need) for every achievement effect the user does not own yet, counters run once each."""
    cache: dict[Callable, int] = {}
    out = {}
    for achievement in ACHIEVEMENTS:
        ref = (achievement.slot, achievement.effect_key)
        if ref in owned:
            continue
        if achievement.counter not in cache:
            try:
                cache[achievement.counter] = int(achievement.counter(user))
            except Exception:  # noqa: BLE001 - a broken counter must not break the catalog
                cache[achievement.counter] = 0
        out[ref] = (min(cache[achievement.counter], achievement.need), achievement.need)
    return out


def due_achievements(user, owned: set[tuple[str, str]]) -> list[Achievement]:
    if not getattr(user, "pk", None):
        return []
    have = progress(user, owned)
    return [a for a in ACHIEVEMENTS if (a.slot, a.effect_key) in have and have[(a.slot, a.effect_key)][0] >= a.need]


def recently_active_cutoff():
    return timezone.now() - timedelta(days=30)
