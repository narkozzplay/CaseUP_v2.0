from __future__ import annotations

import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out

from .sessions import end_on_logout, register_login

logger = logging.getLogger(__name__)


def _on_login(sender, request, user, **kwargs):
    if request is None or not hasattr(request, "session"):
        return
    try:
        register_login(request, user)
    except Exception:  # noqa: BLE001 - a login must never fail because of the device list
        logger.exception("Could not record the session for user %s", getattr(user, "pk", None))


def _on_logout(sender, request, user, **kwargs):
    if request is None:
        return
    try:
        end_on_logout(request)
    except Exception:  # noqa: BLE001
        logger.exception("Could not close the session record on logout")


def connect_session_signals() -> None:
    user_logged_in.connect(_on_login, dispatch_uid="accounts_session_login")
    user_logged_out.connect(_on_logout, dispatch_uid="accounts_session_logout")
