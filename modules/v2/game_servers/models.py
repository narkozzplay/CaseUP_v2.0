from __future__ import annotations

from decimal import Decimal

from django.db import models


class GameServer(models.Model):
    class Game(models.TextChoices):
        CS2 = "cs2", "Counter-Strike 2"

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    game = models.CharField(max_length=24, choices=Game.choices, default=Game.CS2, db_index=True)
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=27015)
    server_path = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_enabled = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=100, db_index=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNKNOWN, db_index=True)
    last_check_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_seen_online_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True, default="")
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)

    shop_label = models.CharField(max_length=120, blank=True, default="")
    shop_description = models.TextField(blank=True, default="")
    sync_notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Game server"
        verbose_name_plural = "Game servers"
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["is_enabled", "sort_order"]),
            models.Index(fields=["game", "status"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"


class GameServerPrivilege(models.Model):
    class PrivilegeType(models.TextChoices):
        VIP = "vip", "VIP"
        ADMIN = "admin", "Admin"
        MODERATION = "moderation", "Moderation"
        OTHER = "other", "Other"

    server = models.ForeignKey(
        GameServer,
        on_delete=models.CASCADE,
        related_name="privileges",
    )
    code = models.SlugField(max_length=64)
    name = models.CharField(max_length=120)
    privilege_type = models.CharField(max_length=16, choices=PrivilegeType.choices, default=PrivilegeType.VIP, db_index=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    duration_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True, db_index=True)
    description = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=100, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Game server privilege"
        verbose_name_plural = "Game server privileges"
        ordering = ("server", "sort_order", "id")
        constraints = [
            models.UniqueConstraint(fields=["server", "code"], name="game_servers_privilege_server_code_unique"),
        ]
        indexes = [
            models.Index(fields=["server", "is_active", "sort_order"]),
            models.Index(fields=["server", "privilege_type", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.server.name}: {self.name}"


class GameServerStatusCheck(models.Model):
    server = models.ForeignKey(
        GameServer,
        on_delete=models.CASCADE,
        related_name="status_checks",
    )
    status = models.CharField(max_length=16, choices=GameServer.Status.choices, db_index=True)
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Game server status check"
        verbose_name_plural = "Game server status checks"
        ordering = ("-checked_at", "-id")
        indexes = [
            models.Index(fields=["server", "-checked_at"]),
            models.Index(fields=["status", "-checked_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.server.name}: {self.status} @ {self.checked_at:%Y-%m-%d %H:%M:%S}"
