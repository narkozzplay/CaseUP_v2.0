"""Notification kinds emitted by promo codes."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_PROMO_BONUS = "promo_bonus"


def render_promo_bonus(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    code, amount = payload.get("code", ""), payload.get("amount", "")
    if lang == "ru":
        title, body = f"Бонус по промокоду {code}: +{amount} ₽", "Бонус к пополнению зачислен на баланс."
    else:
        title, body = f"Promo code {code} bonus: +{amount} ₽", "The top-up bonus is on your balance."
    return NotificationView(title=title, body=body, url=reverse("promo:dashboard"), icon="referrals")


def register_notification_kinds() -> None:
    register_kind(KIND_PROMO_BONUS, render_promo_bonus)
