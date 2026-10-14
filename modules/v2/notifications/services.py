from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from .models import Notification
from .registry import NotificationView, get_renderer


logger = logging.getLogger(__name__)

DROPDOWN_LIMIT = 6
PAGE_LIMIT = 100


def _lang(language_code: str | None) -> str:
    return "ru" if str(language_code or "ru").startswith("ru") else "en"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def notify(
    recipient,
    kind: str,
    *,
    actor=None,
    object_id: int | None = None,
    payload: dict | None = None,
    dedupe: bool = True,
) -> Notification | None:
    """Create a notification for ``recipient``.

    With ``dedupe`` an existing notification for the same recipient, kind,
    actor and object is brought back to the top as unread instead of adding
    a duplicate (e.g. a friend request that was cancelled and sent again).
    """
    if not getattr(recipient, "pk", None):
        return None
    if actor is not None and getattr(actor, "pk", None) == recipient.pk:
        return None

    with transaction.atomic():
        if dedupe:
            existing = (
                Notification.objects.select_for_update()
                .filter(recipient=recipient, kind=kind, actor=actor, object_id=object_id)
                .order_by("-created_at", "-id")
                .first()
            )
            if existing is not None:
                existing.is_read = False
                existing.read_at = None
                existing.created_at = timezone.now()
                if payload is not None:
                    existing.payload = payload
                existing.save(update_fields=["is_read", "read_at", "created_at", "payload"])
                return existing

        return Notification.objects.create(
            recipient=recipient,
            actor=actor,
            kind=kind,
            object_id=object_id,
            payload=payload or {},
        )


def mark_read(user, notification_id: int) -> bool:
    updated = Notification.objects.filter(pk=notification_id, recipient=user, is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    return bool(updated)


def mark_all_read(user) -> int:
    return Notification.objects.filter(recipient=user, is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )


def resolve_notifications(kind: str, object_id: int, *, recipient=None) -> int:
    """Mark notifications about an object as read once it was handled."""
    qs = Notification.objects.filter(kind=kind, object_id=object_id, is_read=False)
    if recipient is not None:
        qs = qs.filter(recipient=recipient)
    return qs.update(is_read=True, read_at=timezone.now())


def discard_notifications(kind: str, object_id: int, *, recipient=None) -> int:
    """Delete notifications about an object that no longer makes sense."""
    qs = Notification.objects.filter(kind=kind, object_id=object_id)
    if recipient is not None:
        qs = qs.filter(recipient=recipient)
    deleted, _ = qs.delete()
    return deleted


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def unread_count(user) -> int:
    if not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(recipient=user, is_read=False).count()


def get_notifications(user, *, limit: int = PAGE_LIMIT, unread_only: bool = False):
    qs = Notification.objects.filter(recipient=user).select_related("actor", "actor__profile_v2")
    if unread_only:
        qs = qs.filter(is_read=False)
    return qs.order_by("-created_at", "-id")[:limit]


def _fallback_view(notification: Notification, lang: str) -> NotificationView:
    return NotificationView(title="Новое уведомление" if lang == "ru" else "New notification")


def render_notification(notification: Notification, language_code: str | None = None) -> NotificationView:
    lang = _lang(language_code)
    renderer = get_renderer(notification.kind)
    if renderer is None:
        return _fallback_view(notification, lang)
    try:
        return renderer(notification, lang)
    except Exception:
        logger.exception("Failed to render notification %s (%s).", notification.pk, notification.kind)
        return _fallback_view(notification, lang)


def build_notification_rows(notifications, language_code: str | None = None) -> list[dict]:
    now = timezone.now()
    return [
        {
            "notification": item,
            "id": item.pk,
            "is_read": item.is_read,
            "created_at": item.created_at,
            "is_fresh": (now - item.created_at).total_seconds() < 60,
            "view": render_notification(item, language_code),
        }
        for item in notifications
    ]
