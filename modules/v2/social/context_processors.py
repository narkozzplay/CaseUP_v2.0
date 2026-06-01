from __future__ import annotations

import logging

from .services import build_sidebar_context


logger = logging.getLogger(__name__)

EMPTY_SOCIAL_SIDEBAR_CONTEXT = {
    "social_counts": {
        "direct": 0,
        "group": 0,
        "conversation": 0,
    },
    "social_recent_spaces": [],
    "social_unread_total": 0,
    "social_unread_counts": {
        "by_type": {
            "direct": 0,
            "group": 0,
            "conversation": 0,
        },
        "by_space_id": {},
    },
}


def social_sidebar(request):
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return dict(EMPTY_SOCIAL_SIDEBAR_CONTEXT)

    try:
        return build_sidebar_context(user)
    except Exception:
        logger.exception("Failed to build social sidebar context.")
        return dict(EMPTY_SOCIAL_SIDEBAR_CONTEXT)
