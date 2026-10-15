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


class TwoFactorMiddleware:
    """Enforces the second factor.

    * A session that logged in to an account with 2FA reaches only the code page and logout until
      the code is entered (two_factor.PENDING_KEY).
    * Admin+ without 2FA are kept on the Безопасность page until they set it up.

    Static/media files, the language switch and the healthcheck are never blocked.
    """

    _ALWAYS_OPEN_PREFIXES = ("/i18n/", "/health/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False) and hasattr(request, "session") and not self._is_open(request.path_info):
            redirect_to = self._redirect_for(request, user)
            if redirect_to:
                return redirect_to
        return self.get_response(request)

    def _is_open(self, path: str) -> bool:
        from django.conf import settings

        prefixes = self._ALWAYS_OPEN_PREFIXES + tuple(p for p in (settings.STATIC_URL, settings.MEDIA_URL) if p and p.startswith("/"))
        return path.startswith(prefixes)

    def _redirect_for(self, request, user):
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        from django.utils.http import urlencode

        from . import two_factor

        verify_url = reverse("accounts:two_factor_verify")
        logout_url = reverse("accounts:logout")
        path = request.path_info
        if two_factor.is_pending(request):
            if path in (verify_url, logout_url):
                return None
            query = urlencode({"next": request.get_full_path()}) if request.method == "GET" else ""
            return HttpResponseRedirect(f"{verify_url}?{query}" if query else verify_url)

        if two_factor.is_required(user):
            security_url = reverse("accounts:security")
            if path in (security_url, logout_url, verify_url):
                return None
            if not two_factor.is_enabled(user):
                return HttpResponseRedirect(f"{security_url}?required=1")
        return None
