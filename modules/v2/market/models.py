from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class MarketSeller(models.Model):
    """A user who sells on the market: the Steam trade link buyers' items go through."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="market_seller")
    trade_url = models.URLField(max_length=255, blank=True, default="")
    trade_url_updated_at = models.DateTimeField(null=True, blank=True)
    inventory_checked_at = models.DateTimeField(null=True, blank=True)
    inventory_error = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Market seller"
        verbose_name_plural = "Market sellers"

    def __str__(self) -> str:
        return f"Seller({self.user_id})"


class Listing(models.Model):
    """One Steam item put up for sale (a «лот»)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "На витрине"
        RESERVED = "reserved", "В сделке"
        SOLD = "sold", "Продан"
        CANCELLED = "cancelled", "Снят продавцом"
        MISSING = "missing", "Снят: предмета нет в инвентаре"
        BLOCKED = "blocked", "Снят администрацией"

    OPEN_STATUSES = (Status.ACTIVE, Status.RESERVED)

    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="market_listings")
    asset_id = models.CharField(max_length=32, db_index=True)
    class_id = models.CharField(max_length=32, blank=True, default="")
    instance_id = models.CharField(max_length=32, blank=True, default="")
    market_hash_name = models.CharField(max_length=255, db_index=True)
    icon_url = models.TextField(blank=True, default="")
    rarity_color = models.CharField(max_length=8, blank=True, default="")
    float_value = models.DecimalField(max_digits=10, decimal_places=8, null=True, blank=True)
    item_type = models.CharField(max_length=16, blank=True, default="", db_index=True)
    exterior = models.CharField(max_length=32, blank=True, default="")
    is_stattrak = models.BooleanField(default=False)
    steam_item = models.ForeignKey("steam_catalog.SteamItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="market_listings")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    checked_at = models.DateTimeField(null=True, blank=True, help_text="Когда сайт последний раз видел предмет в инвентаре продавца.")
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Market listing"
        verbose_name_plural = "Market listings"
        constraints = [
            models.UniqueConstraint(fields=["asset_id"], condition=Q(status__in=["active", "reserved"]), name="market_listing_open_asset_uniq"),
            models.CheckConstraint(condition=Q(price__gt=0), name="market_listing_price_positive"),
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="market_listing_status_idx")]

    def __str__(self) -> str:
        return f"LOT-{self.pk}: {self.market_hash_name}"

    @property
    def code(self) -> str:
        return f"LOT-{self.pk:06d}" if self.pk else "LOT-NEW"

    @property
    def icon(self) -> str:
        value = (self.icon_url or "").strip()
        if not value or value.startswith(("http://", "https://")):
            return value
        return f"https://community.cloudflare.steamstatic.com/economy/image/{value}/256x256"

    @property
    def steam_price(self) -> Decimal | None:
        return self.steam_item.price_rub if self.steam_item_id and self.steam_item else None

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES


class MarketEvent(models.Model):
    """Append-only journal of every market action: who did what to which lot (and later, which deal)."""

    class Kind(models.TextChoices):
        TRADE_LINK = "trade_link", "Трейд-ссылка сохранена"
        INVENTORY = "inventory", "Инвентарь загружен"
        LISTED = "listed", "Лот выставлен"
        PRICE = "price", "Цена изменена"
        CANCELLED = "cancelled", "Лот снят продавцом"
        MISSING = "missing", "Лот снят: предмета нет"
        BLOCKED = "blocked", "Лот снят администрацией"

    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, null=True, blank=True, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="market_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="market_events_made")
    message = models.CharField(max_length=255, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Market event"
        verbose_name_plural = "Market events"
