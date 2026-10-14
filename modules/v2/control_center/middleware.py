from __future__ import annotations

from django.urls import Resolver404, resolve

from .models import ActivityCategory
from .services.activity import authenticated_user, bind_request, log_activity, unbind_request

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# Provider callbacks are logged as payment events; static/health are not user actions.
SKIP_PREFIXES = ("/static/", "/media/", "/health/", "/payments/webhook/", "/telegram/webhook/")


class ActivityContextMiddleware:
    """Makes the current request available to activity receivers and journals every state-changing
    request (method, route, status, IP). Request bodies are never stored: they can hold passwords and keys."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = bind_request(request)
        try:
            journaled = request.method not in SAFE_METHODS and not request.path.startswith(SKIP_PREFIXES)
            # Remember who sent it: a logout request ends with an anonymous request.user.
            sender = authenticated_user(getattr(request, "user", None)) if journaled else None
            response = self.get_response(request)
            if journaled:
                self._log_request(request, response, sender)
            return response
        finally:
            unbind_request(token)

    @staticmethod
    def _log_request(request, response, sender) -> None:
        try:
            match = resolve(request.path_info)
            route = f"{match.namespace}:{match.url_name}" if match.namespace else (match.url_name or "")
        except Resolver404:
            route = ""
        log_activity(
            category=ActivityCategory.REQUEST,
            event=f"request.{route}" if route else "request",
            summary=f"{request.method} {route or request.path} → {response.status_code}",
            user=authenticated_user(getattr(request, "user", None)) or sender,
            actor=authenticated_user(getattr(request, "user", None)) or sender,
            request=request,
            method=request.method,
            path=request.path,
            status_code=response.status_code,
        )
