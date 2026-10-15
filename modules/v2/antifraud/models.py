from __future__ import annotations

import ipaddress

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class UserIp(models.Model):
    """Every public IP an account was seen on (from sign-ins and activity), with first/last time."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="seen_ips")
    ip = models.GenericIPAddressField()
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    hits = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = "User IP"
        verbose_name_plural = "User IPs"
        constraints = [models.UniqueConstraint(fields=["user", "ip"], name="antifraud_user_ip_uniq")]
        indexes = [models.Index(fields=["ip", "last_seen"], name="antifraud_ip_seen_idx")]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.ip}"


class MultiAccountCase(models.Model):
    """A group of accounts linked by shared IPs, waiting for (or after) an admin's decision.

    OPEN — needs a look. CLEAN — checked, fine (alerts again only if a new account joins).
    ABUSE — the same person abusing the platform; accounts were banned.
    """

    class Status(models.TextChoices):
        OPEN = "open", "На проверке"
        CLEAN = "clean", "Чисто"
        ABUSE = "abuse", "Абуз"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="MultiAccountMember", related_name="multi_account_cases")
    shared_ips = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    last_alert_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-updated_at", "-id")
        verbose_name = "Multi-account case"
        verbose_name_plural = "Multi-account cases"

    def __str__(self) -> str:
        return f"MultiAccountCase #{self.pk} ({self.status})"


class MultiAccountMember(models.Model):
    case = models.ForeignKey(MultiAccountCase, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="multi_account_memberships")
    added_at = models.DateTimeField(default=timezone.now)
    # Members that joined after the last decision are highlighted for the admin.
    is_new = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["case", "user"], name="antifraud_case_member_uniq")]


class IpBanQuerySet(models.QuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(lifted_at__isnull=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


class IpBan(models.Model):
    """Blocks requests from an address or a range (CIDR). Admin+ are never blocked, so a mistake can be undone."""

    network = models.CharField(max_length=64)
    reason = models.CharField(max_length=255)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    case = models.ForeignKey(MultiAccountCase, on_delete=models.SET_NULL, null=True, blank=True, related_name="ip_bans")

    objects = IpBanQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "IP ban"
        verbose_name_plural = "IP bans"

    def __str__(self) -> str:
        return self.network

    def clean(self):
        self.network = normalize_network(self.network)

    @property
    def is_active(self) -> bool:
        return self.lifted_at is None and (self.expires_at is None or self.expires_at > timezone.now())


def normalize_network(raw: str) -> str:
    try:
        return str(ipaddress.ip_network((raw or "").strip(), strict=False))
    except ValueError as exc:
        raise ValidationError("Укажите IP-адрес (95.24.1.10) или диапазон (95.24.1.0/24).") from exc
