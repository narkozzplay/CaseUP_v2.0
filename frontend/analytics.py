"""Yandex Metrika: the counter loads only after the visitor accepts analytics cookies, never in the admin areas."""
from __future__ import annotations

from django.conf import settings

# Set by the cookie consent bar together with caseup_ad_consent when the bar also asks about ads.
CONSENT_COOKIE = "caseup_analytics_consent"
EXCLUDED_PATH_PREFIXES = ("/admin/", "/control-center/")


def metrika_id() -> str:
    value = str(getattr(settings, "YANDEX_METRIKA_ID", "") or "").strip()
    return value if value.isdigit() else ""


def has_analytics_consent(request) -> bool:
    return getattr(request, "COOKIES", {}).get(CONSENT_COOKIE) == "1"


def analytics_context(request):
    counter = metrika_id()
    path = getattr(request, "path", "") or ""
    if not counter or path.startswith(EXCLUDED_PATH_PREFIXES):
        return {"metrika_id": "", "analytics_ask_consent": False}
    consent = has_analytics_consent(request)
    return {
        "metrika_id": counter if consent else "",
        "analytics_ask_consent": not consent,
    }
