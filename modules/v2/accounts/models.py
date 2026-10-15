from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from common.encryption import EncryptedTextField


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


class UserSession(models.Model):
    """One signed-in browser/device: where and how the user logged in, and when it was last active.

    Mirrors a Django session (by key). Ending a session deletes the Django session itself, so the
    device is signed out on its next request; the row stays as history until pruned.
    """

    class Method(models.TextChoices):
        STEAM = "steam", "Steam"
        PASSWORD = "password", "Пароль"
        UNKNOWN = "", "—"

    class EndReason(models.TextChoices):
        LOGOUT = "logout", "Выход"
        REVOKED = "revoked", "Завершён пользователем"
        ADMIN = "admin", "Завершён администратором"
        EXPIRED = "expired", "Истёк"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_sessions")
    session_key = models.CharField(max_length=40, unique=True)
    login_method = models.CharField(max_length=16, choices=Method.choices, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    city = models.CharField(max_length=120, blank=True, default="")
    country = models.CharField(max_length=120, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")
    device_type = models.CharField(max_length=16, blank=True, default="")
    os = models.CharField(max_length=64, blank=True, default="")
    browser = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_reason = models.CharField(max_length=16, choices=EndReason.choices, blank=True, default="")

    class Meta:
        ordering = ("-last_seen_at", "-id")
        verbose_name = "User session"
        verbose_name_plural = "User sessions"
        indexes = [models.Index(fields=["user", "ended_at", "last_seen_at"])]

    def __str__(self) -> str:
        return f"UserSession(user={self.user_id}, {self.device_label}, ip={self.last_ip or self.ip_address})"

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    @property
    def device_label(self) -> str:
        parts = [part for part in (self.os, self.browser) if part]
        return " · ".join(parts) or "Неизвестное устройство"

    @property
    def location_label(self) -> str:
        return ", ".join(part for part in (self.city, self.country) if part)


class TwoFactorAuth(models.Model):
    """Authenticator-app (TOTP) second factor of one user.

    A row with ``confirmed_at`` empty is a setup in progress (QR shown, first code not entered yet).
    Backup codes are stored only as SHA-256 hashes: they are random and long enough that a slow hash
    adds nothing, and every guess is rate-limited anyway.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="two_factor")
    secret = EncryptedTextField(plain_max_length=64)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(default=0)
    backup_code_hashes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Two-factor authentication"
        verbose_name_plural = "Two-factor authentication"

    def __str__(self) -> str:
        state = "on" if self.confirmed_at else "setup"
        return f"TwoFactorAuth(user={self.user_id}, {state})"

    @property
    def is_enabled(self) -> bool:
        return self.confirmed_at is not None

    @property
    def backup_codes_left(self) -> int:
        return len(self.backup_code_hashes or [])
