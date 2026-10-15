from __future__ import annotations

import logging

from django.http import HttpResponseForbidden
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

_OPEN_PREFIXES = ("/health/", "/static/", "/payments/webhook/", "/telegram/")


class IpBanMiddleware:
    """Answers 403 to banned IPs. Admin+ are never blocked (so a wrong ban can always be lifted),
    nor are the healthcheck and payment/Telegram webhooks."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith(_OPEN_PREFIXES):
            try:
                if self._blocked(request):
                    response = HttpResponseForbidden(render_to_string("antifraud/ip_banned.html", request=None))
                    response._caseup_custom_403_rendered = True  # keep this page, not the generic 403
                    return response
            except Exception:  # noqa: BLE001 - the check must never take the site down
                logger.exception("IP ban check failed")
        return self.get_response(request)

    def _blocked(self, request) -> bool:
        from modules.v2.control_center._compat import client_ip

        from .services import is_ip_banned

        if not is_ip_banned(client_ip(request)):
            return False
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            from modules.v2.profiles.rbac import get_user_role

            if get_user_role(user).is_admin_plus:
                return False
        return True
