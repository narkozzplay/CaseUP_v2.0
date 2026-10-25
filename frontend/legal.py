"""Public legal documents.

Bump a document's version whenever its text changes. Acceptance is stored with the versions the user
saw: LegalConsent (at Steam login) and Payment.terms_version (at every top-up). Users whose last
consent is older than the current agreement/privacy/consent versions are asked again at their next login.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LegalDocument:
    key: str
    title: str
    url_name: str
    version: str
    version_label: str


AGREEMENT = LegalDocument("agreement", "Пользовательское соглашение", "frontend:agreement", "2026-10-25", "25.10.2026")
PRIVACY = LegalDocument("privacy", "Политика конфиденциальности", "frontend:privacy", "2026-10-25", "25.10.2026")
PD_CONSENT = LegalDocument("consent", "Согласие на обработку персональных данных", "frontend:pd_consent", "2026-10-14", "14.10.2026")
COOKIES = LegalDocument("cookies", "Политика cookie", "frontend:cookies", "2026-10-25", "25.10.2026")

LEGAL_DOCUMENTS = (AGREEMENT, PRIVACY, PD_CONSENT, COOKIES)

# Kept for the top-up flow (Payment.terms_version).
AGREEMENT_VERSION = AGREEMENT.version
AGREEMENT_VERSION_LABEL = AGREEMENT.version_label
