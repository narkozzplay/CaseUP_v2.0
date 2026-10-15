from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

CYCLE_DAYS = 7
ZERO = Decimal("0.00")


class DailyBonusSettings(models.Model):
    """Singleton (pk=1): the switch and who may claim."""

    is_enabled = models.BooleanField("Ежедневный бонус включён", default=True)
    require_steam = models.BooleanField("Только с привязанным Steam", default=True)
    require_topup = models.BooleanField("Только после первого пополнения", default=True)
    free_case_valid_days = models.PositiveSmallIntegerField("Сколько дней действует бесплатный кейс", default=7)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Daily bonus settings"
        verbose_name_plural = "Daily bonus settings"

    @classmethod
    def load(cls) -> "DailyBonusSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class DailyBonusReward(models.Model):
    """What day N of the 7-day cycle gives: balance bonus units or one free case opening."""

    class Kind(models.TextChoices):
        BALANCE = "balance", "Бонус на баланс"
        CASE = "case", "Бесплатный кейс"

    day = models.PositiveSmallIntegerField(unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.BALANCE)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO, help_text="Для бонуса на баланс; для кейса — запасная сумма, если кейс недоступен.")
    case = models.ForeignKey("case_simulator.SimCase", on_delete=models.SET_NULL, null=True, blank=True, related_name="daily_bonus_rewards")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("day",)
        verbose_name = "Daily bonus reward"
        verbose_name_plural = "Daily bonus rewards"

    def __str__(self) -> str:
        return f"День {self.day}: {self.label}"

    def clean(self):
        super().clean()
        if not 1 <= self.day <= CYCLE_DAYS:
            raise ValidationError({"day": f"День должен быть от 1 до {CYCLE_DAYS}."})
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "Сумма не может быть отрицательной."})

    @property
    def gives_case(self) -> bool:
        return self.kind == self.Kind.CASE and self.case is not None and self.case.is_active and not self.case.is_terminal

    @property
    def label(self) -> str:
        if self.gives_case:
            return f"Кейс «{self.case.name}»"
        return f"{self.amount:.0f} ₽" if self.amount == self.amount.to_integral() else f"{self.amount:.2f} ₽"


class DailyBonusState(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_bonus_state")
    cycle_day = models.PositiveSmallIntegerField(default=0, help_text="День цикла (1–7), полученный последним.")
    streak = models.PositiveIntegerField(default=0, help_text="Дней подряд без пропусков.")
    best_streak = models.PositiveIntegerField(default=0)
    last_claim_date = models.DateField(null=True, blank=True, db_index=True)
    claims_total = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Daily bonus state"
        verbose_name_plural = "Daily bonus states"


class DailyBonusClaim(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_bonus_claims")
    date = models.DateField(db_index=True)
    day = models.PositiveSmallIntegerField()
    streak = models.PositiveIntegerField(default=1)
    kind = models.CharField(max_length=16, choices=DailyBonusReward.Kind.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    case = models.ForeignKey("case_simulator.SimCase", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    ledger_entry_id = models.BigIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-date", "-id")
        verbose_name = "Daily bonus claim"
        verbose_name_plural = "Daily bonus claims"
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="daily_bonus_one_claim_per_day")]

    @property
    def label(self) -> str:
        if self.kind == DailyBonusReward.Kind.CASE and self.case_id:
            return f"Кейс «{self.case.name}»"
        return f"{self.amount:.2f} ₽"


class DailyFreeCase(models.Model):
    """One free opening of a case, won on a «case» day; used by the next opening of that case."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт открытия"
        USED = "used", "Открыт"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_free_cases")
    case = models.ForeignKey("case_simulator.SimCase", on_delete=models.CASCADE, related_name="daily_free_cases")
    claim = models.OneToOneField(DailyBonusClaim, on_delete=models.CASCADE, related_name="free_case")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("expires_at", "id")
        verbose_name = "Daily free case"
        verbose_name_plural = "Daily free cases"

    @property
    def is_usable(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
