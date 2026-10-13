from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.v2.profiles.models import LedgerReason
from modules.v2.profiles.services.balance import adjust_balance

from .models import Payment
from .providers import BasePaymentProvider, PaymentProviderError, get_provider


logger = logging.getLogger(__name__)

MONEY_Q = Decimal("0.01")


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


def create_topup_payment(*, user, amount: Decimal, return_url_builder) -> Payment:
    """Create a local Payment and register it with the active provider.

    return_url_builder(payment) -> absolute URL the provider sends the user back to.
    """
    provider = get_provider()
    if provider is None:
        raise TopupError("Пополнение временно недоступно: платёжный провайдер не настроен.")

    amount = Decimal(amount).quantize(MONEY_Q)
    minimum, maximum = get_topup_limits()
    if amount < minimum or amount > maximum:
        raise TopupError(f"Сумма пополнения должна быть от {minimum:.2f} до {maximum:.2f}.")

    payment = Payment.objects.create(
        user=user,
        provider=provider.code,
        amount=amount,
        currency=getattr(settings, "PAYMENTS_CURRENCY", "RUB"),
        description=f"Пополнение баланса CaseUP, пользователь {user.username}"[:255],
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
    if result.raw:
        payment.provider_payload = result.raw
    payment.save(update_fields=["external_id", "confirmation_url", "status", "provider_payload", "updated_at"])

    if payment.status == Payment.Status.SUCCEEDED:
        sync_payment(payment)
    return payment


def _credit(payment: Payment) -> None:
    entry = adjust_balance(
        user=payment.user,
        delta=payment.amount,
        reason=LedgerReason.TOPUP,
        amount_snapshot=payment.amount,
        idempotency_key=f"payment:{payment.public_id}",
        comment=f"Пополнение через {payment.provider} · {payment.external_id}",
    )
    payment.ledger_entry = entry


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
        new_status = remote.status if remote.status in Payment.Status.values else locked.status

        if new_status == Payment.Status.SUCCEEDED:
            if remote.amount is not None and remote.amount.quantize(MONEY_Q) != locked.amount:
                locked.status = Payment.Status.FAILED
                locked.error_message = f"Сумма у провайдера ({remote.amount}) не совпадает с заказом."
                logger.error("Top-up %s amount mismatch: %s != %s", locked.public_id, remote.amount, locked.amount)
            else:
                locked.status = Payment.Status.SUCCEEDED
                locked.paid_at = locked.paid_at or timezone.now()
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
