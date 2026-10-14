from __future__ import annotations

from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

from .base import BasePaymentProvider, PaymentProviderError, ProviderPayment


API_URL = "https://api.yookassa.ru/v3"

# https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process#lifecycle
STATUS_MAP = {
    "pending": "pending",
    "waiting_for_capture": "pending",
    "succeeded": "succeeded",
    "canceled": "canceled",
}


class YooKassaProvider(BasePaymentProvider):
    code = "yookassa"
    title = "ЮKassa"
    # Addresses YooKassa sends HTTP notifications from (yookassa.ru/developers/using-api/webhooks).
    webhook_ip_networks = (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.154.128/25",
        "2a02:5180::/32",
    )

    def __init__(self):
        self.shop_id = getattr(settings, "YOOKASSA_SHOP_ID", "")
        self.secret_key = getattr(settings, "YOOKASSA_SECRET_KEY", "")
        self.timeout = float(getattr(settings, "YOOKASSA_TIMEOUT_SECONDS", 15))
        self.is_test_mode = self.secret_key.startswith("test_")

    @classmethod
    def is_configured(cls) -> bool:
        return bool(getattr(settings, "YOOKASSA_SHOP_ID", "") and getattr(settings, "YOOKASSA_SECRET_KEY", ""))

    def _request(self, method: str, path: str, *, json=None, idempotence_key: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key
        try:
            response = requests.request(
                method,
                f"{API_URL}{path}",
                json=json,
                headers=headers,
                auth=(self.shop_id, self.secret_key),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PaymentProviderError(f"ЮKassa недоступна: {exc.__class__.__name__}") from exc

        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            description = data.get("description") or data.get("code") or f"HTTP {response.status_code}"
            raise PaymentProviderError(f"ЮKassa отклонила запрос: {description}")
        return data

    def _to_provider_payment(self, data: dict) -> ProviderPayment:
        amount_data = data.get("amount") or {}
        try:
            amount = Decimal(str(amount_data.get("value"))) if amount_data.get("value") is not None else None
        except InvalidOperation:
            amount = None
        cancellation = data.get("cancellation_details") or {}
        return ProviderPayment(
            external_id=str(data.get("id") or ""),
            status=STATUS_MAP.get(data.get("status"), "pending"),
            amount=amount,
            currency=str(amount_data.get("currency") or ""),
            confirmation_url=str((data.get("confirmation") or {}).get("confirmation_url") or ""),
            error_message=str(cancellation.get("reason") or ""),
            is_test=bool(data.get("test")),
            raw=data,
        )

    def create_payment(self, payment, *, return_url: str) -> ProviderPayment:
        body = {
            "amount": {"value": f"{payment.amount:.2f}", "currency": payment.currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": (payment.description or "Пополнение баланса")[:128],
            "metadata": {
                "payment_id": str(payment.public_id),
                "user_id": str(payment.user_id),
            },
        }
        if getattr(settings, "YOOKASSA_SEND_RECEIPT", False):
            email = (getattr(payment.user, "email", "") or "").strip()
            if not email:
                raise PaymentProviderError("Для чека нужен email: укажите его в профиле.")
            body["receipt"] = {
                "customer": {"email": email},
                "items": [
                    {
                        "description": (payment.description or "Пополнение баланса")[:128],
                        "quantity": "1.00",
                        "amount": {"value": f"{payment.amount:.2f}", "currency": payment.currency},
                        "vat_code": int(getattr(settings, "YOOKASSA_VAT_CODE", 1)),
                        "payment_mode": "full_prepayment",
                        "payment_subject": "service",
                    }
                ],
            }
        data = self._request("POST", "/payments", json=body, idempotence_key=str(payment.public_id))
        return self._to_provider_payment(data)

    def fetch_payment(self, payment) -> ProviderPayment:
        if not payment.external_id:
            raise PaymentProviderError("У платежа нет идентификатора ЮKassa.")
        return self._to_provider_payment(self._request("GET", f"/payments/{payment.external_id}"))

    def get_account_info(self) -> dict:
        """Shop info for the configured keys (GET /v3/me): proves the keys work, shows test/live mode."""
        return self._request("GET", "/me")

    def extract_webhook_payment_id(self, payload: dict) -> str:
        obj = payload.get("object") or {}
        return str(obj.get("id") or "")
