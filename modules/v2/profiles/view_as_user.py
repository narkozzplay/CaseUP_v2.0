"""«Глазами пользователя»: команда смотрит сайт так, как его видит обычный пользователь без подписки.

Режим живёт в сессии. Пока он включён, на время запроса у request.user в памяти снимаются
is_staff / is_superuser, а роль профиля становится USER, поэтому все проверки прав (get_user_role,
шаблоны, middleware) видят обычного пользователя. В базу это не попадает: сигналы pre_save
возвращают настоящие значения перед любым сохранением. Активная подписка тоже скрывается.

В режиме разрешены только просмотр (GET/HEAD/OPTIONS), выход из режима и выход из аккаунта,
чтобы случайно ничего не изменить от имени «пользователя».
"""
from __future__ import annotations

from django.conf import settings
from django.db.models.signals import post_save, pre_save
from django.http import HttpResponseForbidden, JsonResponse
from django.urls import reverse

from .models import UserProfile, UserRole
from .rbac import get_user_role


SESSION_KEY = "caseup_view_as_user"
REAL_FLAGS_ATTR = "_caseup_view_as_user_real"
REAL_ROLE_ATTR = "_caseup_view_as_user_real_role"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
READ_ONLY_MESSAGE = "Режим «глазами пользователя»: действия отключены. Выйдите из режима, чтобы что-то изменить."


def can_view_as_user(user) -> bool:
    """Режим доступен команде (модератор и выше), то есть тем, кто видит сайт иначе, чем пользователь."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, REAL_FLAGS_ATTR, None) is not None:
        return True
    return bool(user.is_staff or user.is_superuser or get_user_role(user).is_moder_plus)


def is_viewing_as_user(user) -> bool:
    return getattr(user, REAL_FLAGS_ATTR, None) is not None


def _get_profile(user) -> UserProfile | None:
    try:
        return user.profile_v2
    except UserProfile.DoesNotExist:
        return None


def apply_view_as_user(user) -> None:
    if is_viewing_as_user(user):
        return
    setattr(user, REAL_FLAGS_ATTR, {"is_staff": user.is_staff, "is_superuser": user.is_superuser})
    user.is_staff = False
    user.is_superuser = False
    profile = _get_profile(user)
    if profile is not None:
        setattr(profile, REAL_ROLE_ATTR, profile.role)
        profile.role = UserRole.USER


def _exit_paths() -> tuple[str, ...]:
    return (reverse("accounts:view_as_user_exit"), reverse("accounts:logout"))


def _read_only_response(request):
    accept = request.headers.get("Accept", "")
    if "application/json" in accept or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": False, "error": READ_ONLY_MESSAGE}, status=403)
    return HttpResponseForbidden(READ_ONLY_MESSAGE)


class ViewAsUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        if session is not None and session.get(SESSION_KEY):
            user = request.user
            if can_view_as_user(user):
                apply_view_as_user(user)
                if request.method not in SAFE_METHODS and request.path_info not in _exit_paths():
                    return _read_only_response(request)
            else:
                session.pop(SESSION_KEY, None)
        return self.get_response(request)


def _restore_user_flags(sender, instance, **kwargs):
    real = getattr(instance, REAL_FLAGS_ATTR, None)
    if real is not None:
        instance.is_staff = real["is_staff"]
        instance.is_superuser = real["is_superuser"]


def _reapply_user_flags(sender, instance, **kwargs):
    if getattr(instance, REAL_FLAGS_ATTR, None) is not None:
        instance.is_staff = False
        instance.is_superuser = False


def _restore_profile_role(sender, instance, **kwargs):
    real_role = getattr(instance, REAL_ROLE_ATTR, None)
    if real_role is not None:
        instance.role = real_role


def _reapply_profile_role(sender, instance, **kwargs):
    if getattr(instance, REAL_ROLE_ATTR, None) is not None:
        instance.role = UserRole.USER


pre_save.connect(_restore_user_flags, sender=settings.AUTH_USER_MODEL, dispatch_uid="caseup_view_as_user_pre_save_user")
post_save.connect(_reapply_user_flags, sender=settings.AUTH_USER_MODEL, dispatch_uid="caseup_view_as_user_post_save_user")
pre_save.connect(_restore_profile_role, sender=UserProfile, dispatch_uid="caseup_view_as_user_pre_save_profile")
post_save.connect(_reapply_profile_role, sender=UserProfile, dispatch_uid="caseup_view_as_user_post_save_profile")
