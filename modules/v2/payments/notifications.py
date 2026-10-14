"""Notification kinds emitted by the payments app."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_TOPUP_SUCCEEDED = "balance_topup_succeeded"


def render_topup_succeeded(notification, lang: str) -> NotificationView:
    amount = (notification.payload or {}).get("amount", "")
    if lang == "ru":
        title, body = f"Баланс пополнен на {amount} ₽", "Платёж подтверждён, деньги зачислены на баланс."
    else:
        title, body = f"Balance topped up by {amount} ₽", "The payment is confirmed and the money is on your balance."
    return NotificationView(title=title, body=body, url=reverse("payments:topup"), icon="balance_topup")


def register_notification_kinds() -> None:
    register_kind(KIND_TOPUP_SUCCEEDED, render_topup_succeeded)
