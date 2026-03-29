from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import UserProfile


class UserLastSeenMiddleware:
    SESSION_KEY = "profile_v2_last_seen_touch"
    TOUCH_INTERVAL_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return response

        now = timezone.now()
        last_touch_raw = request.session.get(self.SESSION_KEY)
        should_touch = True

        if last_touch_raw:
            try:
                last_touch = timezone.datetime.fromisoformat(last_touch_raw)
                if timezone.is_naive(last_touch):
                    last_touch = timezone.make_aware(last_touch, timezone.get_current_timezone())
                should_touch = now - last_touch >= timedelta(seconds=self.TOUCH_INTERVAL_SECONDS)
            except (TypeError, ValueError):
                should_touch = True

        if should_touch:
            try:
                profile = user.profile_v2
            except UserProfile.DoesNotExist:
                profile, _ = UserProfile.objects.get_or_create(user=user)
            UserProfile.objects.filter(pk=profile.pk).update(last_seen_at=now)
            request.session[self.SESSION_KEY] = now.isoformat()
            request._profile_v2_cache = profile

        return response
