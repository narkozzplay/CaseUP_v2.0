"""Promo codes: activation, limits and the four kinds of benefit.

* Balance bonus — credited at activation (LedgerReason.PROMO_BONUS).
* Top-up bonus — waits for the next real top-up of at least ``min_topup`` and adds ``percent`` of it,
  or the fixed ``amount`` (ledger hook on TOPUP, after commit).
* Subscription discount — waits for the next subscription purchase; the quote takes the larger of the
  referral discount and this one, and the purchase uses the code up only if the code's discount won.
* Free case — waits for the next opening of that case, which then costs nothing.

One activation per code per account; at most one pending code of each kind at a time. A new code of
the same kind replaces the pending one only when the user confirmed it (``replace=True``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import PromoActivation, PromoCode

logger = logging.getLogger(__name__)

MONEY_Q = Decimal("0.01")
HUNDRED = Decimal("100")
MAX_WRONG_CODES = 10
WRONG_CODES_WINDOW = timedelta(hours=1)
_WRONG_PREFIX = "promo-wrong:"


class PromoError(ValueError):
    pass


class PromoReplaceRequired(PromoError):
    """The user already holds an unused code of this kind; activating the new one would drop it."""

    def __init__(self, *, current: PromoActivation, new_code: str):
        self.current = current
        self.new_code = new_code
        super().__init__(f"У вас уже активен промокод {current.promo.code}. Заменить его на {new_code}?")


@dataclass(frozen=True)
class ActivationResult:
    activation: PromoActivation
    message: str
    became_referral_of: object | None = None


def normalize_code(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum() or ch in "-_")[:32]


def new_account_days() -> int:
    return int(getattr(settings, "PROMO_NEW_ACCOUNT_DAYS", 7))


# --------------------------------------------------------------------------- activation


def _wrong_key(user) -> str:
    return f"{_WRONG_PREFIX}{user.pk}"


def _register_wrong_code(user) -> None:
    key = _wrong_key(user)
    if not cache.add(key, 1, int(WRONG_CODES_WINDOW.total_seconds())):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, int(WRONG_CODES_WINDOW.total_seconds()))


def _has_real_topups(user) -> bool:
    from modules.v2.payments.models import Payment

    payments = Payment.objects.filter(user=user, status=Payment.Status.SUCCEEDED)
    if not getattr(settings, "PROMO_COUNT_TEST_PAYMENTS", False):
        payments = payments.filter(is_test=False)
    return payments.exists()


def _check_limits(promo: PromoCode, user, *, replace: bool = False) -> PromoActivation | None:
    """Raise PromoError when the code cannot be activated; return the pending code it would replace."""
    now = timezone.now()
    if not promo.is_active:
        raise PromoError("Этот промокод больше не действует.")
    if promo.starts_at and promo.starts_at > now:
        raise PromoError("Этот промокод ещё не начал действовать.")
    if promo.is_expired:
        raise PromoError("Срок действия промокода истёк.")
    if promo.is_exhausted:
        raise PromoError("Промокод уже активировали максимальное число раз.")
    if promo.owner_id and promo.owner_id == user.pk:
        raise PromoError("Свой промокод активировать нельзя.")
    if promo.only_new_accounts and user.date_joined < now - timedelta(days=new_account_days()):
        raise PromoError(f"Промокод только для новых аккаунтов (зарегистрированных не раньше {new_account_days()} дн. назад).")
    if promo.only_without_topups and _has_real_topups(user):
        raise PromoError("Промокод только для тех, кто ещё не пополнял баланс.")
    if promo.kind == PromoCode.Kind.FREE_CASE and (promo.case is None or not promo.case.is_active):
        raise PromoError("Кейс по этому промокоду сейчас недоступен.")
    if PromoActivation.objects.filter(promo=promo, user=user).exists():
        raise PromoError("Вы уже активировали этот промокод.")
    if promo.kind != PromoCode.Kind.BALANCE_BONUS:
        pending = PromoActivation.objects.select_related("promo").filter(user=user, status=PromoActivation.Status.PENDING, promo__kind=promo.kind).first()
        if pending is not None and pending.is_usable:
            if not replace:
                raise PromoReplaceRequired(current=pending, new_code=promo.code)
            return pending
    return None


def activate(*, user, raw_code: str, ip_address: str | None = None, replace: bool = False) -> ActivationResult:
    code = normalize_code(raw_code)
    if not code:
        raise PromoError("Введите промокод.")
    if int(cache.get(_wrong_key(user)) or 0) >= MAX_WRONG_CODES:
        raise PromoError("Слишком много неверных промокодов. Попробуйте через час.")
    promo = PromoCode.objects.select_related("case", "owner").filter(code=code).first()
    if promo is None:
        _register_wrong_code(user)
        raise PromoError("Такого промокода нет. Проверьте написание.")

    with transaction.atomic():
        # Lock only the promo row: Postgres refuses FOR UPDATE on the nullable side of the joins.
        promo = PromoCode.objects.select_for_update(of=("self",)).select_related("case", "owner").get(pk=promo.pk)
        replaced = _check_limits(promo, user, replace=replace)
        if replaced is not None:
            _release(replaced)
        try:
            with transaction.atomic():
                activation = PromoActivation.objects.create(promo=promo, user=user, ip_address=ip_address or None)
        except IntegrityError as exc:
            raise PromoError("Вы уже активировали этот промокод.") from exc
        PromoCode.objects.filter(pk=promo.pk).update(activations_count=F("activations_count") + 1)

        if promo.kind == PromoCode.Kind.BALANCE_BONUS:
            amount = Decimal(promo.amount or 0).quantize(MONEY_Q)
            if amount <= 0:
                raise PromoError("У промокода не настроена сумма бонуса.")
            entry = _credit(user, amount, activation, comment=f"Промокод {promo.code}")
            _mark_used(activation, amount=amount, entry=entry)
            message = f"Промокод активирован: на баланс зачислено {amount:.2f} ₽."
        elif promo.kind == PromoCode.Kind.TOPUP_BONUS:
            message = f"Промокод активирован: {promo.benefit_label}. Бонус придёт вместе со следующим пополнением."
        elif promo.kind == PromoCode.Kind.SUBSCRIPTION_DISCOUNT:
            message = f"Промокод активирован: {promo.benefit_label}. Скидка применится при следующей покупке подписки."
        else:
            message = f"Промокод активирован: следующее открытие кейса «{promo.case.name}» — бесплатно."

    referrer = _bind_blogger(promo, user, ip_address)
    summary = f"Активировал промокод {promo.code}: {promo.benefit_label}"
    if replaced is not None:
        summary += f" (вместо {replaced.promo.code})"
        message = message.replace("Промокод активирован", f"Промокод {replaced.promo.code} заменён на {promo.code}", 1)
    _log(user, "promo.activated", summary, obj=activation)
    return ActivationResult(activation=activation, message=message, became_referral_of=referrer)


def _release(activation: PromoActivation) -> None:
    """Drop an unused activation as if the code was never entered: the user may come back to it later,
    and its activation slot goes back to the code's limit."""
    PromoCode.objects.filter(pk=activation.promo_id, activations_count__gt=0).update(activations_count=F("activations_count") - 1)
    PromoActivation.objects.filter(pk=activation.pk, status=PromoActivation.Status.PENDING).delete()


def _bind_blogger(promo: PromoCode, user, ip_address):
    """A blogger code makes a fresh account (no inviter yet) the blogger's referral."""
    if promo.owner_id is None or user.date_joined < timezone.now() - timedelta(days=new_account_days()):
        return None
    from modules.v2.referrals.services import bind_to_referrer

    referral = bind_to_referrer(user=user, referrer=promo.owner, code=promo.code, ip_address=ip_address, source="промокоду")
    return promo.owner if referral is not None else None


# --------------------------------------------------------------------------- pending benefits


def pending_activation(user, kind: str) -> PromoActivation | None:
    if not getattr(user, "is_authenticated", False):
        return None
    activation = (
        PromoActivation.objects.select_related("promo", "promo__case")
        .filter(user=user, status=PromoActivation.Status.PENDING, promo__kind=kind)
        .order_by("created_at", "id")
        .first()
    )
    return activation if activation is not None and activation.is_usable else None


def subscription_discount(user, family: str) -> tuple[int, PromoActivation | None]:
    """(percent, activation) of the user's pending subscription code that applies to ``family``."""
    activation = pending_activation(user, PromoCode.Kind.SUBSCRIPTION_DISCOUNT)
    if activation is None:
        return 0, None
    scope = activation.promo.subscription_family
    if scope and scope != family:
        return 0, None
    return int(activation.promo.percent or 0), activation


def use_subscription_discount(*, activation_id: int, amount_saved: Decimal, note: str) -> None:
    """Called inside the subscription purchase transaction once the code's discount was charged."""
    activation = PromoActivation.objects.select_for_update().filter(pk=activation_id, status=PromoActivation.Status.PENDING).first()
    if activation is None:
        from modules.v2.subscriptions.services import SubscriptionPurchaseError

        raise SubscriptionPurchaseError("Промокод уже использован. Обновите страницу.")
    _mark_used(activation, amount=amount_saved, note=note)


def consume_free_case(*, user, case) -> PromoActivation | None:
    """Use up a pending free-case code for this case (inside the opening transaction)."""
    activation = (
        PromoActivation.objects.select_for_update(of=("self",))
        .select_related("promo")
        .filter(user=user, status=PromoActivation.Status.PENDING, promo__kind=PromoCode.Kind.FREE_CASE, promo__case=case)
        .order_by("created_at", "id")
        .first()
    )
    if activation is None or activation.promo.is_expired:
        return None
    _mark_used(activation, amount=case.open_price, note=f"Бесплатное открытие «{case.name}»")
    return activation


def free_case_activation(user, case) -> PromoActivation | None:
    activation = pending_activation(user, PromoCode.Kind.FREE_CASE)
    if activation is None:
        return None
    if activation.promo.case_id == case.pk:
        return activation
    # The oldest pending one may be for another case; look for this case specifically.
    other = (
        PromoActivation.objects.select_related("promo")
        .filter(user=user, status=PromoActivation.Status.PENDING, promo__kind=PromoCode.Kind.FREE_CASE, promo__case=case)
        .first()
    )
    return other if other is not None and other.is_usable else None


def topup_bonus_for(promo: PromoCode, topup: Decimal) -> Decimal:
    """What a top-up of ``topup`` earns with this code: the fixed amount, or the percent capped by max_bonus."""
    if promo.min_topup and topup < promo.min_topup:
        return Decimal("0")
    if promo.amount:
        return Decimal(promo.amount).quantize(MONEY_Q)
    bonus = (Decimal(topup) * Decimal(promo.percent or 0) / HUNDRED).quantize(MONEY_Q, rounding=ROUND_DOWN)
    if promo.max_bonus:
        bonus = min(bonus, Decimal(promo.max_bonus))
    return bonus


def apply_topup_bonus(entry) -> PromoActivation | None:
    """After a top-up: pay the pending top-up code if the payment qualifies (real money, big enough)."""
    from modules.v2.payments.models import Payment
    from modules.v2.profiles.models import LedgerReason

    if entry.reason != LedgerReason.TOPUP or entry.delta <= 0:
        return None
    payment = Payment.objects.filter(ledger_entry=entry).first()
    if payment is None or (payment.is_test and not getattr(settings, "PROMO_COUNT_TEST_PAYMENTS", False)):
        return None
    with transaction.atomic():
        activation = (
            PromoActivation.objects.select_for_update(of=("self",))
            .select_related("promo", "user")
            .filter(user_id=entry.user_id, status=PromoActivation.Status.PENDING, promo__kind=PromoCode.Kind.TOPUP_BONUS, created_at__lte=entry.created_at)
            .order_by("created_at", "id")
            .first()
        )
        if activation is None or activation.promo.is_expired:
            return None
        promo = activation.promo
        bonus = topup_bonus_for(promo, Decimal(entry.delta))
        if bonus < MONEY_Q:
            return None
        if promo.amount:
            comment = f"Промокод {promo.code}: +{bonus:.2f} ₽ к пополнению {entry.delta:.2f} ₽"
        else:
            comment = f"Промокод {promo.code}: +{promo.percent}% к пополнению {entry.delta:.2f} ₽"
        ledger_entry = _credit(activation.user, bonus, activation, comment=comment, key_suffix=f":{entry.pk}")
        _mark_used(activation, amount=bonus, entry=ledger_entry, note=f"Пополнение {entry.delta:.2f} ₽")
    _notify_bonus(activation.user, promo, bonus)
    return activation


# --------------------------------------------------------------------------- admin


def cancel_activation(*, actor, activation: PromoActivation, request=None) -> None:
    """Admin: cancel a pending activation (already paid bonuses stay — adjust the balance by hand)."""
    from modules.v2.control_center.models import AuditAction
    from modules.v2.control_center.services.audit import log_admin_action

    if activation.status != PromoActivation.Status.PENDING:
        raise PromoError("Отменить можно только неиспользованную активацию.")
    activation.status = PromoActivation.Status.CANCELED
    activation.note = f"Отменена: {actor.username}"
    activation.save(update_fields=["status", "note"])
    log_admin_action(actor=actor, action=AuditAction.PROMO_UPDATE, target_user=activation.user, summary=f"Отменена активация {activation.promo.code}", payload={"activation_id": activation.pk}, request=request)


# --------------------------------------------------------------------------- helpers


def _credit(user, amount: Decimal, activation: PromoActivation, *, comment: str, key_suffix: str = ""):
    from modules.v2.profiles.models import LedgerReason
    from modules.v2.profiles.services.balance import adjust_balance

    return adjust_balance(
        user=user,
        delta=amount,
        reason=LedgerReason.PROMO_BONUS,
        amount_snapshot=amount,
        idempotency_key=f"promo:{activation.pk}{key_suffix}",
        comment=comment,
    )


def _mark_used(activation: PromoActivation, *, amount=None, entry=None, note: str = "") -> None:
    activation.status = PromoActivation.Status.USED
    activation.used_at = timezone.now()
    activation.benefit_amount = Decimal(amount).quantize(MONEY_Q) if amount is not None else None
    activation.ledger_entry = entry
    activation.note = note[:255]
    activation.save(update_fields=["status", "used_at", "benefit_amount", "ledger_entry", "note"])


def _log(user, event: str, summary: str, *, obj=None) -> None:
    from modules.v2.control_center.models import ActivityCategory
    from modules.v2.control_center.services.activity import log_activity

    log_activity(category=ActivityCategory.ACCOUNT, event=event, summary=summary, user=user, obj=obj)


def _notify_bonus(user, promo: PromoCode, amount: Decimal) -> None:
    from modules.v2.notifications.services import notify

    from .notifications import KIND_PROMO_BONUS

    try:
        notify(user, KIND_PROMO_BONUS, dedupe=False, payload={"code": promo.code, "amount": f"{amount:.2f}"})
    except Exception:  # noqa: BLE001
        logger.exception("Promo bonus notification failed for user %s", user.pk)
