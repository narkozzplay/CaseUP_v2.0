from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class LegalConsent(models.Model):
    """Evidence that a user accepted the legal documents: which editions, when, from where.

    Written at Steam login when the user ticked the consent boxes (accounts.consent). A user whose
    latest row is older than the current document versions is asked again at the next login.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="legal_consents")
    agreement_version = models.CharField(max_length=32)
    privacy_version = models.CharField(max_length=32)
    pd_consent_version = models.CharField(max_length=32)
    cookies_version = models.CharField(max_length=32)
    is_adult_confirmed = models.BooleanField(default=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    accepted_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-accepted_at", "-id")
        verbose_name = "Legal consent"
        verbose_name_plural = "Legal consents"
        indexes = [models.Index(fields=["user", "accepted_at"])]

    def __str__(self) -> str:
        return f"LegalConsent(user={self.user_id}, agreement={self.agreement_version}, at={self.accepted_at:%Y-%m-%d %H:%M})"
