"""Trial for a symbolic price and auto-renewal from the game balance.

A user who never had a site subscription may take the trial plan (SubscriptionPricingConfig.trial_*)
for a few days at the trial price. Taking it means agreeing to auto-renewal: shortly before each end
date the plan's monthly price is charged from the game balance and the term is extended by a month.
If the balance is short, the user is told how much to top up and the subscription simply ends.
Auto-renewal can be switched off at any time on the plans page; the paid term stays.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason, UserProfile
from modules.v2.profiles.services.balance import BalanceError, adjust_balance

from .models import SubscriptionPlan, UserSubscription

logger = logging.getLogger(__name__)

TRIAL_FALLBACK_PLAN_CODE = "site_plus"
# The hourly lifecycle task renews subscriptions that end within this window.
RENEW_AHEAD = timedelta(hours=2)


class TrialError(ValueError):
    pass


@dataclass(frozen=True)
class TrialOffer:
    plan: SubscriptionPlan
    price: Decimal
    days: int
    renewal_price: Decimal


def _trial_plan(config) -> SubscriptionPlan | None:
    plan = config.trial_plan or SubscriptionPlan.objects.filter(code=TRIAL_FALLBACK_PLAN_CODE).first()
    return plan if plan is not None and plan.is_active else None


def trial_offer(user=None) -> TrialOffer | None:
    """The trial on offer, or None when it is off or the user already had a subscription."""
    from .services import get_period_price, get_subscription_pricing_config

    config = get_subscription_pricing_config()
    if not config.trial_enabled:
        return None
    plan = _trial_plan(config)
    if plan is None:
        return None
    if user is not None and getattr(user, "is_authenticated", False):
        if UserSubscription.objects.filter(user=user, family=plan.family).exists():
            return None
    return TrialOffer(plan=plan, price=config.trial_price_rub, days=config.trial_days, renewal_price=get_period_price(plan, 1))


@transaction.atomic
def start_trial(user, *, accepted_auto_renew: bool) -> UserSubscription:
    from modules.v2.business.revenue import record_ledger_entry

    from .services import _lock_subscription_owner, assign_user_subscription_plan

    if not accepted_auto_renew:
        raise TrialError("Отметьте согласие на автопродление — без него пробный период не оформляется.")
    _lock_subscription_owner(user)
    UserProfile.objects.select_for_update().get_or_create(user=user)
    offer = trial_offer(user)
    if offer is None:
        raise TrialError("Пробный период доступен только тем, у кого ещё не было подписки.")
    try:
        entry = adjust_balance(
            user=user,
            delta=-offer.price,
            reason=LedgerReason.SUBSCRIPTION_PURCHASE,
            amount_snapshot=offer.price,
            comment=f"Пробный период «{offer.plan.name}» на {offer.days} дн.",
        )
    except BalanceError:
        raise TrialError(f"Недостаточно средств: для пробного периода нужно {offer.price:.2f} ₽ на балансе.") from None
    record_ledger_entry(entry)
    now = timezone.now()
    subscription = assign_user_subscription_plan(
        user=user,
        plan=offer.plan,
        starts_at=now,
        expires_at=now + timedelta(days=offer.days),
        notes=f"Trial {offer.days} days for {offer.price:.2f} RUB, auto-renew {offer.renewal_price:.2f} RUB/month accepted",
    )
    subscription.is_trial, subscription.auto_renew = True, True
    subscription.save(update_fields=["is_trial", "auto_renew", "updated_at"])
    return subscription


def set_auto_renew(user, *, enabled: bool) -> UserSubscription | None:
    from .services import get_active_subscription

    subscription = get_active_subscription(user)
    if subscription is None or subscription.expires_at is None:
        return None
    UserSubscription.objects.filter(pk=subscription.pk).update(auto_renew=enabled, updated_at=timezone.now())
    subscription.auto_renew = enabled
    return subscription


def _notify(user, subscription, *, title: str, body: str) -> None:
    from .lifecycle import _notify as lifecycle_notify
    from .notifications import KIND_SUBSCRIPTION_RENEWAL

    try:
        lifecycle_notify(user, KIND_SUBSCRIPTION_RENEWAL, object_id=subscription.pk, title=title, body=body)
    except Exception:  # noqa: BLE001 - a notice never undoes a charge
        logger.exception("Renewal notice for subscription %s failed", subscription.pk)


def renew_due_subscriptions(*, now=None) -> dict[str, int]:
    from .services import SubscriptionPurchaseError, purchase_subscription_with_balance

    now = now or timezone.now()
    due = list(
        UserSubscription.objects.select_related("user", "plan").filter(
            status=UserSubscription.Status.ACTIVE,
            auto_renew=True,
            expires_at__gt=now,
            expires_at__lte=now + RENEW_AHEAD,
        )
    )
    renewed = failed = 0
    for subscription in due:
        plan, user = subscription.plan, subscription.user
        if not plan.is_active:
            UserSubscription.objects.filter(pk=subscription.pk).update(auto_renew=False, updated_at=now)
            continue
        try:
            purchase_subscription_with_balance(user=user, plan=plan, months=1)
        except SubscriptionPurchaseError as exc:
            failed += 1
            # One notice per end date, not one per hourly run.
            if cache.add(f"subscriptions:renew_failed:{subscription.pk}:{subscription.expires_at:%Y%m%d%H%M}", 1, 60 * 60 * 6):
                balance = UserProfile.objects.filter(user=user).values_list("balance_coins", flat=True).first() or Decimal("0.00")
                _notify(
                    user,
                    subscription,
                    title=f"Не удалось продлить «{plan.name}»",
                    body=f"Для продления нужно {plan.price_rub:.2f} ₽, на балансе {balance:.2f} ₽ ({exc}). Пополните баланс до {timezone.localtime(subscription.expires_at):%d.%m %H:%M}, иначе подписка закончится.",
                )
            continue
        renewed += 1
        UserSubscription.objects.filter(pk=subscription.pk).update(is_trial=False, updated_at=now)
        fresh = UserSubscription.objects.get(pk=subscription.pk)
        _notify(
            user,
            fresh,
            title=f"Подписка «{plan.name}» продлена",
            body=f"С баланса списано за месяц. Новый срок — до {timezone.localtime(fresh.expires_at):%d.%m.%Y}. Отключить автопродление можно на странице тарифов.",
        )
    return {"renewed": renewed, "renew_failed": failed}
