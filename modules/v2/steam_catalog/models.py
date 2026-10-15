from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import get_language


class SteamItem(models.Model):
    class ItemType(models.TextChoices):
        SKIN = "skin", "Skin"
        CASE = "case", "Case"
        STICKER = "sticker", "Sticker"
        GRAFFITI = "graffiti", "Graffiti"
        CHARM = "charm", "Charm"
        OTHER = "other", "Other"

    market_hash_name = models.CharField(max_length=255, unique=True)
    family_name = models.CharField(max_length=255, blank=True, default="", db_index=True)

    name = models.CharField(max_length=255, blank=True, default="")
    name_ru = models.CharField(max_length=255, blank=True, null=True, default=None)

    item_type = models.CharField(
        max_length=16,
        choices=ItemType.choices,
        default=ItemType.OTHER,
        db_index=True,
    )

    icon_url = models.TextField(blank=True, default="")
    rarity = models.CharField(max_length=64, blank=True, default="", db_index=True)
    rarity_key = models.CharField(max_length=24, blank=True, default="", db_index=True)
    exterior = models.CharField(max_length=32, blank=True, default="", db_index=True)
    weapon = models.CharField(max_length=64, blank=True, default="", db_index=True)

    is_stattrak = models.BooleanField(default=False, db_index=True)
    wear_order = models.PositiveSmallIntegerField(default=0, db_index=True)

    allow_buy = models.BooleanField(default=True, db_index=True)
    allow_sell = models.BooleanField(default=True, db_index=True)

    price_rub = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    stock = models.IntegerField(default=0, db_index=True)

    is_active = models.BooleanField(default=True, db_index=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    created_from_scan = models.BooleanField(default=False)
    # Last time the price queue asked Steam about this item (with or without a price in the answer).
    price_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def display_name(self) -> str:
        lang = (get_language() or "en")[:2]
        if lang == "ru" and self.name_ru:
            return self.name_ru
        return self.name or self.market_hash_name

    @property
    def group_name(self) -> str:
        if self.item_type == self.ItemType.SKIN and self.family_name:
            lang = (get_language() or "en")[:2]
            if lang == "ru" and self.name_ru:
                return self.name_ru
            return self.family_name
        return self.display_name

    def icon_cdn(self, size: int = 256) -> str:
        if not self.icon_url:
            return ""

        value = self.icon_url.strip()
        if value.startswith("http://") or value.startswith("https://"):
            return value

        return f"https://community.cloudflare.steamstatic.com/economy/image/{value}/{size}x{size}"

    def __str__(self) -> str:
        return self.market_hash_name

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["item_type", "is_active"]),
            models.Index(fields=["needs_review", "is_active"]),
            models.Index(fields=["allow_buy", "allow_sell", "is_active"]),
            models.Index(fields=["family_name", "item_type"]),
            models.Index(fields=["family_name", "wear_order", "is_stattrak"]),
        ]


class SteamItemPriceHistory(models.Model):
    item = models.ForeignKey(
        SteamItem,
        on_delete=models.CASCADE,
        related_name="price_history",
    )
    price_rub = models.DecimalField(max_digits=12, decimal_places=2)
    source = models.CharField(max_length=32, blank=True, default="manual")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["item", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"SteamItemPriceHistory(item={self.item_id}, price={self.price_rub}, source={self.source})"


class PriceDropAlertRule(models.Model):
    class NotifyTransport(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        INTERNAL = "internal", "Internal"

    class Direction(models.TextChoices):
        UP = "up", "Рост"
        DOWN = "down", "Падение"
        BOTH = "both", "Оба направления"

    item_type = models.CharField(
        max_length=16,
        choices=SteamItem.ItemType.choices,
        unique=True,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True, db_index=True)

    direction = models.CharField(
        max_length=8,
        choices=Direction.choices,
        default=Direction.BOTH,
        db_index=True,
    )
    lookback_hours = models.PositiveSmallIntegerField(default=4)
    min_drop_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("200.00"))
    min_drop_percent = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("10.00"))
    repeat_step_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("50.00"))
    repeat_step_percent = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("5.00"))
    cooldown_minutes = models.PositiveIntegerField(default=360)

    notify_transport = models.CharField(
        max_length=16,
        choices=NotifyTransport.choices,
        default=NotifyTransport.TELEGRAM,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["item_type"]
        verbose_name = "Price drop alert rule"
        verbose_name_plural = "Price drop alert rules"

    @property
    def min_change_rub(self) -> Decimal:
        return self.min_drop_rub

    @property
    def min_change_percent(self) -> Decimal:
        return self.min_drop_percent

    def supports_direction(self, direction: str) -> bool:
        return self.direction in {self.Direction.BOTH, direction}

    def __str__(self) -> str:
        return (
            f"{self.item_type} / {self.direction} / {self.lookback_hours}h / "
            f"{self.min_drop_rub} RUB / {self.min_drop_percent}%"
        )


class PriceDropAlertEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        IGNORED = "ignored", "Ignored"
        ERROR = "error", "Error"

    class Direction(models.TextChoices):
        UP = "up", "Рост"
        DOWN = "down", "Падение"

    rule = models.ForeignKey(
        PriceDropAlertRule,
        on_delete=models.CASCADE,
        related_name="events",
    )
    item = models.ForeignKey(
        SteamItem,
        on_delete=models.CASCADE,
        related_name="price_drop_alert_events",
    )

    notify_transport = models.CharField(
        max_length=16,
        choices=PriceDropAlertRule.NotifyTransport.choices,
        default=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        db_index=True,
    )
    trigger_source = models.CharField(max_length=16, blank=True, default="celery", db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    direction = models.CharField(
        max_length=8,
        choices=Direction.choices,
        default=Direction.DOWN,
        db_index=True,
    )
    window_started_at = models.DateTimeField()
    baseline_at = models.DateTimeField()
    current_at = models.DateTimeField()

    baseline_price_rub = models.DecimalField(max_digits=12, decimal_places=2)
    current_price_rub = models.DecimalField(max_digits=12, decimal_places=2)
    drop_rub = models.DecimalField(max_digits=12, decimal_places=2)
    drop_percent = models.DecimalField(max_digits=7, decimal_places=2)
    step_index = models.PositiveIntegerField(default=1, db_index=True)

    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="steam_catal_status_97a61f_idx"),
            models.Index(fields=["item", "created_at"], name="steam_catal_item_id_1d0b26_idx"),
            models.Index(fields=["rule", "created_at"], name="steam_catal_rule_id_0a4b50_idx"),
            models.Index(fields=["notify_transport", "status"], name="steam_catal_notify__aa9afc_idx"),
            models.Index(fields=["rule", "item", "direction", "created_at"], name="steam_catal_rule_id_433a09_idx"),
            models.Index(fields=["rule", "item", "direction", "step_index"], name="steam_catal_rule_id_56d007_idx"),
        ]
        verbose_name = "Price drop alert event"
        verbose_name_plural = "Price drop alert events"

    @property
    def absolute_change_rub(self) -> Decimal:
        return self.drop_rub.copy_abs()

    @property
    def absolute_change_percent(self) -> Decimal:
        return self.drop_percent.copy_abs()

    def __str__(self) -> str:
        return (
            f"{self.item.market_hash_name} / {self.direction} / step {self.step_index} / "
            f"{self.drop_rub} RUB / {self.drop_percent}% / {self.status}"
        )


class UserPriceAlertRule(models.Model):
    class ScopeType(models.TextChoices):
        ITEM = "item", "Конкретный предмет"
        FAMILY = "family", "Группа / family"
        ITEM_TYPE = "item_type", "Тип предмета"

    class Direction(models.TextChoices):
        UP = "up", "Рост"
        DOWN = "down", "Падение"
        BOTH = "both", "Оба направления"

    class ReferencePoint(models.TextChoices):
        PREVIOUS_POINT = "previous_point", "От предыдущей точки"
        WINDOW_START = "window_start", "От старта окна"
        WINDOW_MAX = "window_max", "От максимума за период"
        WINDOW_MIN = "window_min", "От минимума за период"
        LAST_LOCAL_MAX = "last_local_max", "От последнего локального максимума"
        LAST_LOCAL_MIN = "last_local_min", "От последнего локального минимума"
        LAST_EXTREME = "last_extreme", "От последней экстремальной точки"

    class ThresholdMode(models.TextChoices):
        RUB = "rub", "Только RUB"
        PERCENT = "percent", "Только %"
        AND = "and", "RUB и %"
        OR = "or", "RUB или %"

    class WindowKind(models.TextChoices):
        HOURS = "hours", "Последние N часов"
        DAYS = "days", "Последние N дней"
        POINTS = "points", "Последние N точек"

    class RepeatMode(models.TextChoices):
        ONCE = "once", "Один раз"
        STEP = "step", "Повторно по шагам"
        NEW_EXTREME = "new_extreme", "После нового экстремума"

    class Transport(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"

    class DeliveryTarget(models.TextChoices):
        USER_CHAT = "user_chat", "В привязанный chat_id пользователя"
        DEFAULT_CHAT = "default_chat", "В default_chat_id"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Активно"
        DISABLED = "disabled", "Выключено"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="steam_price_alert_rules")
    name = models.CharField(max_length=255)
    scope_type = models.CharField(max_length=16, choices=ScopeType.choices, default=ScopeType.ITEM_TYPE, db_index=True)
    item = models.ForeignKey(SteamItem, on_delete=models.CASCADE, related_name="user_alert_rules", null=True, blank=True)
    family_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    item_type = models.CharField(max_length=16, choices=SteamItem.ItemType.choices, blank=True, default="", db_index=True)
    direction = models.CharField(max_length=8, choices=Direction.choices, default=Direction.BOTH, db_index=True)
    reference_point = models.CharField(max_length=32, choices=ReferencePoint.choices, default=ReferencePoint.WINDOW_START, db_index=True)
    threshold_mode = models.CharField(max_length=16, choices=ThresholdMode.choices, default=ThresholdMode.AND)
    window_kind = models.CharField(max_length=16, choices=WindowKind.choices, default=WindowKind.HOURS)
    window_value = models.PositiveIntegerField(default=24)
    min_change_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    min_change_percent = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    cooldown_minutes = models.PositiveIntegerField(default=360)
    repeat_step_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    repeat_step_percent = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    repeat_mode = models.CharField(max_length=16, choices=RepeatMode.choices, default=RepeatMode.ONCE, db_index=True)
    transport = models.CharField(max_length=16, choices=Transport.choices, default=Transport.TELEGRAM, db_index=True)
    delivery_target = models.CharField(max_length=16, choices=DeliveryTarget.choices, default=DeliveryTarget.USER_CHAT, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user_id", "name", "id"]
        indexes = [
            models.Index(fields=["user", "status"], name="st_upar_usr_stat_idx"),
            models.Index(fields=["scope_type", "item_type"], name="st_upar_scope_type_idx"),
            models.Index(fields=["family_name", "status"], name="st_upar_fam_stat_idx"),
        ]

    def clean(self):
        super().clean()
        if self.scope_type == self.ScopeType.ITEM and not self.item_id:
            raise ValidationError({"item": "Выберите предмет."})
        if self.scope_type == self.ScopeType.FAMILY and not (self.family_name or "").strip():
            raise ValidationError({"family_name": "Укажите family/group name."})
        if self.scope_type == self.ScopeType.ITEM_TYPE and not (self.item_type or "").strip():
            raise ValidationError({"item_type": "Укажите тип предмета."})
        if self.window_value <= 0:
            raise ValidationError({"window_value": "Значение окна должно быть больше 0."})
        if self.min_change_rub <= Decimal("0") and self.min_change_percent <= Decimal("0"):
            raise ValidationError("Нужно указать порог RUB, %, либо оба.")
        if self.threshold_mode == self.ThresholdMode.RUB and self.min_change_rub <= Decimal("0"):
            raise ValidationError({"min_change_rub": "Для режима RUB нужен положительный порог."})
        if self.threshold_mode == self.ThresholdMode.PERCENT and self.min_change_percent <= Decimal("0"):
            raise ValidationError({"min_change_percent": "Для режима % нужен положительный порог."})
        if self.reference_point == self.ReferencePoint.LAST_LOCAL_MAX and self.direction == self.Direction.UP:
            raise ValidationError({"reference_point": "Локальный максимум применим для падения."})
        if self.reference_point == self.ReferencePoint.LAST_LOCAL_MIN and self.direction == self.Direction.DOWN:
            raise ValidationError({"reference_point": "Локальный минимум применим для роста."})

    @property
    def is_enabled(self) -> bool:
        return self.status == self.Status.ACTIVE

    def supports_direction(self, direction: str) -> bool:
        return self.direction in {self.Direction.BOTH, direction}

    def __str__(self) -> str:
        return f"{self.user_id}:{self.name}"


class UserPriceAlertEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        ERROR = "error", "Error"
        SKIPPED = "skipped", "Skipped"

    rule = models.ForeignKey(UserPriceAlertRule, on_delete=models.CASCADE, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="steam_price_alert_events")
    item = models.ForeignKey(SteamItem, on_delete=models.CASCADE, related_name="user_price_alert_events")
    transport = models.CharField(max_length=16, choices=UserPriceAlertRule.Transport.choices, default=UserPriceAlertRule.Transport.TELEGRAM, db_index=True)
    delivery_target = models.CharField(max_length=16, choices=UserPriceAlertRule.DeliveryTarget.choices, default=UserPriceAlertRule.DeliveryTarget.USER_CHAT, db_index=True)
    delivery_status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    trigger_source = models.CharField(max_length=16, blank=True, default="celery", db_index=True)
    direction = models.CharField(max_length=8, choices=PriceDropAlertEvent.Direction.choices, db_index=True)
    reference_point = models.CharField(max_length=32, choices=UserPriceAlertRule.ReferencePoint.choices, db_index=True)
    reference_at = models.DateTimeField()
    current_at = models.DateTimeField()
    window_started_at = models.DateTimeField()
    baseline_price_rub = models.DecimalField(max_digits=12, decimal_places=2)
    current_price_rub = models.DecimalField(max_digits=12, decimal_places=2)
    change_rub = models.DecimalField(max_digits=12, decimal_places=2)
    change_percent = models.DecimalField(max_digits=7, decimal_places=2)
    step_index = models.PositiveIntegerField(default=1, db_index=True)
    repeat_reason = models.CharField(max_length=32, blank=True, default="")
    sent_to = models.CharField(max_length=128, blank=True, default="")
    dedupe_key = models.CharField(max_length=255, unique=True)
    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "delivery_status"], name="st_upae_usr_dstat_idx"),
            models.Index(fields=["rule", "item", "direction"], name="steam_user_event_rule_item_idx"),
            models.Index(fields=["delivery_status", "created_at"], name="st_upae_dstat_cr_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.rule_id}:{self.item_id}:{self.direction}:{self.delivery_status}"


class StockReason(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "Income"
        OUT = "out", "Expense"

    name = models.CharField(max_length=64, unique=True)
    direction = models.CharField(max_length=8, choices=Direction.choices, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["direction", "sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class StockMovement(models.Model):
    item = models.ForeignKey(SteamItem, on_delete=models.CASCADE, related_name="stock_moves")
    delta = models.IntegerField()
    reason = models.ForeignKey(
        StockReason,
        on_delete=models.PROTECT,
        related_name="moves",
        null=True,
        blank=True,
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="steam_stock_moves_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["item", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def clean(self):
        super().clean()

        if self.delta == 0:
            raise ValidationError({"delta": "Delta cannot be 0."})

        if self.reason:
            if self.reason.direction == StockReason.Direction.IN and self.delta < 0:
                raise ValidationError({"delta": "Income reason expects positive delta."})
            if self.reason.direction == StockReason.Direction.OUT and self.delta > 0:
                raise ValidationError({"delta": "Expense reason expects negative delta."})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                prev = StockMovement.objects.select_for_update().get(pk=self.pk)
                diff = self.delta - prev.delta
            else:
                diff = self.delta

            item = SteamItem.objects.select_for_update().get(pk=self.item_id)
            new_stock = item.stock + diff
            if new_stock < 0:
                raise ValidationError("Stock cannot go below 0.")

            item.stock = new_stock
            item.save(update_fields=["stock", "updated_at"])

            super().save(*args, **kwargs)

class CS2ReferenceItem(models.Model):
    """Read-only CS2 item reference imported from an open dataset (ByMykel/CSGO-API)."""

    class Kind(models.TextChoices):
        SKIN = "skin", "Скин"
        CASE = "case", "Кейс"
        STICKER = "sticker", "Стикер"

    external_id = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    name = models.CharField(max_length=255, db_index=True)
    market_hash_name = models.CharField(max_length=255, blank=True, default="", db_index=True)

    weapon = models.CharField(max_length=64, blank=True, default="", db_index=True)
    category = models.CharField(max_length=64, blank=True, default="", db_index=True)
    pattern = models.CharField(max_length=128, blank=True, default="")

    rarity_name = models.CharField(max_length=64, blank=True, default="")
    rarity_key = models.CharField(max_length=24, blank=True, default="", db_index=True)
    rarity_color = models.CharField(max_length=16, blank=True, default="")

    collection_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    collections = models.JSONField(default=list, blank=True)
    crates = models.JSONField(default=list, blank=True)
    wears = models.JSONField(default=list, blank=True)

    min_float = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    max_float = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    has_stattrak = models.BooleanField(default=False)
    has_souvenir = models.BooleanField(default=False)

    image_url = models.URLField(max_length=1000, blank=True, default="")
    release_date = models.CharField(max_length=32, blank=True, default="")

    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "weapon", "name"]
        indexes = [
            models.Index(fields=["kind", "weapon"], name="steam_cat_ref_kind_weapon_idx"),
            models.Index(fields=["kind", "collection_name"], name="steam_cat_ref_kind_coll_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def rarity_label(self) -> str:
        from modules.v2.steam_catalog.services.helpers import rarity_ru_label_from_key

        if self.kind == self.Kind.CASE:
            return self.category or self.rarity_name
        return rarity_ru_label_from_key(self.rarity_key) or self.rarity_name or self.category

    @property
    def float_bar_style(self) -> str:
        if self.min_float is None or self.max_float is None:
            return ""
        left = max(0, min(100, float(self.min_float) * 100))
        width = max(1, min(100 - left, (float(self.max_float) - float(self.min_float)) * 100))
        return f"left: {left:.1f}%; width: {width:.1f}%;"

    @property
    def catalog_input_value(self) -> str:
        """Value for upsert_from_market_input: a concrete market hash name."""
        if self.kind == self.Kind.SKIN and self.wears:
            return f"{self.name} ({self.wears[0]})"
        return self.market_hash_name or self.name
