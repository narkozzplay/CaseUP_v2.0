"""Notification kinds emitted by the referral program."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_REFERRAL_COUNTED = "referral_counted"


def render_referral_counted(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    referee, count = payload.get("referee", ""), payload.get("count", 0)
    discount, turnover = payload.get("discount", 0), payload.get("turnover", 0)
    if lang == "ru":
        title = f"Реферал засчитан: {referee}"
        body = f"Засчитано рефералов: {count}. Скидка на подписки — {discount}%."
        if turnover:
            body += f" Бонус с продаж рефералов — {turnover}%."
    else:
        title = f"Referral counted: {referee}"
        body = f"Counted referrals: {count}. Subscription discount: {discount}%."
        if turnover:
            body += f" Turnover bonus: {turnover}%."
    return NotificationView(title=title, body=body, url=reverse("referrals:dashboard"), icon="referrals")


def register_notification_kinds() -> None:
    register_kind(KIND_REFERRAL_COUNTED, render_referral_counted)
