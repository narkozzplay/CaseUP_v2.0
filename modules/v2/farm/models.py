from __future__ import annotations

import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from modules.v2.steam_catalog.models import SteamItem


ZERO_MONEY = Decimal("0.00")

EXTERIOR_CODES = {
    "Factory New": "FN",
    "Minimal Wear": "MW",
    "Field-Tested": "FT",
    "Well-Worn": "WW",
    "Battle-Scarred": "BS",
}
EXTERIOR_SUFFIX_RE = re.compile(r"\s*\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)\s*$")

STATUS_LABELS_RU = {
    "active": "Активен",
    "paused": "Пауза",
    "banned": "Бан",
    "review": "Проверка",
}


class FarmAccount(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        BANNED = "banned", "Banned"
        REVIEW = "review", "Review"

    class Role(models.TextChoices):
        # Farm bots collect weekly drops; a storage account receives them
        # (target for the future drop auto-send between accounts).
        FARM = "farm", "Фарм-бот"
        STORAGE = "storage", "Склад"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="farm_accounts",
    )
    title = models.CharField(max_length=120)
    steam_id64 = models.CharField(max_length=32, blank=True, default="", db_index=True)
    trade_url = models.URLField(blank=True, default="")
    login_hint = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REVIEW, db_index=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.FARM, db_index=True)
    persona_name = models.CharField(max_length=120, blank=True, default="")
    avatar_url = models.URLField(max_length=500, blank=True, default="")
    profile_synced_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.owner_id})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("farm:accounts_detail", args=[self.id])

    @property
    def steam_profile_url(self) -> str:
        if not self.steam_id64:
            return ""
        return f"https://steamcommunity.com/profiles/{self.steam_id64}"

    @property
    def initials(self) -> str:
        value = (self.title or self.persona_name or "?").strip()
        digits = re.search(r"(\d{1,3})\s*$", value)
        if digits:
            return digits.group(1)[-2:]
        return value[:2].upper()

    @property
    def status_label(self) -> str:
        return STATUS_LABELS_RU.get(self.status, self.get_status_display())


class FarmAccountInventorySync(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    account = models.ForeignKey(
        FarmAccount,
        on_delete=models.CASCADE,
        related_name="inventory_sync_runs",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    total_assets = models.PositiveIntegerField(default=0)
    total_sellable = models.PositiveIntegerField(default=0)
    error_text = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["account", "status"]),
            models.Index(fields=["account", "started_at"]),
        ]

    def __str__(self) -> str:
        return f"InventorySync(account={self.account_id}, status={self.status})"


class FarmAccountInventoryItem(models.Model):
    class SaleState(models.TextChoices):
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        SOLD = "sold", "Sold"

    account = models.ForeignKey(
        FarmAccount,
        on_delete=models.CASCADE,
        related_name="inventory_items",
    )
    sync_run = models.ForeignKey(
        FarmAccountInventorySync,
        on_delete=models.SET_NULL,
        related_name="items",
        null=True,
        blank=True,
    )

    asset_id = models.CharField(max_length=64)
    class_id = models.CharField(max_length=64, blank=True, default="")
    instance_id = models.CharField(max_length=64, blank=True, default="")
    amount = models.PositiveIntegerField(default=1)

    market_hash_name = models.CharField(max_length=255, db_index=True)
    icon_url = models.TextField(blank=True, default="")
    rarity_color = models.CharField(max_length=8, blank=True, default="")
    float_value = models.FloatField(null=True, blank=True)

    tradable = models.BooleanField(default=False, db_index=True)
    marketable = models.BooleanField(default=False, db_index=True)

    matched_item = models.ForeignKey(
        SteamItem,
        on_delete=models.SET_NULL,
        related_name="farm_inventory_matches",
        null=True,
        blank=True,
    )

    catalog_price_rub_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payout_price_rub_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    is_sellable = models.BooleanField(default=False, db_index=True)
    sell_block_reason = models.CharField(max_length=255, blank=True, default="")
    is_missing = models.BooleanField(default=False, db_index=True)

    sale_state = models.CharField(
        max_length=16,
        choices=SaleState.choices,
        default=SaleState.AVAILABLE,
        db_index=True,
    )
    sale_locked_at = models.DateTimeField(null=True, blank=True)
    sold_at = models.DateTimeField(null=True, blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_sellable", "market_hash_name", "asset_id"]
        constraints = [
            models.UniqueConstraint(fields=["account", "asset_id"], name="farm_unique_account_asset_id"),
        ]
        indexes = [
            models.Index(fields=["account", "is_missing", "is_sellable"]),
            models.Index(fields=["account", "market_hash_name"]),
            models.Index(fields=["account", "last_seen_at"]),
            models.Index(fields=["account", "sale_state", "last_seen_at"]),
        ]

    @property
    def icon_cdn(self) -> str:
        if not self.icon_url:
            return ""

        value = self.icon_url.strip()
        if value.startswith("http://") or value.startswith("https://"):
            return value

        return f"https://community.cloudflare.steamstatic.com/economy/image/{value}/96fx96f"

    @property
    def icon_large(self) -> str:
        value = (self.icon_url or "").strip()
        if not value or value.startswith(("http://", "https://")):
            return value
        return f"https://community.cloudflare.steamstatic.com/economy/image/{value}/256fx256f"

    @property
    def name_parts(self) -> tuple[str, str]:
        """Split a market name into a small kicker and the main title, like
        "AK-47" / "Wintergreen" or "Sticker | Austin 2025" / "ropz (Champion)"."""
        name = EXTERIOR_SUFFIX_RE.sub("", self.market_hash_name or "").strip()
        parts = [part.strip() for part in name.split(" | ") if part.strip()]
        if len(parts) < 2:
            return "", name
        if len(parts) == 2:
            return parts[0], parts[1]
        return f"{parts[0]} | {' | '.join(parts[2:])}", parts[1]

    @property
    def exterior_code(self) -> str:
        match = EXTERIOR_SUFFIX_RE.search(self.market_hash_name or "")
        return EXTERIOR_CODES.get(match.group(1), "") if match else ""

    @property
    def block_label(self) -> str:
        if self.sale_state == self.SaleState.RESERVED:
            return "В продаже"
        if self.sale_state == self.SaleState.SOLD:
            return "Продан"
        if self.is_sellable:
            return ""
        if not self.tradable:
            return "Трейд-лок"
        if self.matched_item_id is None:
            return "Нет в каталоге"
        if self.catalog_price_rub_snapshot is None:
            return "Нет цены"
        return "Недоступен"

    @property
    def is_selectable(self) -> bool:
        return self.is_sellable and self.sale_state == self.SaleState.AVAILABLE

    @property
    def payout_price_safe(self) -> Decimal:
        return self.payout_price_rub_snapshot or ZERO_MONEY

    def __str__(self) -> str:
        return f"FarmInventoryItem(account={self.account_id}, asset={self.asset_id}, sellable={self.is_sellable})"


class ActiveDropPoolEntry(models.Model):
    ALLOWED_TYPES = (
        SteamItem.ItemType.CASE,
        SteamItem.ItemType.SKIN,
        SteamItem.ItemType.GRAFFITI,
    )

    item = models.ForeignKey(
        SteamItem,
        on_delete=models.CASCADE,
        related_name="active_drop_entries",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=100, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "item__market_hash_name"]
        constraints = [
            models.UniqueConstraint(fields=["item"], name="farm_unique_active_drop_pool_item"),
        ]
        indexes = [
            models.Index(fields=["is_active", "sort_order"]),
        ]

    def clean(self):
        super().clean()
        if self.item_id and self.item.item_type not in self.ALLOWED_TYPES:
            raise ValidationError({"item": "В активный пул можно добавлять только case / skin / graffiti."})

    def __str__(self) -> str:
        return f"DropPool: {self.item.market_hash_name}"


class WeeklyDropLog(models.Model):
    ALLOWED_TYPES = (
        SteamItem.ItemType.CASE,
        SteamItem.ItemType.SKIN,
        SteamItem.ItemType.GRAFFITI,
    )

    account = models.ForeignKey(
        FarmAccount,
        on_delete=models.CASCADE,
        related_name="weekly_drops",
    )
    week_index = models.PositiveIntegerField(db_index=True)
    week_start = models.DateField(db_index=True)
    week_end = models.DateField(db_index=True)

    item_1 = models.ForeignKey(
        SteamItem,
        on_delete=models.PROTECT,
        related_name="weekly_drops_as_first",
        null=True,
        blank=True,
    )
    item_2 = models.ForeignKey(
        SteamItem,
        on_delete=models.PROTECT,
        related_name="weekly_drops_as_second",
        null=True,
        blank=True,
    )

    notes = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="farm_weekly_drops_created",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_index", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["account", "week_index"], name="farm_unique_account_drop_week"),
        ]
        indexes = [
            models.Index(fields=["account", "week_index"]),
            models.Index(fields=["week_start", "week_end"]),
        ]

    def clean(self):
        super().clean()

        if not self.item_1 and not self.item_2:
            raise ValidationError("Нужно указать хотя бы один предмет дропа.")

        if self.item_1 and self.item_2 and self.item_1_id == self.item_2_id:
            raise ValidationError({"item_2": "Нельзя выбрать один и тот же предмет дважды."})

        for field_name in ("item_1", "item_2"):
            item = getattr(self, field_name)
            if item and item.item_type not in self.ALLOWED_TYPES:
                raise ValidationError({field_name: "Допустимы только case / skin / graffiti."})

        if self.week_end < self.week_start:
            raise ValidationError({"week_end": "week_end не может быть раньше week_start."})

    @property
    def week_month_label(self) -> str:
        from .services import get_drop_week_by_index

        return get_drop_week_by_index(self.week_index).month_year_label

    @property
    def week_range_label(self) -> str:
        return f"{self.week_start:%d.%m.%Y} — {self.week_end:%d.%m.%Y}"

    def __str__(self) -> str:
        return f"WeeklyDrop(account={self.account_id}, week={self.week_index})"


class FarmModuleConfig(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    default_caseup_fee_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("15.00"))
    subscribed_caseup_fee_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("7.00"))
    external_reference_fee_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("10.00"))
    price_up_threshold_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("12.00"))
    price_down_threshold_percent = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("10.00"))
    caseup_advantage_min_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("25.00"))
    milestone_notifications_enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Farm module config"
        verbose_name_plural = "Farm module config"

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self):
        errors: dict[str, str] = {}
        for field_name in (
            "default_caseup_fee_percent",
            "subscribed_caseup_fee_percent",
            "external_reference_fee_percent",
            "price_up_threshold_percent",
            "price_down_threshold_percent",
            "caseup_advantage_min_rub",
        ):
            if getattr(self, field_name) < ZERO_MONEY:
                errors[field_name] = "Значение не может быть отрицательным."
        if self.subscribed_caseup_fee_percent > self.default_caseup_fee_percent:
            errors["subscribed_caseup_fee_percent"] = "Подписочная комиссия не должна быть выше дефолтной."
        if errors:
            raise ValidationError(errors)

    @classmethod
    def bootstrap_defaults(cls) -> dict[str, object]:
        return {
            "default_caseup_fee_percent": Decimal("15.00"),
            "subscribed_caseup_fee_percent": Decimal("7.00"),
            "external_reference_fee_percent": Decimal("10.00"),
            "price_up_threshold_percent": Decimal("12.00"),
            "price_down_threshold_percent": Decimal("10.00"),
            "caseup_advantage_min_rub": Decimal("25.00"),
            "milestone_notifications_enabled": True,
            "notes": "Global farm analytics / notification / commission configuration.",
        }

    def __str__(self) -> str:
        return "Farm module config"


class FarmDropSession(models.Model):
    class SourceType(models.TextChoices):
        WEEKLY = "weekly", "Weekly drop"
        BONUS = "bonus", "Bonus"
        EVENT = "event", "Event"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        ARCHIVED = "archived", "Archived"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="farm_drop_sessions",
    )
    account = models.ForeignKey(
        FarmAccount,
        on_delete=models.CASCADE,
        related_name="drop_sessions",
    )
    source_type = models.CharField(max_length=16, choices=SourceType.choices, default=SourceType.WEEKLY, db_index=True)
    title = models.CharField(max_length=140, blank=True, default="")
    collected_at = models.DateTimeField(db_index=True)
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="farm_drop_sessions_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-collected_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "status", "collected_at"], name="farm_sess_usr_dt_idx"),
            models.Index(fields=["account", "collected_at"], name="farm_sess_acc_dt_idx"),
        ]

    def clean(self):
        super().clean()
        if self.account_id and self.owner_id and self.account.owner_id != self.owner_id:
            raise ValidationError({"account": "Farm session owner must match farm account owner."})

    def save(self, *args, **kwargs):
        if self.account_id and not self.owner_id:
            self.owner_id = self.account.owner_id
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def collected_label(self) -> str:
        return self.collected_at.strftime("%d.%m.%Y %H:%M")

    def __str__(self) -> str:
        return f"FarmDropSession({self.account_id}, {self.collected_at:%Y-%m-%d %H:%M})"


class FarmDropItem(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PARTIAL = "partial", "Partially sold"
        SOLD = "sold", "Sold"

    session = models.ForeignKey(
        FarmDropSession,
        on_delete=models.CASCADE,
        related_name="items",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="farm_drop_items",
    )
    steam_item = models.ForeignKey(
        SteamItem,
        on_delete=models.SET_NULL,
        related_name="farm_drop_items",
        null=True,
        blank=True,
    )
    name_snapshot = models.CharField(max_length=255)
    market_hash_name_snapshot = models.CharField(max_length=255, db_index=True)
    icon_url_snapshot = models.TextField(blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)
    remaining_quantity = models.PositiveIntegerField(default=1)
    collected_price_rub_snapshot = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    notes = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["market_hash_name_snapshot", "id"]
        indexes = [
            models.Index(fields=["session", "status"], name="farm_item_sess_st_idx"),
            models.Index(fields=["owner", "status"], name="farm_item_user_st_idx"),
            models.Index(fields=["market_hash_name_snapshot"], name="farm_item_name_idx"),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, str] = {}
        if self.quantity <= 0:
            errors["quantity"] = "Количество должно быть больше нуля."
        if self.remaining_quantity < 0:
            errors["remaining_quantity"] = "remaining_quantity не может быть отрицательным."
        if self.remaining_quantity > self.quantity:
            errors["remaining_quantity"] = "remaining_quantity не может быть больше quantity."
        if self.collected_price_rub_snapshot < ZERO_MONEY:
            errors["collected_price_rub_snapshot"] = "Цена не может быть отрицательной."
        if self.session_id and self.owner_id and self.session.owner_id != self.owner_id:
            errors["owner"] = "Owner must match session owner."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.session_id and not self.owner_id:
            self.owner_id = self.session.owner_id
        if not self.name_snapshot:
            self.name_snapshot = self.market_hash_name_snapshot
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def sold_quantity(self) -> int:
        return max(self.quantity - self.remaining_quantity, 0)

    @property
    def current_price_rub(self) -> Decimal:
        if self.steam_item_id and self.steam_item and self.steam_item.price_rub is not None:
            return self.steam_item.price_rub
        return self.collected_price_rub_snapshot or ZERO_MONEY

    @property
    def icon_cdn(self) -> str:
        value = (self.icon_url_snapshot or "").strip()
        if not value:
            return ""
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"https://community.cloudflare.steamstatic.com/economy/image/{value}/96fx96f"

    def __str__(self) -> str:
        return f"FarmDropItem({self.market_hash_name_snapshot}, qty={self.quantity}, remaining={self.remaining_quantity})"


class FarmDropSale(models.Model):
    class Platform(models.TextChoices):
        CASEUP = "caseup", "CaseUP"
        STEAM = "steam", "Steam"
        LISSKINS = "lisskins", "LisSkins"
        CSMONEY = "csmoney", "CS.MONEY"
        OTHER = "other", "Other"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="farm_drop_sales",
    )
    session = models.ForeignKey(
        FarmDropSession,
        on_delete=models.CASCADE,
        related_name="sales",
    )
    item = models.ForeignKey(
        FarmDropItem,
        on_delete=models.CASCADE,
        related_name="sales",
    )
    platform = models.CharField(max_length=16, choices=Platform.choices, db_index=True)
    sold_at = models.DateTimeField(db_index=True)
    quantity = models.PositiveIntegerField(default=1)
    gross_sale_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    fee_percent_snapshot = models.DecimalField(max_digits=6, decimal_places=2, default=ZERO_MONEY)
    fee_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    net_sale_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    cost_basis_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    profit_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    profit_percent = models.DecimalField(max_digits=7, decimal_places=2, default=ZERO_MONEY)
    commission_saved_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sold_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "platform", "sold_at"], name="farm_sale_usr_pl_idx"),
            models.Index(fields=["item", "platform"], name="farm_sale_item_pf_idx"),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, str] = {}
        if self.session_id and self.item_id and self.session_id != self.item.session_id:
            errors["item"] = "Sale item must belong to selected session."
        if self.quantity <= 0:
            errors["quantity"] = "Количество продажи должно быть больше нуля."
        if self.gross_sale_rub < ZERO_MONEY:
            errors["gross_sale_rub"] = "gross_sale_rub не может быть отрицательным."
        if self.fee_percent_snapshot < ZERO_MONEY:
            errors["fee_percent_snapshot"] = "fee_percent_snapshot не может быть отрицательным."
        if self.owner_id and self.item_id and self.owner_id != self.item.owner_id:
            errors["owner"] = "Owner must match farm item owner."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.item_id:
            self.session_id = self.item.session_id
            self.owner_id = self.item.owner_id
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"FarmDropSale(item={self.item_id}, platform={self.platform}, qty={self.quantity})"


class FarmerRewardTier(models.Model):
    code = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=120)
    threshold_turnover_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    reward_title = models.CharField(max_length=120, blank=True, default="")
    reward_description = models.TextField(blank=True, default="")
    bonus_value_rub = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    sort_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "threshold_turnover_rub", "id"]
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="farm_rw_tier_act_idx"),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, str] = {}
        if self.threshold_turnover_rub < ZERO_MONEY:
            errors["threshold_turnover_rub"] = "Порог не может быть отрицательным."
        if self.bonus_value_rub < ZERO_MONEY:
            errors["bonus_value_rub"] = "Бонус не может быть отрицательным."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.title} ({self.threshold_turnover_rub:.2f} ₽)"


class FarmerRewardGrant(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="farmer_reward_grants",
    )
    tier = models.ForeignKey(
        FarmerRewardTier,
        on_delete=models.PROTECT,
        related_name="grants",
    )
    turnover_rub_snapshot = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO_MONEY)
    granted_at = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-granted_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "tier"], name="farm_reward_once_uc"),
        ]
        indexes = [
            models.Index(fields=["owner", "granted_at"], name="farm_grant_usr_dt_idx"),
        ]

    def clean(self):
        super().clean()
        if self.turnover_rub_snapshot < ZERO_MONEY:
            raise ValidationError({"turnover_rub_snapshot": "Turnover snapshot cannot be negative."})

    def __str__(self) -> str:
        return f"RewardGrant(user={self.owner_id}, tier={self.tier_id})"


class FarmNotificationEvent(models.Model):
    class EventType(models.TextChoices):
        PRICE_UP = "price_up", "Price up"
        PRICE_DOWN = "price_down", "Price down"
        SALE = "sale", "Sale"
        MILESTONE = "milestone", "Milestone"
        CASEUP_ADVANTAGE = "caseup_advantage", "CaseUP advantage"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="farm_notification_events",
    )
    item = models.ForeignKey(
        FarmDropItem,
        on_delete=models.CASCADE,
        related_name="notification_events",
        null=True,
        blank=True,
    )
    sale = models.ForeignKey(
        FarmDropSale,
        on_delete=models.CASCADE,
        related_name="notification_events",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "event_type", "sent_at"], name="farm_evt_usr_tp_idx"),
            models.Index(fields=["item", "event_type"], name="farm_evt_item_tp_idx"),
        ]

    def __str__(self) -> str:
        return f"FarmNotificationEvent(user={self.owner_id}, type={self.event_type})"
