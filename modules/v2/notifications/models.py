from __future__ import annotations

from django.conf import settings
from django.db import models


class Notification(models.Model):
    """In-app notification for a single recipient.

    ``kind`` selects a renderer from :mod:`modules.v2.notifications.registry`;
    ``object_id`` points at the related object of that kind (e.g. a friend
    request), and ``payload`` keeps any extra data the renderer needs.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications_sent",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=48, db_index=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"], name="notif_recipient_read_idx"),
            models.Index(fields=["kind", "object_id"], name="notif_kind_object_idx"),
        ]

    def __str__(self) -> str:
        return f"Notification({self.kind} → {self.recipient_id}, read={self.is_read})"
