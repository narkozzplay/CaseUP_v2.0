from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import requests
from django.conf import settings

from .base import BasePaymentProvider, PaymentProviderError, ProviderPayment


PAY_URL = "https://pay.fk.money/"
API_URL = "https://api.fk.life/v1"

# https://docs.freekassa.net: order status in the API (0 new, 1 paid, 8 error, 9 canceled).
API_STATUS_MAP = {
    1: "succeeded",
    8: "failed",
    9: "canceled",
}


def _md5(*parts) -> str:
    return hashlib.md5(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()


class FreeKassaProvider(BasePaymentProvider):
    """FreeKassa (SCI): the payer goes to a signed pay.fk.money link, FreeKassa posts a signed
    notification back. The notification signature (secret word 2) is what proves the payment;
    the API key, when set, lets reconciliation re-check payments whose notification was lost."""

    code = "freekassa"
    title = "FreeKassa"
    webhook_ok_body = "YES"

    def __init__(self):
        self.merchant_id = str(getattr(settings, "FREEKASSA_MERCHANT_ID", "") or "")
        self.secret_1 = str(getattr(settings, "FREEKASSA_SECRET_1", "") or "")
        self.secret_2 = str(getattr(settings, "FREEKASSA_SECRET_2", "") or "")
        self.api_key = str(getattr(settings, "FREEKASSA_API_KEY", "") or "")
        self.timeout = float(getattr(settings, "FREEKASSA_TIMEOUT_SECONDS", 15))
        self.is_test_mode = bool(getattr(settings, "FREEKASSA_TEST_MODE", False))

    @classmethod
    def is_configured(cls) -> bool:
        return bool(
            getattr(settings, "FREEKASSA_MERCHANT_ID", "")
            and getattr(settings, "FREEKASSA_SECRET_1", "")
            and getattr(settings, "FREEKASSA_SECRET_2", "")
        )

    def create_payment(self, payment, *, return_url: str) -> ProviderPayment:
        amount = f"{payment.amount:.2f}"
        order_id = str(payment.public_id)
        params = {
            "m": self.merchant_id,
            "oa": amount,
            "currency": payment.currency,
            "o": order_id,
            "s": _md5(self.merchant_id, amount, self.secret_1, payment.currency, order_id),
            "lang": "ru",
        }
        email = (getattr(payment.user, "email", "") or "").strip()
        if email:
            params["em"] = email
        return ProviderPayment(
            external_id=order_id,
            status="pending",
            amount=payment.amount,
            currency=payment.currency,
            confirmation_url=f"{PAY_URL}?{urlencode(params)}",
            is_test=self.is_test_mode,
        )

    # --- notification -------------------------------------------------------------------------

    def parse_webhook(self, request) -> dict:
        return request.POST.dict()

    def extract_webhook_payment_id(self, payload: dict) -> str:
        expected = _md5(
            payload.get("MERCHANT_ID", ""),
            payload.get("AMOUNT", ""),
            self.secret_2,
            payload.get("MERCHANT_ORDER_ID", ""),
        )
        if str(payload.get("MERCHANT_ID", "")) != self.merchant_id or not hmac.compare_digest(
            expected, str(payload.get("SIGN", "")).lower()
        ):
            raise PaymentProviderError("Неверная подпись оповещения FreeKassa.")
        return str(payload.get("MERCHANT_ORDER_ID") or "")

    def apply_webhook(self, payment, payload: dict) -> None:
        """Keep the verified notification: it is FreeKassa's proof the order was paid."""
        payment.provider_payload = {**(payment.provider_payload or {}), "notification": payload}
        payment.save(update_fields=["provider_payload", "updated_at"])

    # --- status -------------------------------------------------------------------------------

    def fetch_payment(self, payment) -> ProviderPayment:
        payload = dict(payment.provider_payload or {})
        notification = payload.get("notification")
        if notification:
            return ProviderPayment(
                external_id=payment.external_id,
                status="succeeded",
                amount=_to_decimal(notification.get("AMOUNT")),
                is_test=payment.is_test,
                raw=payload,
            )

        if self.api_key:
            order = self._fetch_order(payment)
            if order is not None:
                status = API_STATUS_MAP.get(_to_int(order.get("status")))
                if status:
                    payload["api_order"] = order
                    return ProviderPayment(
                        external_id=payment.external_id,
                        status=status,
                        amount=_to_decimal(order.get("amount")),
                        currency=str(order.get("currency") or ""),
                        is_test=payment.is_test,
                        raw=payload,
                    )

        # FreeKassa keeps no expiry of its own; reconciliation cancels it after the site's payment timeout.
        return ProviderPayment(external_id=payment.external_id, status="pending", is_test=payment.is_test, raw=payload)

    def _api_signature(self, data: dict) -> str:
        message = "|".join(str(data[key]) for key in sorted(data))
        return hmac.new(self.api_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    def _fetch_order(self, payment) -> dict | None:
        data = {
            "shopId": int(self.merchant_id) if self.merchant_id.isdigit() else self.merchant_id,
            "nonce": time.time_ns(),
            "paymentId": payment.external_id,
        }
        data["signature"] = self._api_signature(data)
        try:
            response = requests.post(f"{API_URL}/orders", json=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PaymentProviderError(f"FreeKassa недоступна: {exc.__class__.__name__}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or body.get("type") == "error":
            raise PaymentProviderError(f"FreeKassa отклонила запрос: {body.get('message') or f'HTTP {response.status_code}'}")
        for order in body.get("orders") or []:
            if str(order.get("merchant_order_id") or "") == payment.external_id:
                return order
        return None


def _to_decimal(value):
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
