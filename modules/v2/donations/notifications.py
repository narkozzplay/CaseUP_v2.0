"""Notification kinds of donations: sponsor candidates (to admin+) and a granted sponsor role (to the donor)."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_SPONSOR_CANDIDATES = "donation_sponsor_candidates"
KIND_SPONSOR_GRANTED = "donation_sponsor_granted"


def render_candidates(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    names = ", ".join(payload.get("names") or [])
    url = reverse("control_center:donations") + "#sponsors"
    if payload.get("reason") == "lifetime":
        if lang == "ru":
            title = "Донатер заслужил роль «Спонсор»"
            body = f"{names}: донаты за всё время больше {payload.get('threshold', '')} ₽. Выдайте роль в Control Center."
        else:
            title = "A donor earned the Sponsor role"
            body = f"{names}: lifetime donations over {payload.get('threshold', '')} RUB."
    elif lang == "ru":
        title = f"Пора выдать роль «Спонсор» за {payload.get('month', '')}"
        body = f"Топ донатеров месяца с суммой больше {payload.get('threshold', '')} ₽: {names}. Выдайте роль в Control Center."
    else:
        title = f"Time to grant the Sponsor role for {payload.get('month', '')}"
        body = f"Top donors over {payload.get('threshold', '')} RUB: {names}."
    return NotificationView(title=title, body=body, url=url, icon="donations")


def render_granted(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    until = payload.get("until") or ""
    if lang == "ru":
        title = "Вам выдана роль «Спонсор»"
        body = f"Спасибо, что поддерживаете проект! Роль действует до {until}." if until else "Спасибо, что поддерживаете проект! Роль остаётся с вами навсегда."
    else:
        title = "You are now a Sponsor"
        body = f"Thank you for supporting the project! The role lasts until {until}." if until else "Thank you for supporting the project! The role is yours for good."
    return NotificationView(title=title, body=body, url=reverse("donations:page"), icon="donations")


def register_notification_kinds() -> None:
    register_kind(KIND_SPONSOR_CANDIDATES, render_candidates)
    register_kind(KIND_SPONSOR_GRANTED, render_granted)
