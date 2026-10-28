from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal


class PaymentProviderError(Exception):
    pass


@dataclass(frozen=True)
class ProviderPayment:
    """Payment state as reported by the provider. status uses Payment.Status values."""

    external_id: str
    status: str
    amount: Decimal | None = None
    currency: str = ""
    confirmation_url: str = ""
    error_message: str = ""
    is_test: bool = False
    raw: dict = field(default_factory=dict)


class BasePaymentProvider:
    code: str = ""
    title: str = ""
    is_test_mode: bool = False
    # Networks webhooks may come from; empty = no source-IP check (see payments.ip_allowlist).
    webhook_ip_networks: tuple[str, ...] = ()
    # Body of the 200 answer to a processed webhook (FreeKassa wants "YES").
    webhook_ok_body: str = ""

    @classmethod
    def is_configured(cls) -> bool:
        return True

    def create_payment(self, payment, *, return_url: str) -> ProviderPayment:
        raise NotImplementedError

    def fetch_payment(self, payment) -> ProviderPayment:
        raise NotImplementedError

    def parse_webhook(self, request) -> dict:
        """Webhook body as a dict; raises ValueError when it cannot be read."""
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("webhook body is not an object")
        return payload

    def apply_webhook(self, payment, payload: dict) -> None:
        """Hook for providers whose signed webhook is itself the proof of payment."""

    def extract_webhook_payment_id(self, payload: dict) -> str:
        """Pull the provider payment id out of a webhook body. The body itself is never trusted:
        the service re-reads the payment through fetch_payment before crediting anything."""
        raise NotImplementedError
