from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def support_header(request):
    """The badge on the "Поддержка" button: unanswered replies for a user, the work queue for a moderator."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {}
    from modules.v2.system.services import is_module_enabled

    from .services import is_support_staff, staff_queue_count, user_unread_count

    try:
        if not is_module_enabled("support"):
            return {}
        staff = is_support_staff(user)
        return {
            "support_enabled": True,
            "support_is_staff": staff,
            "support_badge": user_unread_count(user) + (staff_queue_count(user) if staff else 0),
        }
    except Exception:
        logger.exception("Failed to build support header context.")
        return {}
