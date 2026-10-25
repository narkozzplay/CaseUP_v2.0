"""Request rate limit for guests on the public pages (catalog, sitemap).

A fixed window per IP in the cache: at most GUEST_RATE_LIMIT_REQUESTS requests per
GUEST_RATE_LIMIT_WINDOW seconds; over that the guest gets 429 with Retry-After until the window ends.
Signed-in users are not limited here: their pages are cheap and they can be banned by account.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger(__name__)

CACHE_PREFIX = "guest-rl"


def _limited_prefixes() -> tuple[str, ...]:
    return tuple(getattr(settings, "GUEST_RATE_LIMIT_PATHS", ()) or ())


def hit(key: str, *, limit: int, window: int) -> int:
    """Counts one request for key; returns 0 if allowed, else the seconds until the window ends."""
    window_start = int(time.time()) // window * window
    cache_key = f"{CACHE_PREFIX}:{key}:{window_start}"
    if cache.add(cache_key, 1, timeout=window + 1):
        count = 1
    else:
        try:
            count = cache.incr(cache_key)
        except ValueError:  # the key expired between add and incr
            cache.add(cache_key, 1, timeout=window + 1)
            count = 1
    if count <= limit:
        return 0
    return max(1, window_start + window - int(time.time()))


def too_many_requests(retry_after: int) -> HttpResponse:
    response = HttpResponse(
        "Слишком много запросов. Подождите немного или войдите через Steam.\n"
        "Too many requests. Please wait a moment or sign in with Steam.\n",
        status=429,
        content_type="text/plain; charset=utf-8",
    )
    response["Retry-After"] = str(retry_after)
    response["Cache-Control"] = "no-store"
    return response


class GuestRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        limit = int(getattr(settings, "GUEST_RATE_LIMIT_REQUESTS", 0) or 0)
        prefixes = _limited_prefixes()
        if limit > 0 and prefixes and request.path_info.startswith(prefixes) and not getattr(request.user, "is_authenticated", False):
            from modules.v2.control_center._compat import client_ip

            window = max(1, int(getattr(settings, "GUEST_RATE_LIMIT_WINDOW", 60)))
            try:
                retry_after = hit(client_ip(request) or "unknown", limit=limit, window=window)
            except Exception:  # noqa: BLE001 - a cache outage must not take the catalog down
                logger.exception("Guest rate limit check failed")
                retry_after = 0
            if retry_after:
                return too_many_requests(retry_after)
        return self.get_response(request)
