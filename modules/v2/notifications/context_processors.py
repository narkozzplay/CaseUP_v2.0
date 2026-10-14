from __future__ import annotations

import logging

from .services import DROPDOWN_LIMIT, build_notification_rows, get_notifications, unread_count


logger = logging.getLogger(__name__)


def notifications_header(request):
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {"notifications_unread_total": 0, "notifications_recent": []}
    try:
        return {
            "notifications_unread_total": unread_count(user),
            "notifications_recent": build_notification_rows(
                get_notifications(user, limit=DROPDOWN_LIMIT),
                getattr(request, "LANGUAGE_CODE", "ru"),
            ),
        }
    except Exception:
        logger.exception("Failed to build notifications header context.")
        return {"notifications_unread_total": 0, "notifications_recent": []}
