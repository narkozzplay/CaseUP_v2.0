from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db import models
from django.templatetags.static import static

ZERO = Decimal("0.00")
BUNDLED_COVER_PATH = "img/cases/{slug}.webp"


@lru_cache(maxsize=64)
def bundled_cover_url(slug: str) -> str:
    """Cover shipped with the project in static/img/cases/, if there is one for this slug."""
    path = BUNDLED_COVER_PATH.format(slug=slug)
    return static(path) if finders.find(path) else ""


class SimCase(models.Model):
    class Kind(models.TextChoices):
        OFFICIAL = "official", "Кейс CS2"
        CUSTOM = "custom", "Кейс CaseUP"
        TERMINAL = "terminal", "Терминал"

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CUSTOM, db_index=True)
    description = models.TextField(blank=True, default="")
    collection_name = models.CharField(max_length=160, blank=True, default="")

    case_price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    key_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        help_text="0 — кейс открывается без ключа.",
    )

    image_url = models.URLField(max_length=1000, blank=True, default="")
    image = models.ImageField(upload_to="case_simulator/cases/", null=True, blank=True)
    accent_color = models.CharField(max_length=16, blank=True, default="#f5a623")

    terminal_offers = models.PositiveSmallIntegerField(default=5, help_text="Сколько предложений показывает терминал.")
    terminal_markup_percent = models.PositiveSmallIntegerField(
        default=10,
        help_text="Наценка терминала к стоимости предмета, %.",
    )

    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=100, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("kind", "sort_order", "id")
        verbose_name = "Кейс симулятора"
        verbose_name_plural = "Кейсы симулятора"

    def __str__(self) -> str:
        return self.name

    @property
    def has_key(self) -> bool:
        return self.key_price > ZERO

    @property
    def open_price(self) -> Decimal:
        return (self.case_price or ZERO) + (self.key_price or ZERO)

    @property
    def cover_url(self) -> str:
        if self.image:
            return self.image.url
        return self.image_url or bundled_cover_url(self.slug)

    @property
    def is_terminal(self) -> bool:
        return self.kind == self.Kind.TERMINAL


class SimCaseItem(models.Model):
    class Rarity(models.TextChoices):
        CONSUMER = "consumer", "Ширпотреб"
        INDUSTRIAL = "industrial", "Промышленное"
        MILSPEC = "milspec", "Армейское"
        RESTRICTED = "restricted", "Запрещённое"
        CLASSIFIED = "classified", "Засекреченное"
        COVERT = "covert", "Тайное"
        GOLD = "gold", "Редкий особый предмет"

    case = models.ForeignKey(SimCase, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=160)
    weapon = models.CharField(max_length=64, blank=True, default="")
    rarity = models.CharField(max_length=16, choices=Rarity.choices, default=Rarity.MILSPEC, db_index=True)
    image_url = models.URLField(max_length=1000, blank=True, default="")
    value = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO, help_text="Оценочная стоимость предмета.")
    weight = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Свой вес выпадения. Пусто — шанс считается по редкости, как в CS2.",
    )
    min_float = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    max_float = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    stattrak_allowed = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("case", "sort_order", "id")
        verbose_name = "Предмет кейса"
        verbose_name_plural = "Предметы кейсов"

    def __str__(self) -> str:
        return f"{self.case}: {self.name}"

    @property
    def skin_name(self) -> str:
        return self.name.split(" | ", 1)[1] if " | " in self.name else self.name

    @property
    def weapon_label(self) -> str:
        if self.weapon:
            return self.weapon
        return self.name.split(" | ", 1)[0] if " | " in self.name else ""

    @property
    def rarity_css(self) -> str:
        return self.rarity

    @property
    def has_float(self) -> bool:
        return self.min_float is not None and self.max_float is not None


class SimOpening(models.Model):
    """One drop: from a case opening or an accepted terminal offer. Doubles as the simulator inventory."""

    class Source(models.TextChoices):
        CASE = "case", "Кейс"
        TERMINAL = "terminal", "Терминал"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sim_openings")
    case = models.ForeignKey(SimCase, on_delete=models.PROTECT, related_name="openings")
    item = models.ForeignKey(SimCaseItem, on_delete=models.PROTECT, related_name="openings")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.CASE)
    float_value = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    exterior = models.CharField(max_length=32, blank=True, default="")
    is_stattrak = models.BooleanField(default=False)
    price_paid = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    item_value = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self) -> str:
        return f"{self.user_id}: {self.display_name}"

    @property
    def display_name(self) -> str:
        prefix = "StatTrak™ " if self.is_stattrak else ""
        suffix = f" ({self.exterior})" if self.exterior else ""
        return f"{prefix}{self.item.name}{suffix}"


class TerminalSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Активен"
        ACCEPTED = "accepted", "Предложение принято"
        BURNED = "burned", "Сгорел"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="terminal_sessions")
    case = models.ForeignKey(SimCase, on_delete=models.PROTECT, related_name="terminal_sessions")
    offers = models.JSONField(default=list)
    current_index = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    price_paid = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    opening = models.OneToOneField(SimOpening, on_delete=models.SET_NULL, null=True, blank=True, related_name="terminal_session")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self) -> str:
        return f"Terminal {self.case_id} for {self.user_id}: {self.status}"

    @property
    def offers_left(self) -> int:
        return max(0, len(self.offers) - self.current_index)

    @property
    def current_offer(self) -> dict | None:
        if self.status != self.Status.ACTIVE or self.current_index >= len(self.offers):
            return None
        return self.offers[self.current_index]
