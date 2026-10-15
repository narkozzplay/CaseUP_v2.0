"""Notification kinds of the market."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_LISTING_MISSING = "market_listing_missing"


def render_listing_missing(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    code, name = payload.get("code", ""), payload.get("name", "")
    if lang == "ru":
        title = f"Лот {code} снят с рынка"
        body = f"{name}: предмета больше нет в вашем инвентаре Steam, поэтому лот убран с витрины."
    else:
        title = f"Listing {code} was removed"
        body = f"{name} is no longer in your Steam inventory, so the listing was taken off the market."
    return NotificationView(title=title, body=body, url=reverse("market:sell"), icon="market")


def register_notification_kinds() -> None:
    register_kind(KIND_LISTING_MISSING, render_listing_missing)
