"""social-auth pipeline step: bind a newly created account to the owner of the invite link."""
from __future__ import annotations

from typing import Any


def bind_referral(strategy, backend: Any, user: Any = None, is_new: bool = False, *args, **kwargs):
    if user is None or not is_new:
        return None
    from modules.v2.control_center._compat import client_ip

    from .services import COOKIE_NAME, SESSION_KEY, bind_new_user

    request = getattr(strategy, "request", None)
    code = ""
    if request is not None:
        code = request.COOKIES.get(COOKIE_NAME, "")
    code = code or (strategy.session_get(SESSION_KEY) or "")
    if code:
        bind_new_user(user=user, code=code, ip_address=client_ip(request) if request is not None else None)
        strategy.session_pop(SESSION_KEY)
    return None
