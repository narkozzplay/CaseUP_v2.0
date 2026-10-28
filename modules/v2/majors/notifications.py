"""Notification kinds of the Pick'em."""
from __future__ import annotations

from django.urls import NoReverseMatch, reverse

from modules.v2.notifications.registry import NotificationView, register_kind

KIND_REWARD = "major_pickem_reward"


def render_reward(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    title_text, reward, major = payload.get("title", ""), payload.get("reward", ""), payload.get("major", "")
    try:
        url = reverse("majors:pickem", args=[payload.get("slug", "")])
    except NoReverseMatch:
        url = ""
    if lang == "ru":
        return NotificationView(title=f"Награда Pick'em: {reward}", body=f"{major}: {title_text}.", url=url, icon="major")
    return NotificationView(title=f"Pick'em reward: {reward}", body=f"{major}: {title_text}.", url=url, icon="major")


def register_notification_kinds() -> None:
    register_kind(KIND_REWARD, render_reward)


def notify_reward(grant) -> None:
    from modules.v2.notifications.services import notify

    rule = grant.rule
    notify(
        grant.user,
        KIND_REWARD,
        object_id=grant.pk,
        payload={"title": rule.title, "reward": rule.reward_label, "major": rule.major.name, "slug": rule.major.slug},
    )
