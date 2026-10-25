from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

ZERO = Decimal("0.00")
HEX_COLOR = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Цвет в формате #RRGGBB.")


class DonationGoal(models.Model):
    """Singleton (pk=1): the current fundraiser and how its widget looks. Everything is set in Control Center."""

    class Variant(models.TextChoices):
        CARD = "card", "Карточка"
        BANNER = "banner", "Широкий баннер"
        COMPACT = "compact", "Компактная полоска"

    class Background(models.TextChoices):
        THEME = "theme", "Как у сайта"
        TINT = "tint", "Подкрашенный акцентом"
        GRADIENT = "gradient", "Градиент"

    is_enabled = models.BooleanField("Сбор включён", default=False)
    title = models.CharField("Название", max_length=120, default="Поддержите развитие CaseUP")
    description = models.TextField("Описание", max_length=1000, blank=True, default="Каждый рубль идёт на серверы, новые функции и обновление цен быстрее.")
    goal_amount = models.DecimalField("Цель, ₽", max_digits=14, decimal_places=2, default=Decimal("10000.00"))
    # Money raised elsewhere (card transfer, streams) that should still count towards the goal.
    collected_offset = models.DecimalField("Собрано вне сайта, ₽", max_digits=14, decimal_places=2, default=ZERO)
    # Donations made since this moment count towards the current goal; «новый сбор» moves it.
    started_at = models.DateTimeField("Начало сбора", default=timezone.now)
    deadline = models.DateField("Собрать до", null=True, blank=True)

    preset_amounts = models.CharField("Быстрые суммы", max_length=120, default="50,100,300,500")
    min_amount = models.DecimalField("Минимальный донат, ₽", max_digits=10, decimal_places=2, default=Decimal("10.00"))
    max_amount = models.DecimalField("Максимальный донат, ₽", max_digits=14, decimal_places=2, default=Decimal("100000.00"))
    button_text = models.CharField("Текст кнопки", max_length=40, default="Поддержать")
    thank_you_text = models.CharField("Благодарность после доната", max_length=200, default="Спасибо за поддержку проекта!")
    allow_messages = models.BooleanField("Разрешить комментарий к донату", default=True)

    variant = models.CharField("Вид", max_length=16, choices=Variant.choices, default=Variant.CARD)
    background = models.CharField("Фон", max_length=16, choices=Background.choices, default=Background.TINT)
    accent_color = models.CharField("Цвет полосы", max_length=7, default="#f5a623", validators=[HEX_COLOR])
    accent_color_end = models.CharField("Второй цвет градиента", max_length=7, default="#ff7a1a", validators=[HEX_COLOR])
    icon = models.CharField("Значок", max_length=8, blank=True, default="❤")
    show_percent = models.BooleanField("Показывать процент", default=True)
    show_goal = models.BooleanField("Показывать сумму цели", default=True)
    show_donors_count = models.BooleanField("Показывать число донатеров", default=True)
    show_recent_donors = models.BooleanField("Показывать последние донаты", default=True)

    show_on_home = models.BooleanField("На главной странице", default=True)
    show_on_dashboard = models.BooleanField("На странице Dashboard", default=True)
    show_in_sidebar = models.BooleanField("В боковом меню кабинета", default=False)
    show_in_menu = models.BooleanField("Пункт «Поддержать проект» в меню", default=True)

    # Sponsor role: the month's top donors above sponsor_monthly_min get it for the next month,
    # everyone whose donations ever total more than sponsor_lifetime_min gets it for good. An admin grants it.
    sponsors_enabled = models.BooleanField("Роль «Спонсор» включена", default=True)
    sponsor_top_size = models.PositiveSmallIntegerField("Сколько донатеров в топе месяца", default=3)
    sponsor_monthly_min = models.DecimalField("Порог за месяц, ₽", max_digits=14, decimal_places=2, default=Decimal("5000.00"))
    sponsor_lifetime_min = models.DecimalField("Порог за всё время, ₽", max_digits=14, decimal_places=2, default=Decimal("10000.00"))
    sponsor_notify_donor = models.BooleanField("Сообщать донатеру о выдаче роли", default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Donation goal"
        verbose_name_plural = "Donation goal"

    def __str__(self) -> str:
        return self.title

    @classmethod
    def load(cls) -> "DonationGoal":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def presets(self) -> list[Decimal]:
        values = []
        for chunk in (self.preset_amounts or "").split(","):
            chunk = chunk.strip()
            if chunk:
                try:
                    values.append(Decimal(chunk).quantize(Decimal("0.01")))
                except Exception:  # noqa: BLE001 - a bad chunk is skipped; the form validates on save
                    continue
        return values


class Donation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="donations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    message = models.CharField(max_length=200, blank=True, default="")
    is_anonymous = models.BooleanField(default=False)
    goal_title = models.CharField(max_length=120, blank=True, default="")
    ledger_entry_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Donation"
        verbose_name_plural = "Donations"

    def __str__(self) -> str:
        return f"Donation({self.user_id}, {self.amount})"


class Sponsorship(models.Model):
    """The «Спонсор» role: a candidate found by the monthly top or the lifetime total, then granted by an admin."""

    class Kind(models.TextChoices):
        MONTHLY = "monthly", "Топ месяца"
        LIFETIME = "lifetime", "За всё время"

    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Ждёт решения"
        GRANTED = "granted", "Выдана"
        DECLINED = "declined", "Отклонена"
        REVOKED = "revoked", "Снята"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sponsorships")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # First day of the month the donations were made in; empty for the lifetime role.
    period = models.DateField(null=True, blank=True)
    amount = models.DecimalField("Сумма донатов", max_digits=14, decimal_places=2, default=ZERO)
    rank = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CANDIDATE, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Sponsorship"
        verbose_name_plural = "Sponsorships"
        constraints = [
            models.UniqueConstraint(fields=["user", "kind", "period"], name="donations_sponsorship_once_per_period"),
            models.UniqueConstraint(fields=["user"], condition=models.Q(kind="lifetime"), name="donations_sponsorship_one_lifetime"),
        ]

    def __str__(self) -> str:
        return f"Sponsorship({self.user_id}, {self.kind}, {self.period}, {self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.GRANTED and (self.expires_at is None or self.expires_at > timezone.now())
