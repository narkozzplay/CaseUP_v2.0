"""Notification kinds emitted by the accounts app."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_NEW_LOGIN = "account_new_login"


def render_new_login(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    where = ", ".join(part for part in (payload.get("location", ""), payload.get("ip", "")) if part)
    device = payload.get("device", "")
    if lang == "ru":
        title = f"Вход с нового устройства: {device}"
        body = f"{where}. Если это были не вы — завершите сеанс и включите двухфакторную защиту." if where else "Если это были не вы — завершите сеанс."
    else:
        title = f"New sign-in: {device}"
        body = f"{where}. If it wasn't you, end that session." if where else "If it wasn't you, end that session."
    return NotificationView(title=title, body=body, url=reverse("accounts:security"), icon="warn")


def register_notification_kinds() -> None:
    register_kind(KIND_NEW_LOGIN, render_new_login)
