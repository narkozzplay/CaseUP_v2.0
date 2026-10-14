"""Consent to the legal documents before signing in with Steam.

The login page stores the ticked boxes in the session (Steam OpenID leaves the site and comes back in
the same session); the social-auth pipeline then refuses to sign in without a current consent and
writes a LegalConsent row once the user is known.
"""
from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from frontend.legal import AGREEMENT, COOKIES, PD_CONSENT, PRIVACY
from modules.v2.control_center._compat import client_ip

from .models import LegalConsent

SESSION_KEY = "caseup_legal_consent"


def current_versions() -> dict[str, str]:
    return {
        "agreement_version": AGREEMENT.version,
        "privacy_version": PRIVACY.version,
        "pd_consent_version": PD_CONSENT.version,
        "cookies_version": COOKIES.version,
    }


# A new cookie-policy edition alone (essential cookies only) does not require asking users again.
_REQUIRED_KEYS = ("agreement_version", "privacy_version", "pd_consent_version")


def store_login_consent(request) -> None:
    request.session[SESSION_KEY] = {
        **current_versions(),
        "accepted_at": timezone.now().isoformat(),
        "ip_address": client_ip(request),
        "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:255],
    }


def session_consent_is_current(data) -> bool:
    if not isinstance(data, dict):
        return False
    versions = current_versions()
    return all(data.get(key) == versions[key] for key in _REQUIRED_KEYS)


def user_has_current_consent(user) -> bool:
    versions = current_versions()
    return LegalConsent.objects.filter(user=user, **{key: versions[key] for key in _REQUIRED_KEYS}).exists()


def record_consent(user, data: dict) -> LegalConsent:
    try:
        accepted_at = datetime.fromisoformat(data.get("accepted_at") or "")
    except ValueError:
        accepted_at = timezone.now()
    return LegalConsent.objects.create(
        user=user,
        agreement_version=data.get("agreement_version", ""),
        privacy_version=data.get("privacy_version", ""),
        pd_consent_version=data.get("pd_consent_version", ""),
        cookies_version=data.get("cookies_version", ""),
        ip_address=data.get("ip_address") or None,
        user_agent=data.get("user_agent", "")[:255],
        accepted_at=accepted_at,
    )
