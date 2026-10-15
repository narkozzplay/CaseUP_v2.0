from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class PromoCode(models.Model):
    """A code users type in on the «Промокоды» page. What it gives depends on ``kind``:

    * BALANCE_BONUS — ``amount`` bonus units on the balance right away;
    * TOPUP_BONUS — ``percent`` extra on the next real top-up of at least ``min_topup`` (capped by ``max_bonus``);
    * SUBSCRIPTION_DISCOUNT — ``percent`` off one subscription purchase (optionally one plan family);
    * FREE_CASE — one free opening of ``case``.

    ``owner`` makes it a blogger code: a fresh account that activates it becomes the owner's referral.
    """

    class Kind(models.TextChoices):
        BALANCE_BONUS = "balance_bonus", "Бонус на баланс"
        TOPUP_BONUS = "topup_bonus", "Бонус к пополнению"
        SUBSCRIPTION_DISCOUNT = "subscription_discount", "Скидка на подписку"
        FREE_CASE = "free_case", "Бесплатный кейс"

    class Family(models.TextChoices):
        ANY = "", "Любые тарифы"
        SITE = "site", "Только тарифы сайта"
        FARMER = "farmer", "Только тарифы фермы"

    code = models.CharField(max_length=32, unique=True)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=120, blank=True, default="", help_text="Для себя: акция, блогер, стрим…")
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    percent = models.PositiveSmallIntegerField(null=True, blank=True)
    min_topup = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_bonus = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    subscription_family = models.CharField(max_length=16, choices=Family.choices, blank=True, default="")
    case = models.ForeignKey("case_simulator.SimCase", on_delete=models.SET_NULL, null=True, blank=True, related_name="promo_codes")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_promo_codes",
        help_text="Блогер: новые аккаунты, активировавшие код, становятся его рефералами.",
    )
    max_activations = models.PositiveIntegerField(null=True, blank=True, help_text="Пусто — без ограничения.")
    activations_count = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    only_new_accounts = models.BooleanField(default=False)
    only_without_topups = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Promo code"
        verbose_name_plural = "Promo codes"

    def __str__(self) -> str:
        return self.code

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_exhausted(self) -> bool:
        return self.max_activations is not None and self.activations_count >= self.max_activations

    @property
    def benefit_label(self) -> str:
        if self.kind == self.Kind.BALANCE_BONUS:
            return f"+{self.amount or Decimal('0'):.0f} ₽ на баланс"
        if self.kind == self.Kind.TOPUP_BONUS:
            text = f"+{self.percent or 0}% к пополнению"
            if self.min_topup:
                text += f" от {self.min_topup:.0f} ₽"
            if self.max_bonus:
                text += f" (до {self.max_bonus:.0f} ₽)"
            return text
        if self.kind == self.Kind.SUBSCRIPTION_DISCOUNT:
            scope = {"site": " на тарифы сайта", "farmer": " на тарифы фермы"}.get(self.subscription_family, " на подписку")
            return f"−{self.percent or 0}%{scope}"
        if self.kind == self.Kind.FREE_CASE:
            return f"Бесплатный кейс «{self.case.name}»" if self.case_id else "Бесплатный кейс"
        return self.get_kind_display()


class PromoActivation(models.Model):
    """One user's use of one code. Codes that pay later (top-up, subscription, case) stay PENDING
    until used; a balance bonus is USED immediately."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт использования"
        USED = "used", "Использован"
        CANCELED = "canceled", "Отменён"

    promo = models.ForeignKey(PromoCode, on_delete=models.CASCADE, related_name="activations")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="promo_activations")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    benefit_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    ledger_entry = models.OneToOneField(
        "profiles.LedgerEntry", on_delete=models.SET_NULL, null=True, blank=True, related_name="promo_activation"
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Promo activation"
        verbose_name_plural = "Promo activations"
        constraints = [models.UniqueConstraint(fields=["promo", "user"], name="promo_activation_once_per_user")]
        indexes = [models.Index(fields=["user", "status"], name="promo_act_user_status_idx")]

    def __str__(self) -> str:
        return f"{self.promo_id} → {self.user_id} ({self.status})"

    @property
    def is_usable(self) -> bool:
        return self.status == self.Status.PENDING and not self.promo.is_expired
