"""«Смотреть как пользователь»: admin+ temporarily sees the site as a plain user with no subscription.

The session keeps one flag. While it is set, request.user is a copy of the admin with staff flags off
and a profile copy with the plain user role; request.real_user is the admin. Nothing is written in
this mode by the viewer: unsafe requests are refused, except leaving the mode, logout and the language
switch. Pages that lazily create their viewer's own records on GET (a referral code, a profile) still
do so for the admin's account, the same as on a normal visit.
"""

from __future__ import annotations

import copy

from django.http import HttpResponseForbidden
from django.urls import Resolver404, resolve

SESSION_KEY = "caseup_view_as_user"
MASK_ATTR = "_caseup_view_as_user"

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_WRITABLE_VIEWS = {"accounts:view_as_user_exit", "accounts:logout", "set_language"}

READ_ONLY_MESSAGE = "Режим просмотра от лица пользователя: действия отключены. Выйдите из режима, чтобы вернуть права."


def is_view_as_user(user) -> bool:
    return bool(getattr(user, MASK_ATTR, False))


def can_view_as_user(user) -> bool:
    from modules.v2.profiles.rbac import get_user_role

    return bool(getattr(user, "is_authenticated", False)) and get_user_role(user).is_admin_plus


def _guard_save(instance, **restore):
    """Writes to a masked copy keep the real privileged values, never the masked ones."""
    masked = {name: getattr(instance, name) for name in restore}
    model_save = type(instance).save

    def save(*args, **kwargs):
        for name, value in restore.items():
            setattr(instance, name, value)
        try:
            model_save(instance, *args, **kwargs)
        finally:
            for name, value in masked.items():
                setattr(instance, name, value)

    instance.save = save


def build_masked_user(user):
    from modules.v2.profiles.models import UserProfile, UserRole

    try:
        profile = user.profile_v2
    except UserProfile.DoesNotExist:
        profile, _ = UserProfile.objects.get_or_create(user=user)

    masked = copy.copy(user)
    real_staff, real_superuser = user.is_staff, user.is_superuser
    masked.is_staff = False
    masked.is_superuser = False
    setattr(masked, MASK_ATTR, True)
    _guard_save(masked, is_staff=real_staff, is_superuser=real_superuser)

    masked_profile = copy.copy(profile)
    real_role = profile.role
    masked_profile.role = UserRole.USER
    _guard_save(masked_profile, role=real_role)
    masked.profile_v2 = masked_profile
    return masked


def _view_name(path: str) -> str:
    try:
        return resolve(path).view_name
    except Resolver404:
        return ""


class ViewAsUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        request.real_user = user
        session = getattr(request, "session", None)
        if session is None or not session.get(SESSION_KEY):
            return self.get_response(request)

        if not can_view_as_user(user):
            session.pop(SESSION_KEY, None)
            return self.get_response(request)

        request.user = build_masked_user(user)
        request.view_as_user = True
        if request.method not in _SAFE_METHODS and _view_name(request.path_info) not in _WRITABLE_VIEWS:
            return HttpResponseForbidden(READ_ONLY_MESSAGE)
        return self.get_response(request)
