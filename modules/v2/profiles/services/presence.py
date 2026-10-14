from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from modules.v2.profiles.models import UserProfile

logger = logging.getLogger(__name__)

ONLINE_THRESHOLD_MINUTES = 5
LAST_SEEN_TOUCH_SECONDS = 60


def is_profile_online(profile: UserProfile | None, *, now=None) -> bool:
    last_seen_at = getattr(profile, "last_seen_at", None)
    if not last_seen_at:
        return False

    current_time = now or timezone.now()
    return last_seen_at >= current_time - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)


def touch_user_last_seen(user, *, now=None) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False

    cache_key = f"caseup:last_seen:{user.pk}"
    try:
        if cache.get(cache_key):
            return False
    except Exception:  # noqa: BLE001 - runs on every request; a cache outage must not break pages
        logger.warning("Cache unavailable, skipping last-seen update", exc_info=True)
        return False

    try:
        profile = user.profile_v2
    except UserProfile.DoesNotExist:
        return False

    current_time = now or timezone.now()
    UserProfile.objects.filter(pk=profile.pk).update(last_seen_at=current_time)
    profile.last_seen_at = current_time
    try:
        cache.set(cache_key, True, LAST_SEEN_TOUCH_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("Cache unavailable, last-seen throttle not stored", exc_info=True)
    return True
