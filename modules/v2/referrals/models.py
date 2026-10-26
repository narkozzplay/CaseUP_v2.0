from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class ReferralCode(models.Model):
    """A user's personal invite code: case-up.ru/r/<code>/."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_code")
    code = models.CharField(max_length=16, unique=True)
    clicks = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Referral code"
        verbose_name_plural = "Referral codes"

    def __str__(self) -> str:
        return f"{self.code} ({self.user_id})"


class Referral(models.Model):
    """One invited account. It counts for the referrer once the invited user has topped up the
    qualifying amount with real money (``qualified_at``) and the review state is OK.

    Accounts sharing an IP with the referrer are put on review (FLAGGED) and do not count until an
    admin marks them clean; BLOCKED never counts.
    """

    class Review(models.TextChoices):
        OK = "ok", "Засчитывается"
        FLAGGED = "flagged", "На проверке"
        BLOCKED = "blocked", "Отклонён"

    referrer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrals_made")
    referee = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referred_by")
    code = models.CharField(max_length=16, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    qualified_at = models.DateTimeField(null=True, blank=True)
    review = models.CharField(max_length=16, choices=Review.choices, default=Review.OK)
    flag_reason = models.CharField(max_length=255, blank=True, default="")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="referrals_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True, default="")
    bonus_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Referral"
        verbose_name_plural = "Referrals"
        indexes = [
            models.Index(fields=["referrer", "qualified_at"], name="ref_referrer_qualified_idx"),
            models.Index(fields=["review", "created_at"], name="ref_review_idx"),
        ]

    def __str__(self) -> str:
        return f"Referral({self.referrer_id} → {self.referee_id}, {self.review})"

    @property
    def is_qualified(self) -> bool:
        return self.qualified_at is not None

    @property
    def counts(self) -> bool:
        return self.is_qualified and self.review == self.Review.OK


class ReferralBonus(models.Model):
    """Turnover bonus paid to the referrer for one item sale of an invited user (one per sale)."""

    referral = models.ForeignKey(Referral, on_delete=models.CASCADE, related_name="bonuses")
    source_entry_id = models.BigIntegerField(unique=True)
    sale_amount = models.DecimalField(max_digits=14, decimal_places=2)
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    ledger_entry = models.OneToOneField(
        "profiles.LedgerEntry", on_delete=models.SET_NULL, null=True, blank=True, related_name="referral_bonus"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Referral bonus"
        verbose_name_plural = "Referral bonuses"

    def __str__(self) -> str:
        return f"ReferralBonus({self.referral_id}, {self.amount})"


class ReferralProgramSettings(models.Model):
    """Singleton (pk=1): the two-sided welcome bonus. Edited in Control Center → Рефералы."""

    welcome_bonus_enabled = models.BooleanField("Бонус «+дни подписки» обеим сторонам", default=True)
    welcome_bonus_days = models.PositiveSmallIntegerField(
        "Сколько дней дарить", default=7, validators=[MinValueValidator(1), MaxValueValidator(90)]
    )
    welcome_bonus_plan = models.ForeignKey(
        "subscriptions.SubscriptionPlan",
        verbose_name="Тариф бонуса",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Пусто — «Плюс» (site_plus).",
    )
    referrer_monthly_limit = models.PositiveSmallIntegerField(
        "Сколько бонусов пригласивший может получить за 30 дней", default=10, help_text="0 — без лимита."
    )
    # Referrals created before this moment (before the program started) do not get the bonus.
    welcome_bonus_since = models.DateTimeField("Бонус для приглашений начиная с", default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Referral program settings"
        verbose_name_plural = "Referral program settings"

    def __str__(self) -> str:
        return "Настройки реферальной программы"

    @classmethod
    def load(cls) -> "ReferralProgramSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ReferralWelcomeBonus(models.Model):
    """Free days of a subscription for one side of a referral, given after the invited user's first
    Steam sign-in. One per invited Steam account and side, ever — deleting and re-creating the account
    does not bring the bonus back (``steam_id`` stays, the user link is nulled)."""

    class Role(models.TextChoices):
        REFERRER = "referrer", "Пригласивший"
        REFEREE = "referee", "Приглашённый"

    class Status(models.TextChoices):
        GRANTED = "granted", "Начислен"
        HELD = "held", "На проверке"
        DENIED = "denied", "Не начислен"

    referral = models.ForeignKey(Referral, on_delete=models.SET_NULL, null=True, blank=True, related_name="welcome_bonuses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="referral_welcome_bonuses")
    role = models.CharField(max_length=16, choices=Role.choices)
    steam_id = models.CharField("Steam ID приглашённого", max_length=32)
    days = models.PositiveSmallIntegerField()
    plan = models.ForeignKey("subscriptions.SubscriptionPlan", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.HELD, db_index=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    mode = models.CharField(max_length=16, blank=True, default="")
    converted_days = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    granted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Referral welcome bonus"
        verbose_name_plural = "Referral welcome bonuses"
        constraints = [models.UniqueConstraint(fields=["steam_id", "role"], name="ref_welcome_steam_role_uniq")]
        indexes = [models.Index(fields=["user", "role", "granted_at"], name="ref_welcome_user_idx")]

    def __str__(self) -> str:
        return f"WelcomeBonus({self.role}, {self.user_id}, {self.status})"
