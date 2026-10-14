from __future__ import annotations

import logging

from django.db.models import Q

from .models import Friendship, FriendRequest


logger = logging.getLogger(__name__)


def friends_sidebar(request):
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {"friends_incoming_total": 0, "friends_total": 0}
    try:
        return {
            "friends_incoming_total": FriendRequest.objects.filter(
                to_user=user,
                status=FriendRequest.Status.PENDING,
            ).count(),
            "friends_total": Friendship.objects.filter(Q(user_a=user) | Q(user_b=user)).count(),
        }
    except Exception:
        logger.exception("Failed to build friends sidebar context.")
        return {"friends_incoming_total": 0, "friends_total": 0}
