"""«Смотреть как обычный пользователь»: an admin sees the site the way a plain user does.

The admin turns the mode on from the dashboard; it lives in the session. For every request while it
is on, request.user becomes a copy of the admin with no staff flags, the «user» role and no
subscription, so views, the sidebar and templates take the same branches they take for a regular
account. Nothing is written to the database: saving the copy (or its profile) writes the real
flags and role back. request.real_user keeps the admin, and the exit URL always runs as the admin.
"""
from __future__ import annotations

import copy

from django.urls import reverse
from django.utils.translation import get_language_from_path

from modules.v2.system.request_cache import clear_request_cache

from .models import UserProfile, UserRole


SESSION_KEY = "caseup_view_as_user"
VIEWER_FLAG = "_caseup_view_as_user"
MASKED_USER_FIELDS = {"is_staff": False, "is_superuser": False}

# Django admin is reachable only for staff; while previewing it would show a login form instead.
SKIP_PREFIXES = ("/admin/", "/static/", "/media/")


def is_viewing_as_user(user) -> bool:
    return bool(getattr(user, VIEWER_FLAG, False))


def can_view_as_user(user) -> bool:
    from .rbac import get_user_role

    return bool(getattr(user, "is_authenticated", False)) and get_user_role(user).is_admin_plus


def _guard_save(instance, real_values: dict) -> None:
    """Any save of the masked copy stores the real values, so the preview never demotes anyone."""
    original_save = instance.save

    def save(*args, **kwargs):
        masked = {name: getattr(instance, name) for name in real_values}
        for name, value in real_values.items():
            setattr(instance, name, value)
        try:
            return original_save(*args, **kwargs)
        finally:
            for name, value in masked.items():
                setattr(instance, name, value)

    instance.save = save


def build_regular_user_view(real_user):
    viewer = copy.copy(real_user)
    _guard_save(viewer, {name: getattr(real_user, name) for name in MASKED_USER_FIELDS})
    for name, value in MASKED_USER_FIELDS.items():
        setattr(viewer, name, value)
    setattr(viewer, VIEWER_FLAG, True)

    try:
        real_profile = real_user.profile_v2
    except UserProfile.DoesNotExist:
        real_profile = None
    if real_profile is not None:
        profile = copy.copy(real_profile)
        _guard_save(profile, {"role": real_profile.role})
        profile.role = UserRole.USER
        profile.user = viewer
        viewer._state.fields_cache["profile_v2"] = profile
    return viewer


def _path_without_language(path: str) -> str:
    language = get_language_from_path(path)
    if language and (path == f"/{language}" or path.startswith(f"/{language}/")):
        return path[len(language) + 1:] or "/"
    return path


class ViewAsUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.real_user = request.user
        request.view_as_user = False
        if request.session.get(SESSION_KEY):
            if not can_view_as_user(request.user):
                request.session.pop(SESSION_KEY, None)
            elif not self._skip(request):
                real_user = getattr(request.user, "_wrapped", request.user)
                request.user = build_regular_user_view(real_user)
                request.view_as_user = True
                # Lookups memoized earlier in this request were made for the admin.
                clear_request_cache()
        return self.get_response(request)

    @staticmethod
    def _skip(request) -> bool:
        path = _path_without_language(request.path_info)
        if path.startswith(SKIP_PREFIXES):
            return True
        return path == _path_without_language(reverse("control_center:view_as_user_exit"))


def view_as_user_context(request):
    return {
        "view_as_user_active": bool(getattr(request, "view_as_user", False)),
        "can_view_as_user": can_view_as_user(getattr(request, "real_user", None) or getattr(request, "user", None)),
    }
