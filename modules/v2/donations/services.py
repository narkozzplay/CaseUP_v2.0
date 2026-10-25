"""Donations to the project from the site balance, and the progress of the current fundraiser.

The goal (texts, amounts, look, placements) is one row edited in Control Center. A donation moves money
from the donor's balance in one ledger entry; the form's one-time token is the ledger idempotency key, so a
double click or a resubmitted form never charges twice.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.services.balance import BalanceError, adjust_balance
from modules.v2.system.request_cache import request_memoize

from .models import ZERO, Donation, DonationGoal

RECENT_SIZE = 5
# Page slot -> the goal flag that turns the widget on there («page» is the donation page itself).
PLACEMENT_FLAGS = {"home": "show_on_home", "dashboard": "show_on_dashboard", "sidebar": "show_in_sidebar", "page": None}


class DonationError(Exception):
    pass


@dataclass
class DonorRow:
    name: str
    amount: Decimal
    message: str
    created_at: object
    is_sponsor: bool = False


@dataclass
class WidgetState:
    goal: DonationGoal
    collected: Decimal
    donated: Decimal
    donors: int
    percent: int
    bar_percent: int
    reached: bool
    days_left: int | None
    recent: list[DonorRow] = field(default_factory=list)


def _donor_name(donation: Donation) -> str:
    if donation.is_anonymous:
        return "Аноним"
    profile = getattr(donation.user, "profile_v2", None)
    return (getattr(profile, "steam_personaname", "") or "").strip() or donation.user.username


def build_state(goal: DonationGoal, *, recent: bool = True, today: date | None = None) -> WidgetState:
    campaign = Donation.objects.filter(created_at__gte=goal.started_at)
    totals = campaign.aggregate(total=Sum("amount"), donors=Count("user_id", distinct=True))
    donated = totals["total"] or ZERO
    collected = donated + (goal.collected_offset or ZERO)
    percent = int((collected * 100 / goal.goal_amount).to_integral_value(rounding=ROUND_DOWN)) if goal.goal_amount > 0 else 0
    days_left = None
    if goal.deadline:
        days_left = max((goal.deadline - (today or timezone.localdate())).days, 0)
    rows = []
    if recent and goal.show_recent_donors:
        from .sponsors import sponsor_user_ids

        latest = list(campaign.select_related("user", "user__profile_v2")[:RECENT_SIZE])
        sponsors = sponsor_user_ids(donation.user_id for donation in latest if not donation.is_anonymous)
        for donation in latest:
            rows.append(DonorRow(_donor_name(donation), donation.amount, donation.message, donation.created_at, donation.user_id in sponsors and not donation.is_anonymous))
    return WidgetState(
        goal=goal,
        collected=collected,
        donated=donated,
        donors=totals["donors"] or 0,
        percent=percent,
        bar_percent=min(max(percent, 0), 100),
        reached=collected >= goal.goal_amount,
        days_left=days_left,
        recent=rows,
    )


@request_memoize
def current_goal() -> DonationGoal:
    return DonationGoal.load()


def widget_state_for(placement: str) -> WidgetState | None:
    """The widget for a page slot, or None when the fundraiser is off or not shown there."""
    goal = current_goal()
    if not goal.is_enabled:
        return None
    if placement not in PLACEMENT_FLAGS:
        return None
    flag = PLACEMENT_FLAGS[placement]
    if flag and not getattr(goal, flag):
        return None
    return _memo_state(goal.pk, goal.updated_at, placement != "sidebar")


@request_memoize
def _memo_state(_pk, _updated_at, recent: bool) -> WidgetState:
    return build_state(current_goal(), recent=recent)


def new_token() -> str:
    return str(uuid.uuid4())


def _is_banned(user) -> bool:
    from modules.v2.control_center.models import UserBan

    return UserBan.objects.active().filter(user=user).exists()


def donate(*, user, amount, token, message: str = "", is_anonymous: bool = False) -> Donation:
    goal = DonationGoal.load()
    if not goal.is_enabled:
        raise DonationError("Сбор сейчас не ведётся.")
    if _is_banned(user):
        raise DonationError("Аккаунт заблокирован.")
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount < goal.min_amount or amount > goal.max_amount:
        raise DonationError(f"Сумма доната — от {goal.min_amount:.0f} до {goal.max_amount:.0f} ₽.")
    message = (message or "").strip()[:200] if goal.allow_messages else ""
    key = f"donation:{user.pk}:{token}"

    with transaction.atomic():
        # One lock per user: a replayed token finds its donation instead of racing to create a second one.
        UserProfile.objects.select_for_update().get_or_create(user=user)
        try:
            entry = adjust_balance(
                user=user,
                delta=-amount,
                reason=LedgerReason.DONATION,
                amount_snapshot=amount,
                idempotency_key=key,
                comment=f"Донат: {goal.title}"[:255],
            )
        except BalanceError as exc:
            if "Insufficient" in str(exc):
                raise DonationError("На балансе недостаточно средств.") from exc
            raise DonationError("Не удалось списать донат с баланса. Обновите страницу и попробуйте ещё раз.") from exc
        existing = Donation.objects.filter(ledger_entry_id=entry.pk).first()
        if existing is not None:
            return existing
        donation = Donation.objects.create(
            user=user,
            amount=amount,
            message=message,
            is_anonymous=is_anonymous,
            goal_title=goal.title,
            ledger_entry_id=entry.pk,
        )
        from .sponsors import check_lifetime

        check_lifetime(user)
        return donation


def start_new_campaign(goal: DonationGoal | None = None) -> DonationGoal:
    goal = goal or DonationGoal.load()
    goal.started_at = timezone.now()
    goal.collected_offset = ZERO
    goal.save(update_fields=["started_at", "collected_offset", "updated_at"])
    return goal


def admin_summary(goal: DonationGoal) -> dict:
    all_time = Donation.objects.aggregate(total=Sum("amount"), count=Count("id"), donors=Count("user_id", distinct=True))
    top = (
        Donation.objects.filter(created_at__gte=goal.started_at)
        .values("user_id", "user__username")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")[:10]
    )
    return {
        "state": build_state(goal),
        "all_time_total": all_time["total"] or ZERO,
        "all_time_count": all_time["count"] or 0,
        "all_time_donors": all_time["donors"] or 0,
        "campaign_count": Donation.objects.filter(created_at__gte=goal.started_at).count(),
        "top": list(top),
        "recent": list(Donation.objects.select_related("user", "user__profile_v2")[:30]),
    }
