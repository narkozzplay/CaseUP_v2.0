from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from modules.v2.farm.models import FarmAccount, FarmAccountInventoryItem
from modules.v2.steam_catalog.models import SteamItem


class TradeBotAccount(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        REVIEW = "review", "Review"
        DISABLED = "disabled", "Disabled"

    title = models.CharField(max_length=120)
    steam_id64 = models.CharField(max_length=32, unique=True, db_index=True)
    trade_url = models.URLField(blank=True, default="")
    login_hint = models.CharField(max_length=120, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REVIEW, db_index=True)
    sort_order = models.PositiveIntegerField(default=100, db_index=True)
    is_enabled = models.BooleanField(default=True, db_index=True)
    is_default = models.BooleanField(default=False, db_index=True)
    last_offer_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "title"]
        indexes = [
            models.Index(fields=["status", "is_enabled", "sort_order"]),
        ]

    def __str__(self) -> str:
        return self.title


class SaleOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OFFER_PENDING = "offer_pending", "Offer pending"
        OFFER_SENT = "offer_sent", "Offer sent"
        OFFER_ACCEPTED = "offer_accepted", "Offer accepted"
        PAYOUT_PENDING = "payout_pending", "Payout pending"
        COMPLETED = "completed", "Completed"
        CANCELLED_BY_USER = "cancelled_by_user", "Cancelled by user"
        CANCELLED_BY_SYSTEM = "cancelled_by_system", "Cancelled by system"
        FAILED = "failed", "Failed"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trade_sale_orders",
    )
    farm_account = models.ForeignKey(
        FarmAccount,
        on_delete=models.PROTECT,
        related_name="trade_sale_orders",
    )
    bot_account = models.ForeignKey(
        TradeBotAccount,
        on_delete=models.PROTECT,
        related_name="sale_orders",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT, db_index=True)
    items_count = models.PositiveIntegerField(default=0)
    total_catalog_price_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_payout_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    payout_paid = models.BooleanField(default=False, db_index=True)
    payout_paid_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True, default="")
    cancelled_reason = models.CharField(max_length=255, blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["seller", "status", "created_at"]),
            models.Index(fields=["farm_account", "status", "created_at"]),
            models.Index(fields=["bot_account", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"SaleOrder#{self.pk} ({self.status})"


class SaleOrderItem(models.Model):
    order = models.ForeignKey(
        SaleOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    inventory_item = models.ForeignKey(
        FarmAccountInventoryItem,
        on_delete=models.PROTECT,
        related_name="trade_sale_items",
    )
    steam_item = models.ForeignKey(
        SteamItem,
        on_delete=models.PROTECT,
        related_name="trade_sale_items",
        null=True,
        blank=True,
    )
    asset_id_snapshot = models.CharField(max_length=64, db_index=True)
    class_id_snapshot = models.CharField(max_length=64, blank=True, default="")
    instance_id_snapshot = models.CharField(max_length=64, blank=True, default="")
    market_hash_name_snapshot = models.CharField(max_length=255, db_index=True)
    icon_url_snapshot = models.TextField(blank=True, default="")
    amount_snapshot = models.PositiveIntegerField(default=1)
    catalog_price_rub_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payout_price_rub_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "inventory_item"], name="tradehub_unique_order_inventory_item"),
        ]
        indexes = [
            models.Index(fields=["order", "asset_id_snapshot"]),
            models.Index(fields=["steam_item", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"SaleOrderItem(order={self.order_id}, asset={self.asset_id_snapshot})"


class TradeOffer(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"

    order = models.OneToOneField(
        SaleOrder,
        on_delete=models.CASCADE,
        related_name="offer",
    )
    bot_account = models.ForeignKey(
        TradeBotAccount,
        on_delete=models.PROTECT,
        related_name="offers",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)
    external_offer_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["external_offer_id"]),
        ]

    def __str__(self) -> str:
        return f"TradeOffer(order={self.order_id}, status={self.status})"


class TradeEventLog(models.Model):
    order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name="logs")
    offer = models.ForeignKey(TradeOffer, on_delete=models.CASCADE, related_name="logs", null=True, blank=True)
    event = models.CharField(max_length=64, db_index=True)
    message = models.CharField(max_length=255, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tradehub_event_logs_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["order", "created_at"]),
            models.Index(fields=["event", "created_at"]),
        ]


class TradeUserPenalty(models.Model):
    class Type(models.TextChoices):
        WARNING = "warning", "Warning"
        BAN = "ban", "Ban"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradehub_penalties",
    )
    type = models.CharField(max_length=16, choices=Type.choices, db_index=True)
    order = models.ForeignKey(SaleOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="penalties")
    offer = models.ForeignKey(TradeOffer, on_delete=models.SET_NULL, null=True, blank=True, related_name="penalties")
    reason = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tradehub_penalties_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "type", "is_active", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"TradePenalty(user={self.user_id}, type={self.type})"
