from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def push_prompt(request):
    """The «turn notifications on» banner after an action where a push helps (a purchase, a top-up...)."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {}
    try:
        from .services import prompt_for

        prompt = prompt_for(user)
    except Exception:  # noqa: BLE001
        logger.exception("Push prompt lookup failed")
        return {}
    return {"push_prompt": prompt} if prompt else {}
