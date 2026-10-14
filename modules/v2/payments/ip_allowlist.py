"""Source-IP check for provider webhooks.

Defense in depth only: the webhook body is never trusted anyway (sync_payment re-reads the payment
through the provider API). The check stops forged requests from triggering those API calls.
"""
from __future__ import annotations

import ipaddress
import logging

from django.conf import settings


logger = logging.getLogger(__name__)


def webhook_ip_check_enabled() -> bool:
    raw = str(getattr(settings, "PAYMENTS_WEBHOOK_IP_CHECK", "auto") or "auto").strip().lower()
    if raw == "auto":
        # Off in development, where webhooks come through ngrok or are simulated locally.
        return not settings.DEBUG
    return raw in {"1", "true", "yes", "on"}


def get_client_ip(request) -> str:
    """The address that connected to our outermost proxy.

    PAYMENTS_WEBHOOK_PROXY_COUNT = number of reverse proxies in front of Django that append to
    X-Forwarded-For (prod: Nginx -> 1). The entry that many places from the right was written by our
    own proxy and cannot be forged by the client; entries further left can.
    """
    proxy_count = int(getattr(settings, "PAYMENTS_WEBHOOK_PROXY_COUNT", 1))
    if proxy_count > 0:
        forwarded = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
        if len(forwarded) >= proxy_count:
            return forwarded[-proxy_count]
    return request.META.get("REMOTE_ADDR", "")


def ip_in_networks(ip: str, networks: tuple[str, ...]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(network, strict=False) for network in networks)


def is_allowed_webhook_source(request, provider) -> bool:
    networks = tuple(getattr(provider, "webhook_ip_networks", ()) or ())
    if not networks or not webhook_ip_check_enabled():
        return True
    ip = get_client_ip(request)
    if ip_in_networks(ip, networks):
        return True
    logger.warning("Rejected %s webhook from %s: not a provider address", provider.code, ip or "<unknown>")
    return False
