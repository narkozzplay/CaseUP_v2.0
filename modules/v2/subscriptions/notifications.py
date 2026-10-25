from __future__ import annotations

from modules.v2.notifications.registry import payload_renderer, register_kind

KIND_SUBSCRIPTION_EXPIRING = "subscription_expiring"
KIND_SUBSCRIPTION_EXPIRED = "subscription_expired"
KIND_SUBSCRIPTION_GIFT = "subscription_gift"


def register_notification_kinds() -> None:
    register_kind(KIND_SUBSCRIPTION_EXPIRING, payload_renderer("subscriptions", "/subscriptions/"))
    register_kind(KIND_SUBSCRIPTION_EXPIRED, payload_renderer("subscriptions", "/subscriptions/"))
    register_kind(KIND_SUBSCRIPTION_GIFT, payload_renderer("subscriptions", "/subscriptions/"))
