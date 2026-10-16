from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditAction(models.TextChoices):
    USER_BAN = "user_ban", "User banned"
    USER_UNBAN = "user_unban", "User unbanned"
    USER_ROLE = "user_role", "Role changed"
    USER_LOGOUT = "user_logout", "Sessions ended"
    USER_2FA_RESET = "user_2fa_reset", "2FA reset"
    REFERRAL_REVIEW = "referral_review", "Referral reviewed"
    PROMO_CREATE = "promo_create", "Promo code created"
    PROMO_UPDATE = "promo_update", "Promo code updated"
    DAILY_BONUS_UPDATE = "daily_bonus_update", "Daily bonus settings updated"
    MARKET_DISPUTE = "market_dispute", "Market dispute resolved"
    MULTI_ACCOUNT_DECISION = "multi_account_decision", "Multi-account decision"
    IP_BAN = "ip_ban", "IP banned"
    IP_UNBAN = "ip_unban", "IP unbanned"
    USER_NOTE = "user_note", "Admin note"
    SITE_MAINTENANCE = "site_maintenance", "Maintenance mode"
    SITE_REGISTRATION = "site_registration", "Registration"
    MODULE_TOGGLE = "module_toggle", "Module toggled"
    CATALOG_APPROVE = "catalog_approve", "Catalog item approved"
    CATALOG_DISABLE = "catalog_disable", "Catalog item disabled"
    SETTINGS_UPDATE = "settings_update", "Settings updated"
    CACHE_CLEAR = "cache_clear", "Cache cleared"
    PAYMENT_RECHECK = "payment_recheck", "Payment rechecked"


AUDIT_ACTION_LABELS_RU = {
    AuditAction.USER_BAN: "Бан пользователя",
    AuditAction.USER_UNBAN: "Разбан пользователя",
    AuditAction.USER_ROLE: "Смена роли",
    AuditAction.USER_LOGOUT: "Завершение сессий",
    AuditAction.USER_2FA_RESET: "Сброс 2FA",
    AuditAction.REFERRAL_REVIEW: "Проверка реферала",
    AuditAction.PROMO_CREATE: "Создан промокод",
    AuditAction.PROMO_UPDATE: "Изменён промокод",
    AuditAction.DAILY_BONUS_UPDATE: "Изменён бонус дня",
    AuditAction.MARKET_DISPUTE: "Решён спор на рынке",
    AuditAction.MULTI_ACCOUNT_DECISION: "Решение по мульти-аккаунтам",
    AuditAction.IP_BAN: "Бан IP",
    AuditAction.IP_UNBAN: "Снятие бана IP",
    AuditAction.USER_NOTE: "Заметка админа",
    AuditAction.SITE_MAINTENANCE: "Режим обслуживания",
    AuditAction.SITE_REGISTRATION: "Регистрация",
    AuditAction.MODULE_TOGGLE: "Переключение модуля",
    AuditAction.CATALOG_APPROVE: "Предмет одобрен",
    AuditAction.CATALOG_DISABLE: "Предмет скрыт",
    AuditAction.SETTINGS_UPDATE: "Изменение настроек",
    AuditAction.CACHE_CLEAR: "Сброс кэша",
    AuditAction.PAYMENT_RECHECK: "Перепроверка платежа",
}


class AdminAuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_audit_actions",
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices, db_index=True)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_audit_targets",
    )
    summary = models.CharField(max_length=255, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Admin audit log entry"
        verbose_name_plural = "Admin audit log"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["action", "created_at"], name="cc_audit_action_idx"),
            models.Index(fields=["target_user", "created_at"], name="cc_audit_target_idx"),
        ]

    def __str__(self) -> str:
        return f"AdminAuditLog({self.action}, actor={self.actor_id}, target={self.target_user_id})"

    @property
    def action_label_ru(self) -> str:
        return AUDIT_ACTION_LABELS_RU.get(self.action, self.get_action_display())


class UserBanQuerySet(models.QuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(lifted_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )

    def expired(self):
        return self.filter(lifted_at__isnull=True, expires_at__isnull=False, expires_at__lte=timezone.now())


class UserBan(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="control_center_bans",
    )
    reason = models.CharField(max_length=255)
    banned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_control_center_bans",
    )
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lifted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lifted_control_center_bans",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserBanQuerySet.as_manager()

    class Meta:
        verbose_name = "User ban"
        verbose_name_plural = "User bans"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["user", "lifted_at"], name="cc_ban_user_lifted_idx"),
        ]

    def __str__(self) -> str:
        return f"UserBan(user={self.user_id}, until={self.expires_at or 'forever'})"

    @property
    def is_active(self) -> bool:
        if self.lifted_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    @property
    def is_permanent(self) -> bool:
        return self.expires_at is None


class ActivityCategory(models.TextChoices):
    AUTH = "auth", "Вход и выход"
    ACCOUNT = "account", "Аккаунт"
    BALANCE = "balance", "Баланс"
    PAYMENT = "payment", "Платежи"
    CASE = "case", "Кейсы"
    STORE = "store", "Магазин"
    SUBSCRIPTION = "subscription", "Подписки"
    TRADING = "trading", "Торговля"
    FARM = "farm", "Ферма"
    SOCIAL = "social", "Социальное"
    CONSTRUCTOR = "constructor", "Конструктор"
    SECURITY = "security", "Безопасность"
    ADMIN = "admin", "Администрирование"
    REQUEST = "request", "Запросы"


class ActivityLog(models.Model):
    """Site-wide activity journal: every meaningful action, who did it, to whom, from where.

    ``user`` is whose activity it is (the payer, the case opener); ``actor`` is who performed it
    (the same user, an admin acting on them, or None for the system/anonymous). Usernames are
    snapshotted so the journal stays readable after an account is gone.
    """

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    category = models.CharField(max_length=16, choices=ActivityCategory.choices, db_index=True)
    event = models.CharField(max_length=64, db_index=True)
    summary = models.CharField(max_length=255, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity_logs"
    )
    username = models.CharField(max_length=150, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity_logs_as_actor"
    )
    actor_username = models.CharField(max_length=150, blank=True, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    object_type = models.CharField(max_length=64, blank=True, default="")
    object_id = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    method = models.CharField(max_length=8, blank=True, default="")
    path = models.CharField(max_length=255, blank=True, default="")
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Activity log entry"
        verbose_name_plural = "Activity log"
        indexes = [
            models.Index(fields=["category", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["event", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.event} {self.username or '-'}"
