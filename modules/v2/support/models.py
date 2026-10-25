from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models


def attachment_upload_to(instance, filename: str) -> str:
    """Attachments live under media/private/, which Caddy does not serve; the name is random on top of that.
    They are given out only by ``support:attachment`` after an access check."""
    suffix = Path(filename).suffix.lower()[:10]
    return f"private/support/{uuid.uuid4().hex}{suffix}"


class SupportTicket(models.Model):
    class Category(models.TextChoices):
        ACCOUNT = "account", "Аккаунт и вход"
        BALANCE = "balance", "Баланс и платежи"
        MARKET = "market", "Рынок и сделки"
        CASES = "cases", "Кейсы и бонусы"
        FARM = "farm", "Ферма и инвестиции"
        BUG = "bug", "Ошибка на сайте"
        IDEA = "idea", "Предложение"
        OTHER = "other", "Другое"

    class Status(models.TextChoices):
        OPEN = "open", "Новый"
        IN_PROGRESS = "in_progress", "В работе"
        WAITING_USER = "waiting_user", "Ждёт ответа"
        RESOLVED = "resolved", "Решён"
        CLOSED = "closed", "Закрыт"

    class Priority(models.TextChoices):
        LOW = "low", "Низкий"
        NORMAL = "normal", "Обычный"
        HIGH = "high", "Высокий"

    ACTIVE_STATUSES = (Status.OPEN, Status.IN_PROGRESS, Status.WAITING_USER)
    # Statuses where the ticket waits for the support team, not for the user.
    STAFF_TURN_STATUSES = (Status.OPEN, Status.IN_PROGRESS)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    subject = models.CharField(max_length=160)
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.OTHER, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets_assigned",
    )
    page_url = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(db_index=True)
    # The support team answered or changed the status and the author has not opened the ticket since.
    user_unread = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Support ticket"
        verbose_name_plural = "Support tickets"
        ordering = ("-last_message_at", "-id")
        indexes = [
            models.Index(fields=["user", "-last_message_at"], name="support_user_recent_idx"),
            models.Index(fields=["status", "-last_message_at"], name="support_status_recent_idx"),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.subject}"

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_closed(self) -> bool:
        return self.status == self.Status.CLOSED


class SupportMessage(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="support_messages")
    body = models.TextField(max_length=5000, blank=True, default="")
    is_staff = models.BooleanField(default=False)
    # A note between moderators; the author of the ticket never sees it.
    is_internal = models.BooleanField(default=False)
    # Status changes ("Тикет взят в работу") are shown in the thread as small system lines.
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Support message"
        verbose_name_plural = "Support messages"
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"Message #{self.pk} in ticket #{self.ticket_id}"


class SupportAttachment(models.Model):
    message = models.ForeignKey(SupportMessage, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=attachment_upload_to, max_length=200)
    original_name = models.CharField(max_length=160)
    content_type = models.CharField(max_length=80, blank=True, default="")
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Support attachment"
        verbose_name_plural = "Support attachments"
        ordering = ("id",)

    def __str__(self) -> str:
        return self.original_name

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")
