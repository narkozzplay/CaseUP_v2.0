"""Notification kinds of the daily bonus."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_REMINDER = "daily_bonus_reminder"


def render_reminder(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    day, streak = payload.get("day", 1), payload.get("streak", 0)
    if lang == "ru":
        title = "Бонус дня ждёт вас"
        body = f"Заберите награду дня {day}, чтобы не потерять серию из {streak} дн. Серия сгорает в полночь (МСК)."
    else:
        title = "Your daily bonus is waiting"
        body = f"Claim day {day} to keep your {streak}-day streak. It resets at midnight (Moscow time)."
    return NotificationView(title=title, body=body, url=reverse("daily_bonus:page"), icon="daily_bonus")


def register_notification_kinds() -> None:
    register_kind(KIND_REMINDER, render_reminder)
