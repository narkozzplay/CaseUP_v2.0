from __future__ import annotations

import re
from typing import Any

from social_core.exceptions import AuthForbidden

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

    raise AuthForbidden(backend, "Steam registration is disabled")
