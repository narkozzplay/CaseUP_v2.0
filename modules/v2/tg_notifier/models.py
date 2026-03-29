from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


DEFAULT_INVESTMENT_MESSAGE_TEMPLATE = """{{ trade_lock_label }}

Предмет: {{ item_name }} ({{ item_url }})
Дата покупки: {{ purchased_at }} МСК
Дата разблокировки: {{ unlock_at }} МСК

Количество: {{ quantity }}
Цена покупки: {{ unit_buy_price }}
Затрачено: {{ total_spent }}

Текущая цена: {{ current_price }}
Текущая стоимость: {{ current_value }}
Стоимость после комиссии Steam (15%): {{ current_value_after_fee }}

Прибыль: {{ profit_abs }} ({{ profit_pct }})
Прибыль после комиссии: {{ profit_after_fee }}

Статус: {{ hold_status_label }}
""".strip()


def generate_webhook_secret() -> str:
    return secrets.token_hex(16)


def generate_link_token() -> str:
    return secrets.token_hex(16)


def default_link_expiry():
    return timezone.now() + timedelta(minutes=30)


class TelegramNotifierConfig(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    is_enabled = models.BooleanField(default=False)
    bot_token = models.CharField(max_length=255, blank=True, default="")
    bot_username = models.CharField(max_length=128, blank=True, default="")
    default_chat_id = models.CharField(max_length=128, blank=True, default="")
    webhook_secret = models.CharField(max_length=64, blank=True, default=generate_webhook_secret)
    public_base_url = models.URLField(max_length=255, blank=True, default="")
    investment_message_template = models.TextField(default=DEFAULT_INVESTMENT_MESSAGE_TEMPLATE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram notifier config"
        verbose_name_plural = "Telegram notifier config"

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        if not self.investment_message_template:
            self.investment_message_template = DEFAULT_INVESTMENT_MESSAGE_TEMPLATE
        if not self.webhook_secret:
            self.webhook_secret = generate_webhook_secret()
        self.public_base_url = (self.public_base_url or "").strip().rstrip("/")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return "Telegram notifier config"

    @property
    def masked_bot_token(self) -> str:
        token = (self.bot_token or "").strip()
        if not token:
            return "Не задан"
        if len(token) <= 10:
            return "*" * len(token)
        return f"{token[:6]}••••{token[-4:]}"

    @classmethod
    def bootstrap_defaults(cls) -> dict[str, object]:
        return {
            "is_enabled": settings.TELEGRAM_NOTIFICATIONS_ENABLED,
            "bot_token": settings.TELEGRAM_BOT_TOKEN,
            "bot_username": "",
            "default_chat_id": settings.TELEGRAM_CHAT_ID,
            "public_base_url": settings.SITE_BASE_URL,
            "investment_message_template": DEFAULT_INVESTMENT_MESSAGE_TEMPLATE,
        }


class TelegramLinkToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_link_tokens",
    )
    token = models.CharField(max_length=32, unique=True, db_index=True, default=generate_link_token)
    expires_at = models.DateTimeField(default=default_link_expiry)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Telegram link token"
        verbose_name_plural = "Telegram link tokens"
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"TelegramLinkToken(user={self.user_id}, token={self.token})"

    @property
    def is_active(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()