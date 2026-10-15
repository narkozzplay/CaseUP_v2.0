"""Logging helpers."""
from __future__ import annotations

import hashlib

from django.utils.log import AdminEmailHandler

THROTTLE_SECONDS = 30 * 60


class ThrottledAdminEmailHandler(AdminEmailHandler):
    """Mails server errors to ADMINS, but the same error (path + exception) at most once per 30 minutes,
    so a broken page that everyone hits does not flood the mailbox."""

    def emit(self, record):
        try:
            from django.core.cache import cache

            request = getattr(record, "request", None)
            exc_type = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else ""
            signature = f"{getattr(request, 'path', '')}|{exc_type}|{record.getMessage()[:200]}"
            key = "admin-mail:" + hashlib.sha1(signature.encode("utf-8", "replace")).hexdigest()
            if not cache.add(key, 1, THROTTLE_SECONDS):
                return
        except Exception:  # noqa: BLE001 - without the cache, still send the mail
            pass
        super().emit(record)
