from __future__ import annotations

import re
from typing import Any

from social_core.exceptions import AuthPolicyError

from modules.v2.system.services import is_registration_enabled


_STEAM_ID_RE = re.compile(r"/openid/id/(\d+)$")


def _extract_claimed_id(response: Any = None) -> str:
    if response is None:
        return ""

    if isinstance(response, dict):
        return str(
            response.get("openid.claimed_id")
            or response.get("claimed_id")
            or response.get("id")
            or ""
        ).strip()

    endpoint = getattr(response, "endpoint", None)
    if endpoint is not None:
        claimed_id = getattr(endpoint, "claimed_id", "") or ""
        if claimed_id:
            return str(claimed_id).strip()

    claimed_id = getattr(response, "claimed_id", "") or ""
    if claimed_id:
        return str(claimed_id).strip()

    identity_url = getattr(response, "identity_url", "") or ""
    if identity_url:
        return str(identity_url).strip()

    display_identifier = getattr(response, "display_identifier", "") or ""
    if display_identifier:
        return str(display_identifier).strip()

    return ""


def _extract_steam_id(uid: Any = None, response: Any = None) -> str:
    if uid:
        uid_str = str(uid).strip()
        if uid_str.isdigit():
            return uid_str

    claimed_id = _extract_claimed_id(response=response)
    match = _STEAM_ID_RE.search(claimed_id)
    if match:
        return match.group(1)

    return ""


def steam_set_username(
    strategy: Any,
    details: dict[str, Any] | None,
    backend: Any,
    uid: Any = None,
    response: Any = None,
    user: Any = None,
    *args,
    **kwargs,
):
    if user is not None:
        return

    backend_name = getattr(backend, "name", "") or ""
    if "steam" not in backend_name:
        return

    steam_id = _extract_steam_id(uid=uid, response=response)
    username = f"steam_{steam_id}" if steam_id else "steam_user"

    details = details or {}
    details["username"] = username

    return {
        "username": username,
        "details": details,
    }


def enforce_registration_enabled(backend: Any, user: Any = None, *args, **kwargs):
    backend_name = getattr(backend, "name", "") or ""
    if "steam" not in backend_name:
        return

    if user is not None:
        return

    if is_registration_enabled():
        return

    raise AuthPolicyError(backend, "Steam registration is disabled")


def require_legal_consent(strategy, backend: Any, user: Any = None, *args, **kwargs):
    """Stop the Steam sign-in unless the documents were accepted on the login page (or earlier).

    Covers direct links to /oauth/login/steam/ that skip the login page, and users whose consent
    predates the current document editions.
    """
    from django.urls import reverse

    from .consent import SESSION_KEY, session_consent_is_current, user_has_current_consent

    if "steam" not in (getattr(backend, "name", "") or ""):
        return None
    if session_consent_is_current(strategy.session_get(SESSION_KEY)):
        return None
    if user is not None and user_has_current_consent(user):
        return None
    return strategy.redirect(f"{reverse('accounts:login')}?consent=required")


def record_legal_consent(strategy, backend: Any, user: Any = None, *args, **kwargs):
    from .consent import SESSION_KEY, record_consent, session_consent_is_current

    data = strategy.session_get(SESSION_KEY)
    if user is None or not session_consent_is_current(data):
        return None
    record_consent(user, data)
    strategy.session_pop(SESSION_KEY)
    return None


def prevent_foreign_steam_link(strategy, backend: Any, uid: Any = None, user: Any = None, *args, **kwargs):
    """A signed-in user linking Steam: refuse a Steam account that already belongs to someone else.

    Without this step social-auth raises AuthAlreadyAssociated and the user sees an error page.
    """
    from django.contrib import messages
    from django.urls import reverse

    if user is None or "steam" not in (getattr(backend, "name", "") or "") or not uid:
        return None
    social = backend.strategy.storage.user.get_social_auth(backend.name, str(uid))
    if social is None or social.user_id == user.pk:
        return None
    request = getattr(strategy, "request", None)
    if request is not None:
        messages.error(request, "Этот Steam-аккаунт уже привязан к другому аккаунту CaseUP. Войдите в него через Steam или обратитесь в поддержку.")
    return strategy.redirect(reverse("profiles:profile"))
