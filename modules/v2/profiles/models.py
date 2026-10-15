from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


User = settings.AUTH_USER_MODEL


class UserRole(models.TextChoices):
    USER = "user", "User"
    FARMER = "farmer", "Farmer"
    MOD = "mod", "Moderator"
    ADMIN = "admin", "Admin"
    CHIEF_ADMIN = "chief_admin", "Chief admin"
    SUPER_ADMIN = "super_admin", "Super admin"


class VerificationLevel(models.TextChoices):
    NONE = "none", "—"
    VERIFIED = "verified", "Verified"
    FARMER = "farmer", "Farmer"
    LEGEND = "legend", "Legend CaseUP"


class UserProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile_v2",
        related_query_name="profile_v2",
    )
    role = models.CharField(
        max_length=16,
        choices=UserRole.choices,
        default=UserRole.USER,
    )
    balance_coins = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    steam_id = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        unique=True,
    )
    steam_personaname = models.CharField(
        max_length=80,
        blank=True,
        default="",
    )
    steam_avatar = models.URLField(
        blank=True,
        default="",
    )
    uploaded_avatar = models.ImageField(
        upload_to="profiles/avatars/",
        blank=True,
        null=True,
    )
    steam_profile_url = models.URLField(
        blank=True,
        default="",
    )
    steam_profile_background = models.URLField(
        blank=True,
        default="",
    )
    steam_profile_background_video = models.URLField(
        blank=True,
        default="",
    )
    steam_background_synced_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    steam_level = models.PositiveIntegerField(default=0)
    steam_level_synced_at = models.DateTimeField(null=True, blank=True)

    faceit_player_id = models.CharField(max_length=64, blank=True, default="")
    faceit_nickname = models.CharField(max_length=128, blank=True, default="")
    faceit_avatar_url = models.URLField(blank=True, default="")
    faceit_skill_level = models.PositiveSmallIntegerField(default=0)
    faceit_elo = models.PositiveIntegerField(default=0)
    faceit_level_image_url = models.URLField(blank=True, default="")
    faceit_last_sync_at = models.DateTimeField(null=True, blank=True)
    faceit_last_error = models.TextField(blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)

    telegram_chat_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
    )
    telegram_username = models.CharField(
        max_length=128,
        blank=True,
        default="",
    )
    telegram_first_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
    )
    telegram_linked_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    telegram_last_interaction_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    theme_preferences = models.JSONField(
        default=dict,
        blank=True,
    )
    module_preferences = models.JSONField(
        default=dict,
        blank=True,
    )
    is_store_author = models.BooleanField(default=False, db_index=True)

    verification_level = models.CharField(
        max_length=16,
        choices=VerificationLevel.choices,
        default=VerificationLevel.NONE,
        db_index=True,
    )

    # Customization cache — обновляется через profile_customization.services
    selected_avatar_url = models.URLField(
        blank=True,
        default="",
        help_text="Кешированный URL аватарки из каталога кастомизации",
    )
    selected_bg_static_url = models.URLField(
        blank=True,
        default="",
        help_text="Кешированный URL статичного фона из каталога",
    )
    selected_bg_video_url = models.URLField(
        blank=True,
        default="",
        help_text="Кешированный URL видео-фона из каталога",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"
        constraints = [
            models.CheckConstraint(
                condition=Q(balance_coins__gte=Decimal("0.00")),
                name="profiles_userprofile_balance_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"Profile({self.user})"

    @property
    def telegram_is_linked(self) -> bool:
        return bool(self.telegram_chat_id)


class LedgerReason(models.TextChoices):
    TOPUP = "topup", "Top up"
    CASE_OPEN = "case_open", "Case open"
    ITEM_SALE = "item_sale", "Item sale"
    WITHDRAW_LOCK = "withdraw_lock", "Withdraw lock"
    WITHDRAW_CANCEL = "withdraw_cancel", "Withdraw cancel"
    ADMIN_ADJUST = "admin_adjust", "Admin adjust"
    SUBSCRIPTION_PURCHASE = "subscription_purchase", "Subscription purchase"
    THEME_PURCHASE = "theme_purchase", "Theme purchase"
    STORE_PURCHASE = "store_purchase", "Store purchase"
    BUNDLE_PURCHASE = "bundle_purchase", "Bundle purchase"
    REFERRAL_BONUS = "referral_bonus", "Referral bonus"


class LedgerEntry(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    delta = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)

    reason = models.CharField(max_length=32, choices=LedgerReason.choices)
    amount_snapshot = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )

    case_opening_id = models.BigIntegerField(null=True, blank=True)
    inventory_item_id = models.BigIntegerField(null=True, blank=True)
    withdraw_request_id = models.BigIntegerField(null=True, blank=True)

    idempotency_key = models.CharField(
        max_length=128,
        null=True,
        blank=True,
    )
    comment = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ledger entry"
        verbose_name_plural = "Ledger entries"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                name="profiles_ledger_user_idempotency_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["reason", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"LedgerEntry(user={self.user_id}, delta={self.delta}, reason={self.reason})"


class VerificationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DENIED = "denied", "Denied"

    class RequestedLevel(models.TextChoices):
        VERIFIED = "verified", "Verified"
        FARMER = "farmer", "Farmer"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="verification_requests",
    )
    requested_level = models.CharField(
        max_length=16,
        choices=RequestedLevel.choices,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    user_message = models.TextField(blank=True, default="")
    admin_note = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_verification_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Verification request"
        verbose_name_plural = "Verification requests"
        indexes = [
            models.Index(fields=["user", "status"], name="prf_verreq_user_status_idx"),
            models.Index(fields=["status", "created_at"], name="prf_verreq_status_idx"),
        ]

    def __str__(self) -> str:
        return f"VerificationRequest({self.user_id}, {self.requested_level}, {self.status})"
