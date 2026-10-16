from __future__ import annotations

import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.v2.notifications.services import notify
from modules.v2.profiles.models import LedgerReason
from modules.v2.profiles.services.balance import adjust_balance

from .models import Payment
from .providers import BasePaymentProvider, PaymentProviderError, get_provider


logger = logging.getLogger(__name__)

MONEY_Q = Decimal("0.01")

# Open (unpaid) payments a user may start within OPEN_PAYMENTS_WINDOW; every start is a provider API call.
OPEN_PAYMENTS_WINDOW = timedelta(minutes=15)
# Reconciliation: payments younger than this are left to the user's return / the webhook.
RECONCILE_MIN_AGE = timedelta(minutes=2)
# ...and older than this are no longer polled (the provider expires unpaid payments much sooner).
RECONCILE_MAX_AGE = timedelta(days=3)
# A payment that never got a provider id (creation failed mid-way) is closed after this.
UNSTARTED_PAYMENT_TTL = timedelta(hours=1)


class TopupError(Exception):
    pass


def get_topup_limits() -> tuple[Decimal, Decimal]:
    minimum = Decimal(str(getattr(settings, "PAYMENTS_TOPUP_MIN", "50"))).quantize(MONEY_Q)
    maximum = Decimal(str(getattr(settings, "PAYMENTS_TOPUP_MAX", "100000"))).quantize(MONEY_Q)
    return minimum, maximum


def get_topup_presets() -> list[Decimal]:
    raw = getattr(settings, "PAYMENTS_TOPUP_PRESETS", "100,300,500,1000,2500")
    presets = []
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if chunk:
            presets.append(Decimal(chunk).quantize(MONEY_Q))
    return presets


def topup_fee_percent() -> Decimal:
    from modules.v2.system.services import get_site_configuration

    value = getattr(get_site_configuration(), "topup_fee_percent", None)
    return Decimal(str(value if value is not None else "0")).quantize(MONEY_Q)


def quote_topup(credit: Decimal) -> tuple[Decimal, Decimal]:
    """(commission, total to pay) for putting ``credit`` on the balance."""
    credit = Decimal(credit).quantize(MONEY_Q)
    fee = (credit * topup_fee_percent() / Decimal("100")).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
    return fee, credit + fee


def create_topup_payment(*, user, amount: Decimal, return_url_builder, terms_version: str = "") -> Payment:
    """Create a local Payment and register it with the active provider.

    ``amount`` is what lands on the balance; the payer is charged it plus the top-up commission.
    return_url_builder(payment) -> absolute URL the provider sends the user back to.
    """
    provider = get_provider()
    if provider is None:
        raise TopupError("Пополнение временно недоступно: платёжный провайдер не настроен.")

    amount = Decimal(amount).quantize(MONEY_Q)
    minimum, maximum = get_topup_limits()
    if amount < minimum or amount > maximum:
        raise TopupError(f"Сумма пополнения должна быть от {minimum:.2f} до {maximum:.2f}.")

    max_open = int(getattr(settings, "PAYMENTS_MAX_OPEN_PER_USER", 5))
    open_count = Payment.objects.filter(
        user=user,
        status__in=(Payment.Status.CREATED, Payment.Status.PENDING),
        created_at__gte=timezone.now() - OPEN_PAYMENTS_WINDOW,
    ).count()
    if open_count >= max_open:
        raise TopupError("Слишком много неоплаченных платежей. Оплатите начатый платёж или подождите 15 минут.")

    fee, total = quote_topup(amount)
    payment = Payment.objects.create(
        user=user,
        provider=provider.code,
        amount=total,
        credit_amount=amount,
        fee_amount=fee,
        currency=getattr(settings, "PAYMENTS_CURRENCY", "RUB"),
        description=f"Пополнение баланса CaseUP, пользователь {user.username}"[:255],
        is_test=provider.is_test_mode,
        terms_version=terms_version,
    )

    try:
        result = provider.create_payment(payment, return_url=return_url_builder(payment))
    except PaymentProviderError as exc:
        payment.status = Payment.Status.FAILED
        payment.error_message = str(exc)[:255]
        payment.save(update_fields=["status", "error_message", "updated_at"])
        logger.warning("Top-up %s failed to start: %s", payment.public_id, exc)
        raise TopupError(str(exc)) from exc

    payment.external_id = result.external_id
    payment.confirmation_url = result.confirmation_url
    payment.status = result.status if result.status in Payment.Status.values else Payment.Status.PENDING
    payment.is_test = payment.is_test or result.is_test
    if result.raw:
        payment.provider_payload = result.raw
    payment.save(update_fields=["external_id", "confirmation_url", "status", "is_test", "provider_payload", "updated_at"])

    if payment.status == Payment.Status.SUCCEEDED:
        sync_payment(payment)
    return payment


def _credit(payment: Payment) -> None:
    entry = adjust_balance(
        user=payment.user,
        delta=payment.balance_amount,
        reason=LedgerReason.TOPUP,
        amount_snapshot=payment.balance_amount,
        idempotency_key=f"payment:{payment.public_id}",
        comment=f"Пополнение через {payment.provider} · {payment.external_id}",
    )
    payment.ledger_entry = entry
    if payment.fee_amount and payment.fee_amount > 0 and not payment.is_test:
        from modules.v2.business.models import RevenueSource
        from modules.v2.business.revenue import record_commission

        record_commission(
            source=RevenueSource.TOPUP_FEE,
            amount=payment.fee_amount,
            ref=f"payment:{payment.public_id}",
            user=payment.user,
            occurred_at=payment.paid_at,
            note=f"Комиссия с пополнения на {payment.balance_amount:.2f} ₽",
        )
    transaction.on_commit(lambda: _notify_credited(payment))


def _provider_fee(payment: Payment):
    """The provider's own fee from its payload (YooKassa: amount minus income_amount)."""
    income = ((payment.provider_payload or {}).get("income_amount") or {}).get("value")
    try:
        return (payment.amount - Decimal(str(income))).quantize(MONEY_Q) if income is not None else None
    except Exception:  # noqa: BLE001 - an odd payload just leaves the fee unknown
        return None


def _notify_credited(payment: Payment) -> None:
    from .notifications import KIND_TOPUP_SUCCEEDED

    try:
        notify(payment.user, KIND_TOPUP_SUCCEEDED, object_id=payment.pk, payload={"amount": f"{payment.balance_amount:.2f}"})
        from modules.v2.webpush.services import request_prompt

        request_prompt(payment.user, "topup")
    except Exception:  # noqa: BLE001 - the money is already credited; a failed notification must not hide that
        logger.exception("Top-up %s credited, but the notification failed", payment.public_id)


def sync_payment(payment: Payment, *, provider: BasePaymentProvider | None = None) -> Payment:
    """Ask the provider for the real state of the payment and credit the balance once it succeeded.

    Safe to call any number of times (webhook, return page, admin re-check): the row lock plus the
    ledger idempotency key make sure the money lands exactly once.
    """
    provider = provider or get_provider(payment.provider)
    if provider is None:
        return payment

    if payment.is_credited:
        return payment

    remote = provider.fetch_payment(payment)

    with transaction.atomic():
        locked = Payment.objects.select_for_update().select_related("user").get(pk=payment.pk)
        if locked.is_credited:
            return locked

        if remote.raw:
            locked.provider_payload = remote.raw
        locked.is_test = locked.is_test or remote.is_test
        new_status = remote.status if remote.status in Payment.Status.values else locked.status

        if new_status == Payment.Status.SUCCEEDED:
            if remote.amount is not None and remote.amount.quantize(MONEY_Q) != locked.amount:
                locked.status = Payment.Status.FAILED
                locked.error_message = f"Сумма у провайдера ({remote.amount}) не совпадает с заказом."
                logger.error("Top-up %s amount mismatch: %s != %s", locked.public_id, remote.amount, locked.amount)
            elif remote.currency and remote.currency.upper() != locked.currency.upper():
                locked.status = Payment.Status.FAILED
                locked.error_message = f"Валюта у провайдера ({remote.currency}) не совпадает с заказом."
                logger.error("Top-up %s currency mismatch: %s != %s", locked.public_id, remote.currency, locked.currency)
            else:
                locked.status = Payment.Status.SUCCEEDED
                locked.paid_at = locked.paid_at or timezone.now()
                locked.provider_fee = _provider_fee(locked)
                _credit(locked)
        else:
            locked.status = new_status
            if remote.error_message:
                locked.error_message = remote.error_message[:255]

        locked.save()
        return locked


def handle_webhook(*, provider_code: str, payload: dict) -> Payment | None:
    provider = get_provider(provider_code)
    if provider is None:
        return None
    external_id = provider.extract_webhook_payment_id(payload)
    if not external_id:
        return None
    payment = Payment.objects.filter(provider=provider.code, external_id=external_id).first()
    if payment is None:
        logger.info("Webhook for unknown %s payment %s", provider.code, external_id)
        return None
    return sync_payment(payment, provider=provider)


def reconcile_pending_payments(*, now=None, limit: int = 200) -> dict[str, int]:
    """Re-check unpaid payments with the provider so a lost webhook or a closed tab never strands money.

    Runs from Celery beat. Each payment goes through sync_payment, so crediting stays exactly-once.
    """
    now = now or timezone.now()
    stats = {"checked": 0, "credited": 0, "closed": 0, "errors": 0, "unstarted_closed": 0}

    stats["unstarted_closed"] = Payment.objects.filter(
        status=Payment.Status.CREATED,
        external_id="",
        created_at__lt=now - UNSTARTED_PAYMENT_TTL,
    ).update(
        status=Payment.Status.FAILED,
        error_message="Платёж не был создан у провайдера.",
        updated_at=now,
    )

    pending = (
        Payment.objects.filter(
            status__in=(Payment.Status.CREATED, Payment.Status.PENDING),
            created_at__lte=now - RECONCILE_MIN_AGE,
            created_at__gte=now - RECONCILE_MAX_AGE,
        )
        .exclude(external_id="")
        .order_by("created_at")[:limit]
    )
    for payment in pending:
        stats["checked"] += 1
        try:
            synced = sync_payment(payment)
        except PaymentProviderError as exc:
            stats["errors"] += 1
            logger.warning("Reconcile: top-up %s could not be checked: %s", payment.public_id, exc)
            continue
        if synced.status == Payment.Status.SUCCEEDED:
            stats["credited"] += 1
        elif synced.is_final:
            stats["closed"] += 1
    if stats["checked"] or stats["unstarted_closed"]:
        logger.info("Reconcile pending payments: %s", stats)
    return stats
