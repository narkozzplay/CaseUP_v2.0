from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from modules.v2.profiles.models import UserProfile
from modules.v2.profiles.rbac import assignable_roles, can_manage_user

from ..models import AuditAction, UserBan
from .audit import log_admin_action

User = get_user_model()

BAN_DURATION_CHOICES = (
    ("1h", "1 час", timedelta(hours=1)),
    ("1d", "1 день", timedelta(days=1)),
    ("7d", "7 дней", timedelta(days=7)),
    ("30d", "30 дней", timedelta(days=30)),
    ("forever", "Навсегда", None),
)
BAN_DURATIONS = {code: delta for code, _label, delta in BAN_DURATION_CHOICES}


class UserManagementError(Exception):
    pass


def end_user_sessions(user) -> int:
    """Sign the user out on every device (ban, admin "end sessions")."""
    from modules.v2.accounts.sessions import end_all_sessions

    return end_all_sessions(user)


def get_active_ban(user) -> UserBan | None:
    return UserBan.objects.active().filter(user=user).select_related("banned_by").first()


@transaction.atomic
def ban_user(*, actor, target, reason: str, duration: str, request=None) -> UserBan:
    if not can_manage_user(actor, target):
        raise UserManagementError("Недостаточно прав для бана этого пользователя.")
    reason = (reason or "").strip()
    if not reason:
        raise UserManagementError("Укажите причину бана.")
    if duration not in BAN_DURATIONS:
        raise UserManagementError("Неизвестная длительность бана.")

    delta = BAN_DURATIONS[duration]
    expires_at = timezone.now() + delta if delta else None
    UserBan.objects.active().filter(user=target).update(lifted_at=timezone.now(), lifted_by=actor)
    ban = UserBan.objects.create(user=target, reason=reason[:255], banned_by=actor, expires_at=expires_at)
    if target.is_active:
        target.is_active = False
        target.save(update_fields=["is_active"])
    sessions = end_user_sessions(target)
    log_admin_action(
        actor=actor,
        action=AuditAction.USER_BAN,
        target_user=target,
        summary=f"{target.username}: {reason}"[:255],
        payload={
            "reason": reason,
            "duration": duration,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "sessions_ended": sessions,
        },
        request=request,
    )
    return ban


@transaction.atomic
def unban_user(*, actor, target, request=None) -> int:
    if not can_manage_user(actor, target):
        raise UserManagementError("Недостаточно прав для разбана этого пользователя.")
    lifted = UserBan.objects.active().filter(user=target).update(lifted_at=timezone.now(), lifted_by=actor)
    if not target.is_active:
        target.is_active = True
        target.save(update_fields=["is_active"])
    log_admin_action(
        actor=actor,
        action=AuditAction.USER_UNBAN,
        target_user=target,
        summary=target.username,
        payload={"bans_lifted": lifted},
        request=request,
    )
    return lifted


def lift_expired_bans() -> int:
    lifted = 0
    now = timezone.now()
    for ban in UserBan.objects.expired().select_related("user"):
        with transaction.atomic():
            ban.lifted_at = now
            ban.save(update_fields=["lifted_at"])
            user = ban.user
            if not user.is_active and not UserBan.objects.active().filter(user=user).exists():
                user.is_active = True
                user.save(update_fields=["is_active"])
            lifted += 1
    return lifted


@transaction.atomic
def change_user_role(*, actor, target, role: str, request=None) -> bool:
    from modules.v2.profiles.views import _apply_profile_role

    if not can_manage_user(actor, target):
        raise UserManagementError("Недостаточно прав для смены роли этого пользователя.")
    allowed = {code for code, _label in assignable_roles(actor)}
    if role not in allowed:
        raise UserManagementError("Эту роль назначить нельзя.")
    profile, _created = UserProfile.objects.get_or_create(user=target)
    previous = profile.role
    changed = _apply_profile_role(target_user=target, profile=profile, role=role)
    if changed:
        log_admin_action(
            actor=actor,
            action=AuditAction.USER_ROLE,
            target_user=target,
            summary=f"{target.username}: {previous} → {role}",
            payload={"from": previous, "to": role},
            request=request,
        )
    return changed


def terminate_sessions(*, actor, target, request=None) -> int:
    if not can_manage_user(actor, target):
        raise UserManagementError("Недостаточно прав для этого пользователя.")
    removed = end_user_sessions(target)
    log_admin_action(
        actor=actor,
        action=AuditAction.USER_LOGOUT,
        target_user=target,
        summary=f"{target.username}: {removed}",
        payload={"sessions_ended": removed},
        request=request,
    )
    return removed


def add_admin_note(*, actor, target, note: str, request=None):
    note = (note or "").strip()
    if not note:
        raise UserManagementError("Заметка пустая.")
    return log_admin_action(
        actor=actor,
        action=AuditAction.USER_NOTE,
        target_user=target,
        summary=note[:255],
        payload={"note": note[:2000]},
        request=request,
    )
