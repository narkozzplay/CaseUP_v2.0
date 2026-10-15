from __future__ import annotations

import logging

from .sessions import touch

logger = logging.getLogger(__name__)


class UserSessionMiddleware:
    """Keeps the signed-in device list fresh (last activity, current IP). Throttled in sessions.touch."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False) and hasattr(request, "session"):
            try:
                touch(request)
            except Exception:  # noqa: BLE001 - bookkeeping must never break a page
                logger.exception("Session activity update failed")
        return response
