"""Notification kinds emitted by the anti-fraud checks (to admin+)."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_MULTI_ACCOUNT = "antifraud_multi_account"


def render_multi_account(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    case, count = payload.get("case", ""), payload.get("count", 0)
    accounts = ", ".join(payload.get("accounts") or [])
    ips = ", ".join(payload.get("ips") or [])
    if lang == "ru":
        if payload.get("reason") == "joined":
            title = f"Мульти-аккаунт: новый аккаунт в группе #{case}"
        else:
            title = f"Мульти-аккаунт: {count} аккаунта с одного IP (группа #{case})"
        body = f"{accounts}. IP: {ips}. Проверьте и примите решение."
    else:
        title = f"Multi-account group #{case}: {count} account(s)"
        body = f"{accounts}. IP: {ips}."
    return NotificationView(title=title, body=body, url=reverse("control_center:multi_account_detail", args=[case]) if case else reverse("control_center:multi_accounts"), icon="warn")


def register_notification_kinds() -> None:
    register_kind(KIND_MULTI_ACCOUNT, render_multi_account)
