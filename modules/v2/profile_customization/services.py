from __future__ import annotations

import re

from django.db import transaction

from modules.v2.profiles.rbac import get_user_role
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import get_active_subscription

from .models import SteamAvatar, SteamBackground, UserCustomization


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def can_customize_profile(user) -> bool:
    """True if admin_plus OR store_author OR active Site Level 5 (Pro)."""
    if not getattr(user, "is_authenticated", False):
        return False
    role = get_user_role(user)
    if role.is_admin_plus:
        return True
    profile = getattr(user, "profile_v2", None)
    if profile is None:
        try:
            profile = user.profile_v2
        except Exception:
            profile = None
    if profile and getattr(profile, "is_store_author", False):
        return True
    # Level 5 Pro Site plan
    sub = get_active_subscription(user, family=SubscriptionPlan.Family.SITE)
    return bool(sub and sub.plan and sub.plan.level >= 5)


def _require_can_customize(user) -> None:
    if not can_customize_profile(user):
        raise PermissionError("Кастомизация профиля недоступна. Требуется Level 5 Pro или права admin.")


def _require_admin_plus(user) -> None:
    if not get_user_role(user).is_admin_plus:
        raise PermissionError("Только admin+ может управлять каталогом.")


# ---------------------------------------------------------------------------
# UserCustomization
# ---------------------------------------------------------------------------

def get_or_create_customization(user) -> UserCustomization:
    obj, _ = UserCustomization.objects.get_or_create(user=user)
    return obj


# ---------------------------------------------------------------------------
# Background management
# ---------------------------------------------------------------------------

def _extract_source_item_id(url: str) -> str:
    """Extract Steam item ID from URL like .../items/2861720/hash.jpg"""
    match = re.search(r"/items/(\d+)/", str(url or ""))
    return match.group(1) if match else ""


def add_background(actor, *, title: str, static_url: str, video_url: str = "") -> SteamBackground:
    _require_admin_plus(actor)
    clean_title = str(title or "").strip()
    if not clean_title:
        raise ValueError("Название обязательно.")
    if not str(static_url or "").strip():
        raise ValueError("Нужна ссылка на изображение фона.")
    source_id = _extract_source_item_id(static_url) or _extract_source_item_id(video_url)
    return SteamBackground.objects.create(
        title=clean_title,
        static_url=str(static_url or "").strip(),
        video_url=str(video_url or "").strip(),
        source_item_id=source_id,
        added_by=actor,
    )


def delete_background(actor, background_id: int) -> None:
    _require_admin_plus(actor)
    with transaction.atomic():
        try:
            bg = SteamBackground.objects.get(pk=background_id)
        except SteamBackground.DoesNotExist:
            return
        # Deactivate and clear denormalized cache on UserProfile
        bg.is_active = False
        bg.save(update_fields=["is_active"])
        _clear_bg_from_profiles(background_id)


def _clear_bg_from_profiles(background_id: int) -> None:
    """Clear selected_bg_* on UserProfile for users who had this background."""
    from modules.v2.profiles.models import UserProfile
    affected_user_ids = list(
        UserCustomization.objects.filter(selected_background_id=background_id)
        .values_list("user_id", flat=True)
    )
    if affected_user_ids:
        UserProfile.objects.filter(user_id__in=affected_user_ids).update(
            selected_bg_static_url="",
            selected_bg_video_url="",
        )
    UserCustomization.objects.filter(selected_background_id=background_id).update(
        selected_background=None
    )


def restore_background(actor, background_id: int) -> None:
    _require_admin_plus(actor)
    SteamBackground.objects.filter(pk=background_id).update(is_active=True)


def list_backgrounds(*, active_only: bool = True):
    qs = SteamBackground.objects.select_related("added_by")
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by("-created_at")


def set_background(user, background_id: int | None) -> UserCustomization:
    _require_can_customize(user)
    with transaction.atomic():
        customization = get_or_create_customization(user)
        if background_id is None:
            customization.selected_background = None
            customization.save(update_fields=["selected_background", "updated_at"])
            _update_profile_bg_cache(user, "", "")
            return customization
        try:
            bg = SteamBackground.objects.get(pk=background_id, is_active=True)
        except SteamBackground.DoesNotExist:
            raise ValueError("Фон не найден или недоступен.")
        customization.selected_background = bg
        customization.save(update_fields=["selected_background", "updated_at"])
        _update_profile_bg_cache(user, bg.static_url, bg.video_url)
    return customization


def clear_background(user) -> UserCustomization:
    return set_background(user, None)


def _update_profile_bg_cache(user, static_url: str, video_url: str) -> None:
    from modules.v2.profiles.models import UserProfile
    UserProfile.objects.filter(user=user).update(
        selected_bg_static_url=static_url,
        selected_bg_video_url=video_url,
    )


# ---------------------------------------------------------------------------
# Avatar management
# ---------------------------------------------------------------------------

def add_avatar(actor, *, title: str, avatar_url: str) -> SteamAvatar:
    _require_admin_plus(actor)
    clean_title = str(title or "").strip()
    if not clean_title:
        raise ValueError("Название обязательно.")
    return SteamAvatar.objects.create(
        title=clean_title,
        avatar_url=str(avatar_url or "").strip(),
        added_by=actor,
    )


def delete_avatar(actor, avatar_id: int) -> None:
    _require_admin_plus(actor)
    with transaction.atomic():
        try:
            av = SteamAvatar.objects.get(pk=avatar_id)
        except SteamAvatar.DoesNotExist:
            return
        av.is_active = False
        av.save(update_fields=["is_active"])
        _clear_avatar_from_profiles(avatar_id)


def _clear_avatar_from_profiles(avatar_id: int) -> None:
    from modules.v2.profiles.models import UserProfile
    affected_user_ids = list(
        UserCustomization.objects.filter(selected_avatar_id=avatar_id)
        .values_list("user_id", flat=True)
    )
    if affected_user_ids:
        UserProfile.objects.filter(user_id__in=affected_user_ids).update(
            selected_avatar_url="",
        )
    UserCustomization.objects.filter(selected_avatar_id=avatar_id).update(
        selected_avatar=None
    )


def restore_avatar(actor, avatar_id: int) -> None:
    _require_admin_plus(actor)
    SteamAvatar.objects.filter(pk=avatar_id).update(is_active=True)


def list_avatars(*, active_only: bool = True):
    qs = SteamAvatar.objects.select_related("added_by")
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by("-created_at")


def set_avatar(user, avatar_id: int | None) -> UserCustomization:
    _require_can_customize(user)
    with transaction.atomic():
        customization = get_or_create_customization(user)
        if avatar_id is None:
            customization.selected_avatar = None
            customization.save(update_fields=["selected_avatar", "updated_at"])
            _update_profile_avatar_cache(user, "")
            return customization
        try:
            av = SteamAvatar.objects.get(pk=avatar_id, is_active=True)
        except SteamAvatar.DoesNotExist:
            raise ValueError("Аватарка не найдена или недоступна.")
        customization.selected_avatar = av
        customization.save(update_fields=["selected_avatar", "updated_at"])
        _update_profile_avatar_cache(user, av.avatar_url)
    return customization


def clear_avatar(user) -> UserCustomization:
    return set_avatar(user, None)


def _update_profile_avatar_cache(user, avatar_url: str) -> None:
    from modules.v2.profiles.models import UserProfile
    UserProfile.objects.filter(user=user).update(selected_avatar_url=avatar_url)
