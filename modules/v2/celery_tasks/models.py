from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem


class PriceAutoUpdateRule(models.Model):
    class Status(models.TextChoices):
        IDLE = "idle", "Idle"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        ERROR = "error", "Error"

    class ScheduleMode(models.TextChoices):
        INTERVAL = "interval", "От точки старта"
        HOURLY = "hourly", "Каждый час в выбранную минуту"

    INTERVAL_CHOICES = (
        (5, "5 min"),
        (10, "10 min"),
        (15, "15 min"),
        (30, "30 min"),
        (60, "60 min"),
    )

    category = models.CharField(
        max_length=16,
        choices=SteamItem.ItemType.choices,
        unique=True,
        db_index=True,
    )
    schedule_mode = models.CharField(
        max_length=16,
        choices=ScheduleMode.choices,
        default=ScheduleMode.INTERVAL,
        db_index=True,
    )
    interval_minutes = models.PositiveSmallIntegerField(
        choices=INTERVAL_CHOICES,
        default=60,
    )
    start_at = models.DateTimeField(null=True, blank=True)
    hourly_minute = models.PositiveSmallIntegerField(null=True, blank=True)

    is_enabled = models.BooleanField(default=True, db_index=True)

    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)

    last_status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.IDLE,
        db_index=True,
    )
    last_trigger = models.CharField(max_length=16, blank=True, default="")
    last_error = models.TextField(blank=True, default="")

    touched_items_count = models.PositiveIntegerField(default=0)
    updated_items_count = models.PositiveIntegerField(default=0)
    history_written_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category"]
        indexes = [
            models.Index(fields=["is_enabled", "next_run_at"]),
            models.Index(fields=["category", "is_enabled"]),
        ]
        verbose_name = "Price auto update rule"
        verbose_name_plural = "Price auto update rules"

    def __str__(self) -> str:
        return f"{self.category} / {self.schedule_display}"

    def clean(self):
        super().clean()

        errors = {}

        if self.schedule_mode == self.ScheduleMode.HOURLY:
            if self.hourly_minute is None:
                errors["hourly_minute"] = "Укажи минуту запуска от 0 до 59."
            elif not 0 <= int(self.hourly_minute) <= 59:
                errors["hourly_minute"] = "Минута запуска должна быть в диапазоне 0..59."
        else:
            if self.hourly_minute is not None and not 0 <= int(self.hourly_minute) <= 59:
                errors["hourly_minute"] = "Минута запуска должна быть в диапазоне 0..59."

        if errors:
            raise ValidationError(errors)

    @property
    def schedule_display(self) -> str:
        if self.schedule_mode == self.ScheduleMode.HOURLY:
            minute = int(self.hourly_minute or 0)
            return f"Каждый час в :{minute:02d}"

        if self.start_at:
            local_start = timezone.localtime(self.start_at)
            return f"Каждые {self.interval_minutes} мин · старт {local_start.strftime('%d.%m.%Y %H:%M')}"
        return f"Каждые {self.interval_minutes} мин · старт сразу"

    def _get_next_hourly_run(self, base_dt):
        minute = int(self.hourly_minute or 0)
        local_base = timezone.localtime(base_dt)
        candidate = local_base.replace(minute=minute, second=0, microsecond=0)

        if candidate <= local_base:
            candidate += timedelta(hours=1)

        return candidate

    def schedule_initial_run(self, *, now=None) -> None:
        now = now or timezone.now()

        if self.schedule_mode == self.ScheduleMode.HOURLY:
            self.next_run_at = self._get_next_hourly_run(now)
            return

        if self.start_at and self.start_at > now:
            self.next_run_at = self.start_at
            return

        self.next_run_at = now

    def schedule_next_run(self, *, from_dt=None) -> None:
        base = from_dt or timezone.now()

        if self.schedule_mode == self.ScheduleMode.HOURLY:
            self.next_run_at = self._get_next_hourly_run(base)
            return

        self.next_run_at = base + timedelta(minutes=self.interval_minutes)


class PriceAutoUpdateRunLog(models.Model):
    rule = models.ForeignKey(
        PriceAutoUpdateRule,
        on_delete=models.CASCADE,
        related_name="run_logs",
    )
    category = models.CharField(
        max_length=16,
        choices=SteamItem.ItemType.choices,
        db_index=True,
    )
    triggered_by = models.CharField(max_length=16, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=16,
        choices=PriceAutoUpdateRule.Status.choices,
        default=PriceAutoUpdateRule.Status.RUNNING,
        db_index=True,
    )

    touched_items_count = models.PositiveIntegerField(default=0)
    updated_items_count = models.PositiveIntegerField(default=0)
    history_written_count = models.PositiveIntegerField(default=0)
    errors_count = models.PositiveIntegerField(default=0)

    error_summary = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["rule", "-started_at"]),
            models.Index(fields=["category", "-started_at"]),
            models.Index(fields=["status", "-started_at"]),
            models.Index(fields=["triggered_by", "-started_at"]),
        ]
        verbose_name = "Price auto update run log"
        verbose_name_plural = "Price auto update run logs"

    def __str__(self) -> str:
        return f"{self.category} / {self.triggered_by} / {self.status}"