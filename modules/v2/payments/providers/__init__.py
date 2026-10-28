from __future__ import annotations

from django.conf import settings

from .base import BasePaymentProvider, PaymentProviderError, ProviderPayment
from .dev import DevPaymentProvider
from .freekassa import FreeKassaProvider
from .yookassa import YooKassaProvider


PROVIDER_CLASSES: dict[str, type[BasePaymentProvider]] = {
    DevPaymentProvider.code: DevPaymentProvider,
    YooKassaProvider.code: YooKassaProvider,
    FreeKassaProvider.code: FreeKassaProvider,
}


def _configured_provider_code() -> str:
    code = (getattr(settings, "PAYMENTS_PROVIDER", "") or "auto").strip().lower()
    if code != "auto":
        return code
    if YooKassaProvider.is_configured():
        return YooKassaProvider.code
    if FreeKassaProvider.is_configured():
        return FreeKassaProvider.code
    if getattr(settings, "DEBUG", False):
        return DevPaymentProvider.code
    return ""


def get_provider(code: str | None = None) -> BasePaymentProvider | None:
    """Return the provider by code, or the active one from settings. None means top-up is off."""
    code = (code or _configured_provider_code()).strip().lower()
    provider_class = PROVIDER_CLASSES.get(code)
    if provider_class is None or not provider_class.is_configured():
        return None
    return provider_class()


__all__ = [
    "BasePaymentProvider",
    "PaymentProviderError",
    "ProviderPayment",
    "get_provider",
]
