from __future__ import annotations

import secrets
from datetime import time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from common.encryption import EncryptedTextField


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
    class UpdatesMode(models.TextChoices):
        POLLING = "polling", "Опрос (работает из РФ)"
        WEBHOOK = "webhook", "Вебхук"

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    is_enabled = models.BooleanField(default=False)
    bot_token = EncryptedTextField(blank=True, default="")
    bot_username = models.CharField(max_length=128, blank=True, default="")
    default_chat_id = models.CharField(max_length=128, blank=True, default="")
    webhook_secret = EncryptedTextField(blank=True, default=generate_webhook_secret, plain_max_length=64)
    public_base_url = models.URLField(max_length=255, blank=True, default="")
    investment_message_template = models.TextField(default=DEFAULT_INVESTMENT_MESSAGE_TEMPLATE)
    updates_mode = models.CharField(max_length=16, choices=UpdatesMode.choices, default=UpdatesMode.POLLING)
    # Last update_id handled by the poller; the next getUpdates asks for newer ones only.
    polling_offset = models.BigIntegerField(default=0)

    # Daily price digest posted to the public channel (channel_digest.py, Celery beat).
    channel_digest_enabled = models.BooleanField(default=False)
    channel_chat_id = models.CharField(max_length=128, blank=True, default="@caseup_pro_anons")
    channel_digest_times = models.CharField(max_length=64, blank=True, default="20:00")
    channel_digest_fresh_hours = models.PositiveSmallIntegerField(default=12)
    # Attach a picture of the top (a screenshot of the digest card) and put the text into its caption.
    channel_digest_with_image = models.BooleanField(default=True)
    # Post wording, editable on the autopost page. Placeholders: see channel_digest.TEXT_PLACEHOLDERS.
    channel_digest_title = models.CharField(max_length=200, blank=True, default="📊 Цены CS2 за сутки · {date}")
    channel_digest_intro = models.TextField(max_length=500, blank=True, default="")
    channel_digest_outro = models.TextField(max_length=500, blank=True, default="")
    channel_digest_link_text = models.CharField(max_length=120, blank=True, default="🔎 Цены и графики — {site}")
    channel_digest_hashtags = models.CharField(max_length=200, blank=True, default="#CaseUP #CounterStrike2")
    channel_digest_card_title = models.CharField(max_length=60, blank=True, default="Цены CS2 за сутки")
    channel_digest_last_sent_at = models.DateTimeField(null=True, blank=True)

    # UTM tags on site links in Telegram, so Yandex Metrika shows them as Telegram, not "direct" visits.
    # Empty utm_source turns tagging off. Medium is fixed: "channel" for autoposts, "bot" for personal messages.
    utm_source = models.CharField(max_length=64, blank=True, default="telegram")
    channel_utm_campaign = models.CharField(max_length=64, blank=True, default="price_digest")
    bot_utm_campaign = models.CharField(max_length=64, blank=True, default="notifications")

    # Personal Telegram notices to the head of the site (admin_events.py): the highest-ranked admins with Telegram linked.
    admin_signup_notices = models.BooleanField(default=True)
    admin_topup_notices = models.BooleanField(default=True)

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

class ChannelDigestSection(models.Model):
    """One block of the channel digest: e.g. "Ножи: рост" — the top N knives by price growth over the day."""

    class Direction(models.TextChoices):
        UP = "up", "Рост"
        DOWN = "down", "Спад"

    title = models.CharField(max_length=80)
    # "all", "type:<SteamItem.item_type>" or "group:<catalog weapon group>"; see channel_digest.CATEGORY_CHOICES.
    category = models.CharField(max_length=32, default="all")
    direction = models.CharField(max_length=8, choices=Direction.choices, default=Direction.UP)
    top_n = models.PositiveSmallIntegerField(default=5)
    min_price_rub = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("10.00"))
    position = models.PositiveSmallIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "Channel digest section"
        verbose_name_plural = "Channel digest sections"

    def __str__(self) -> str:
        return self.title


class ChannelDigestPost(models.Model):
    """Journal of digest runs: what went to the channel, or why nothing did."""

    class Trigger(models.TextChoices):
        SCHEDULE = "schedule", "По расписанию"
        MANUAL = "manual", "Вручную"

    class Status(models.TextChoices):
        SENT = "sent", "Опубликован"
        EMPTY = "empty", "Нечего постить"
        ERROR = "error", "Ошибка"

    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    chat_id = models.CharField(max_length=128, blank=True, default="")
    text = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Channel digest post"
        verbose_name_plural = "Channel digest posts"

    def __str__(self) -> str:
        return f"ChannelDigestPost({self.created_at:%Y-%m-%d %H:%M}, {self.status})"


# Callables: a fixed time() default trips Django's W161 check when it happens to be close to the current time.
def default_quiet_from():
    return time(23, 0)


def default_quiet_to():
    return time(9, 0)


def default_digest_time():
    return time(10, 0)


class TelegramUserPrefs(models.Model):
    """A user's Telegram bot settings: quiet hours, a daily cap on notices and the personal daily digest.

    Also keeps the day's notice counter, so the cap needs no extra table.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_prefs")
    quiet_enabled = models.BooleanField("Тихие часы", default=True)
    quiet_from = models.TimeField("С", default=default_quiet_from)
    quiet_to = models.TimeField("До", default=default_quiet_to)
    # 0 means no cap.
    daily_limit = models.PositiveSmallIntegerField("Уведомлений в день", default=30)
    digest_enabled = models.BooleanField("Личная сводка", default=False)
    digest_time = models.TimeField("Время сводки", default=default_digest_time)
    digest_last_sent_on = models.DateField(null=True, blank=True)
    counter_date = models.DateField(null=True, blank=True)
    counter_value = models.PositiveIntegerField(default=0)
    # The «limit reached» note is sent once a day.
    limit_notice_on = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram bot settings"
        verbose_name_plural = "Telegram bot settings"

    def __str__(self) -> str:
        return f"TelegramUserPrefs(user={self.user_id})"


class TelegramDeferredNotice(models.Model):
    """A notice held back during quiet hours; sent when they end."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_deferred_notices")
    text = models.TextField()
    # The notice's buttons, sent with it when quiet hours end.
    reply_markup = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"TelegramDeferredNotice(user={self.user_id})"
