from __future__ import annotations

from django.conf import settings
from django.urls import reverse

from .base import BasePaymentProvider, ProviderPayment


class DevPaymentProvider(BasePaymentProvider):
    """Local stand-in for a real gateway: the "payment page" is our own confirm screen.
    Only available with DEBUG=True so it can never credit money in production."""

    code = "dev"
    title = "Тестовая касса"
    is_test_mode = True

    @classmethod
    def is_configured(cls) -> bool:
        return bool(getattr(settings, "DEBUG", False))

    def create_payment(self, payment, *, return_url: str) -> ProviderPayment:
        from modules.v2.payments.models import Payment

        return ProviderPayment(
            external_id=f"dev-{payment.public_id}",
            status=Payment.Status.PENDING,
            amount=payment.amount,
            currency=payment.currency,
            confirmation_url=reverse("payments:dev_checkout", args=[payment.public_id]),
            is_test=True,
        )

    def fetch_payment(self, payment) -> ProviderPayment:
        dev_status = (payment.provider_payload or {}).get("dev_status") or payment.status
        return ProviderPayment(
            external_id=payment.external_id,
            status=dev_status,
            amount=payment.amount,
            currency=payment.currency,
            confirmation_url=payment.confirmation_url,
            is_test=True,
        )

    def extract_webhook_payment_id(self, payload: dict) -> str:
        return str(payload.get("id") or "")
