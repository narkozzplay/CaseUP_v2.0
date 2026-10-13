from __future__ import annotations

from .._compat import client_ip
from ..models import AdminAuditLog


def log_admin_action(*, actor, action: str, target_user=None, summary: str = "", payload: dict | None = None, request=None) -> AdminAuditLog:
    actor_obj = actor if getattr(actor, "is_authenticated", False) else None
    return AdminAuditLog.objects.create(
        actor=actor_obj,
        action=action,
        target_user=target_user,
        summary=(summary or "")[:255],
        payload=payload or {},
        ip_address=client_ip(request) if request is not None else None,
    )
