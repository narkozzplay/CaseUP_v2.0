from __future__ import annotations

from django.templatetags.static import static

from .models import UserProfile, UserRole
from .rbac import get_user_role

DEFAULT_NO_IMAGE_STATIC_PATH = "img/placeholders/no_image.svg"

ROLE_PREFIX_BY_CODE = {
    UserRole.SUPER_ADMIN: "SUPER ADMIN",
    UserRole.CHIEF_ADMIN: "CHIEF ADMIN",
    UserRole.ADMIN: "ADMIN",
    UserRole.MOD: "MODERATOR",
    UserRole.FARMER: "FARMER",
    UserRole.USER: "USER",
}


def get_default_no_image_url() -> str:
    return static(DEFAULT_NO_IMAGE_STATIC_PATH)


def _safe_file_url(file_field) -> str:
    if not file_field:
        return ""
    try:
        return str(file_field.url or "").strip()
    except Exception:
        return ""


def get_profile_display_name(profile: UserProfile | None, *, fallback_username: str = "") -> str:
    steam_name = str(getattr(profile, "steam_personaname", "") or "").strip() if profile else ""
    fallback = str(fallback_username or "").strip()
    return steam_name or fallback or "Unknown"


def get_user_display_name(user) -> str:
    if user is None:
        return "Unknown"

    fallback_username = str(getattr(user, "username", "") or "").strip()
    profile = getattr(user, "profile_v2", None)
    if profile is None and getattr(user, "is_authenticated", False):
        try:
            profile = user.profile_v2
        except UserProfile.DoesNotExist:
            profile = None
    return get_profile_display_name(profile, fallback_username=fallback_username)


def get_profile_avatar_url(profile: UserProfile | None) -> str:
    if profile is None:
        return get_default_no_image_url()

    uploaded_avatar_url = _safe_file_url(getattr(profile, "uploaded_avatar", None))
    if uploaded_avatar_url:
        return uploaded_avatar_url

    steam_avatar_url = str(getattr(profile, "steam_avatar", "") or "").strip()
    if steam_avatar_url:
        return steam_avatar_url

    return get_default_no_image_url()


def get_user_avatar_url(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return get_default_no_image_url()

    profile = getattr(user, "profile_v2", None)
    if profile is None:
        try:
            profile = user.profile_v2
        except UserProfile.DoesNotExist:
            return get_default_no_image_url()

    return get_profile_avatar_url(profile)


def get_user_role_prefix(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return "[USER]"

    access = get_user_role(user)
    profile = getattr(user, "profile_v2", None)
    if profile is None:
        try:
            profile = user.profile_v2
        except UserProfile.DoesNotExist:
            profile = None

    if access.code in ROLE_PREFIX_BY_CODE:
        role_label = ROLE_PREFIX_BY_CODE[access.code]
    elif profile and profile.is_store_author:
        role_label = "STORE AUTHOR"
    else:
        role_label = "USER"

    if role_label == "USER" and profile and profile.is_store_author:
        role_label = "STORE AUTHOR"

    return f"[{role_label}]"

