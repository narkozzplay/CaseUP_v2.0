"""Background upkeep: renew subscriptions with auto-renewal, close expired ones and warn users before the end."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import UserSubscription
from .notifications import KIND_SUBSCRIPTION_EXPIRED, KIND_SUBSCRIPTION_EXPIRING

REMINDER_LEAD = timedelta(days=3)


def _plans_url() -> str:
    try:
        return reverse("subscriptions:plans")
    except Exception:
        return "/subscriptions/"


def _notify(user, kind: str, *, object_id: int, title: str, body: str) -> None:
    from modules.v2.notifications.services import notify

    notify(user, kind, object_id=object_id, payload={"title": title, "body": body, "url": _plans_url()})


def expire_subscriptions(*, now=None) -> int:
    now = now or timezone.now()
    expired = list(
        UserSubscription.objects
        .select_related("user", "plan")
        .filter(status=UserSubscription.Status.ACTIVE, expires_at__isnull=False, expires_at__lte=now)
    )
    closed = 0
    for subscription in expired:
        # Re-check the end date in the update itself: a renewal that committed meanwhile moved it forward.
        updated = UserSubscription.objects.filter(
            pk=subscription.pk,
            status=UserSubscription.Status.ACTIVE,
            expires_at__lte=now,
        ).update(status=UserSubscription.Status.EXPIRED, updated_at=now)
        if not updated:
            continue
        closed += 1
        _notify(
            subscription.user,
            KIND_SUBSCRIPTION_EXPIRED,
            object_id=subscription.pk,
            title=f"Подписка «{subscription.plan.name}» закончилась",
            body="Продлите тариф, чтобы вернуть доступ к платным функциям.",
        )
    return closed


def remind_expiring_subscriptions(*, now=None) -> int:
    now = now or timezone.now()
    due = list(
        UserSubscription.objects
        .select_related("user", "plan")
        .filter(
            status=UserSubscription.Status.ACTIVE,
            expires_at__gt=now,
            expires_at__lte=now + REMINDER_LEAD,
            expiry_reminder_sent_at__isnull=True,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
    )
    reminded = 0
    for subscription in due:
        # Claim the reminder for this exact end date: a renewal that committed meanwhile moved it and keeps its own.
        claimed = UserSubscription.objects.filter(
            pk=subscription.pk,
            status=UserSubscription.Status.ACTIVE,
            expires_at=subscription.expires_at,
            expiry_reminder_sent_at__isnull=True,
        ).update(expiry_reminder_sent_at=now)
        if not claimed:
            continue
        reminded += 1
        local_end = timezone.localtime(subscription.expires_at)
        if subscription.auto_renew:
            from .trial import renewal_charge

            body = (
                f"Тариф действует до {local_end:%d.%m.%Y %H:%M}. Включено автопродление: перед окончанием с баланса "
                f"спишется {renewal_charge(subscription.user, subscription.plan):.2f} ₽ за месяц. Отключить его можно на странице тарифов."
            )
        else:
            body = f"Тариф действует до {local_end:%d.%m.%Y %H:%M}. Продлите его заранее, чтобы не потерять доступ."
        _notify(
            subscription.user,
            KIND_SUBSCRIPTION_EXPIRING,
            object_id=subscription.pk,
            title=f"Подписка «{subscription.plan.name}» скоро закончится",
            body=body,
        )
    return reminded


def process_subscription_lifecycle(*, now=None) -> dict[str, int]:
    from .trial import renew_due_subscriptions

    now = now or timezone.now()
    # Renew first, so a subscription paid for in time is never closed.
    renewal = renew_due_subscriptions(now=now)
    return {
        **renewal,
        "expired": expire_subscriptions(now=now),
        "reminded": remind_expiring_subscriptions(now=now),
    }
