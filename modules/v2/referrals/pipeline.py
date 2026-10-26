"""social-auth pipeline step: bind a newly created account to the owner of the invite link and give
both sides the welcome bonus after the invited user's first Steam sign-in."""
from __future__ import annotations

from typing import Any


def bind_referral(strategy, backend: Any, user: Any = None, is_new: bool = False, *args, **kwargs):
    if user is None:
        return None
    from modules.v2.control_center._compat import client_ip

    from .services import COOKIE_NAME, SESSION_KEY, bind_new_user
    from .welcome import on_steam_login

    request = getattr(strategy, "request", None)
    ip_address = client_ip(request) if request is not None else None
    if not is_new:
        # An account bound earlier (e.g. before it first signed in with Steam) gets its bonus now;
        # for everyone else this is one cheap lookup.
        if "steam" in (getattr(backend, "name", "") or ""):
            on_steam_login(user=user, steam_id=str(kwargs.get("uid") or ""), ip_address=ip_address)
        return None

    code = ""
    if request is not None:
        code = request.COOKIES.get(COOKIE_NAME, "")
    code = code or (strategy.session_get(SESSION_KEY) or "")
    if code:
        bind_new_user(user=user, code=code, ip_address=ip_address)
        strategy.session_pop(SESSION_KEY)
    return None
