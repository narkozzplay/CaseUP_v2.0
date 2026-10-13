from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditAction(models.TextChoices):
    USER_BAN = "user_ban", "User banned"
    USER_UNBAN = "user_unban", "User unbanned"
    USER_ROLE = "user_role", "Role changed"
    USER_LOGOUT = "user_logout", "Sessions ended"
    USER_NOTE = "user_note", "Admin note"
    SITE_MAINTENANCE = "site_maintenance", "Maintenance mode"
    SITE_REGISTRATION = "site_registration", "Registration"
    MODULE_TOGGLE = "module_toggle", "Module toggled"
    CATALOG_APPROVE = "catalog_approve", "Catalog item approved"
    CATALOG_DISABLE = "catalog_disable", "Catalog item disabled"
    SETTINGS_UPDATE = "settings_update", "Settings updated"
    CACHE_CLEAR = "cache_clear", "Cache cleared"


AUDIT_ACTION_LABELS_RU = {
    AuditAction.USER_BAN: "Бан пользователя",
    AuditAction.USER_UNBAN: "Разбан пользователя",
    AuditAction.USER_ROLE: "Смена роли",
    AuditAction.USER_LOGOUT: "Завершение сессий",
    AuditAction.USER_NOTE: "Заметка админа",
    AuditAction.SITE_MAINTENANCE: "Режим обслуживания",
    AuditAction.SITE_REGISTRATION: "Регистрация",
    AuditAction.MODULE_TOGGLE: "Переключение модуля",
    AuditAction.CATALOG_APPROVE: "Предмет одобрен",
    AuditAction.CATALOG_DISABLE: "Предмет скрыт",
    AuditAction.SETTINGS_UPDATE: "Изменение настроек",
    AuditAction.CACHE_CLEAR: "Сброс кэша",
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
