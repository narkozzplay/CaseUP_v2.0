from __future__ import annotations

from .services.presence import touch_user_last_seen


class UserLastSeenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if touch_user_last_seen(user):
            request._profile_v2_cache = user.profile_v2

        return self.get_response(request)
