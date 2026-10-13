from __future__ import annotations

import ipaddress


def client_ip(request) -> str | None:
    raw = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip() or request.META.get("REMOTE_ADDR") or ""
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None
