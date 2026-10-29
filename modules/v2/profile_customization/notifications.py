"""Notification kinds of the profile customization."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationView, register_kind

from .effects import SLOT_LABELS, get_effect

KIND_EFFECT_UNLOCKED = "profile_effect_unlocked"


def render_effect_unlocked(notification, lang: str) -> NotificationView:
    payload = notification.payload or {}
    effect = get_effect(payload.get("slot", ""), payload.get("key", ""))
    url = reverse("profile_customization:catalog") + "?tab=effects"
    if effect is None:
        title = "Новое оформление профиля" if lang == "ru" else "New profile cosmetic"
        return NotificationView(title=title, url=url, icon="customization_catalog")
    slot_ru, slot_en = SLOT_LABELS[effect.slot]
    if lang == "ru":
        return NotificationView(
            title=f"Открыто: {effect.title_ru}",
            body=f"{slot_ru}, {effect.rarity_label_ru.lower()}. {effect.how_ru or 'Награда CaseUP'}. Наденьте в «Оформлении профиля».",
            url=url,
            icon="customization_catalog",
        )
    return NotificationView(
        title=f"Unlocked: {effect.title_en}",
        body=f"{slot_en}, {effect.rarity_label_en}. {effect.how_en or 'CaseUP reward'}. Put it on in Profile appearance.",
        url=url,
        icon="customization_catalog",
    )


def register_notification_kinds() -> None:
    register_kind(KIND_EFFECT_UNLOCKED, render_effect_unlocked)


def notify_effect_unlocked(grant) -> None:
    from modules.v2.notifications.services import notify

    notify(grant.user, KIND_EFFECT_UNLOCKED, object_id=grant.pk, payload={"slot": grant.slot, "key": grant.key}, dedupe=False)
