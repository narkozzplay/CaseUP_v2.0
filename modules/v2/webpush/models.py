from __future__ import annotations

from django.conf import settings
from django.db import models


class VapidKey(models.Model):
    """The site's VAPID key pair: generated once, browsers bind subscriptions to its public half."""

    private_key = models.TextField()
    public_key = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)


class PushSubscription(models.Model):
    """One browser/device that agreed to receive pushes."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=64)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    @property
    def device_label(self) -> str:
        ua = self.user_agent.lower()
        system = next((name for key, name in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"), ("windows", "Windows"), ("mac os", "Mac"), ("linux", "Linux")) if key in ua), "Устройство")
        browser = next((name for key, name in (("yabrowser", "Яндекс Браузер"), ("edg/", "Edge"), ("opr/", "Opera"), ("firefox", "Firefox"), ("chrome", "Chrome"), ("safari", "Safari")) if key in ua), "браузер")
        return f"{system} · {browser}"


class PushSettings(models.Model):
    """Per-user choices: switched-off categories and the «turn on notifications» prompt state."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_settings")
    disabled_categories = models.JSONField(default=list, blank=True)
    prompt_reason = models.CharField(max_length=32, blank=True, default="")
    prompt_requested_at = models.DateTimeField(null=True, blank=True)
    prompt_snoozed_until = models.DateTimeField(null=True, blank=True)
    prompt_dismissals = models.PositiveSmallIntegerField(default=0)


class PushLog(models.Model):
    """Delivery counters for the Control Center: one row per push sent to one device."""

    class Status(models.TextChoices):
        SENT = "sent", "Доставлен в браузер"
        GONE = "gone", "Подписка устарела"
        FAILED = "failed", "Ошибка"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    category = models.CharField(max_length=24, db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
