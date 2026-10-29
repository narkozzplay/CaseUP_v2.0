from __future__ import annotations

import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.v2.profiles.rbac import get_user_role
from modules.v2.subscriptions.feature_codes import PROFILE_CUSTOMIZATION
from modules.v2.subscriptions.services import user_has_feature
from modules.v2.system.request_cache import request_memoize

from .effects import (
    SLOT_ACCENT,
    SLOT_FRAME,
    SLOT_HERO,
    SLOT_NAME,
    SLOT_TITLE,
    SLOTS,
    ProfileEffect,
    get_effect,
    get_kit,
)
from .models import EffectGrant, ShowcaseItem, SteamAvatar, SteamBackground, UserCustomization


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def can_customize_profile(user) -> bool:
    """True if admin_plus OR store_author OR a plan with the profile_customization feature (Pro)."""
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
    return user_has_feature(user, PROFILE_CUSTOMIZATION)


def _require_can_customize(user) -> None:
    if not can_customize_profile(user):
        raise PermissionError("Кастомизация профиля недоступна. Требуется тариф Pro или права admin.")


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


# ---------------------------------------------------------------------------
# CaseUP effects (avatar frame, nickname, profile header, title, accent)
# ---------------------------------------------------------------------------

EFFECT_FIELDS = {
    SLOT_FRAME: "avatar_frame",
    SLOT_NAME: "name_effect",
    SLOT_HERO: "hero_effect",
    SLOT_TITLE: "title_effect",
    SLOT_ACCENT: "accent_effect",
}

def owned_effects(user) -> set[tuple[str, str]]:
    """(slot, key) of the reward effects the user has earned."""
    if not getattr(user, "pk", None):
        return set()
    return set(EffectGrant.objects.filter(user=user).values_list("slot", "key"))


def _is_admin_plus(user) -> bool:
    return bool(getattr(user, "is_authenticated", False)) and get_user_role(user).is_admin_plus


def can_wear(user, effect: ProfileEffect, *, can_customize: bool | None = None, owned: set | None = None) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if effect.is_free:
        return True
    if effect.is_pro:
        return can_customize_profile(user) if can_customize is None else can_customize
    if _is_admin_plus(user):
        return True
    owned = owned_effects(user) if owned is None else owned
    return (effect.slot, effect.key) in owned


def _locked_message(effect: ProfileEffect) -> str:
    if effect.is_reward:
        how = f" {effect.how_ru}." if effect.how_ru else ""
        return f"«{effect.title_ru}» — награда, её нужно заработать.{how}"
    return "Этот эффект входит в тариф Pro."


def set_effect(user, slot: str, key: str) -> UserCustomization:
    """Set (or clear with an empty key) one effect slot for the user."""
    if not getattr(user, "is_authenticated", False):
        raise PermissionError("Нужен вход в аккаунт.")
    if slot not in SLOTS:
        raise ValueError("Неизвестный тип эффекта.")
    clean_key = str(key or "").strip()
    if clean_key:
        effect = get_effect(slot, clean_key)
        if effect is None:
            raise ValueError("Эффект не найден.")
        if not can_wear(user, effect):
            raise PermissionError(_locked_message(effect))
    field = EFFECT_FIELDS[slot]
    customization = get_or_create_customization(user)
    setattr(customization, field, clean_key)
    customization.save(update_fields=[field, "updated_at"])
    return customization


def apply_kit(user, kit_key: str) -> UserCustomization:
    """Put on every part of a kit at once; all parts must be available."""
    if not getattr(user, "is_authenticated", False):
        raise PermissionError("Нужен вход в аккаунт.")
    kit = get_kit(kit_key)
    if kit is None:
        raise ValueError("Комплект не найден.")
    can_customize = can_customize_profile(user)
    owned = owned_effects(user)
    locked = [effect for effect in kit.effects if not can_wear(user, effect, can_customize=can_customize, owned=owned)]
    if locked:
        names = ", ".join(f"«{effect.title_ru}»" for effect in locked)
        raise PermissionError(f"В комплекте есть недоступные эффекты: {names}.")
    customization = get_or_create_customization(user)
    fields = []
    for effect in kit.effects:
        field = EFFECT_FIELDS[effect.slot]
        setattr(customization, field, effect.key)
        fields.append(field)
    customization.save(update_fields=[*fields, "updated_at"])
    return customization


def grant_effect(user, slot: str, key: str, *, source: str = "admin", note: str = "", send_notification: bool = True) -> EffectGrant | None:
    """Give the user a reward effect. Returns None when it was already theirs."""
    effect = get_effect(slot, key)
    if effect is None:
        raise ValueError("Эффект не найден.")
    try:
        with transaction.atomic():
            grant = EffectGrant.objects.create(user=user, slot=slot, key=key, source=source[:64], note=note[:255])
    except IntegrityError:
        return None
    if send_notification:
        from .notifications import notify_effect_unlocked

        notify_effect_unlocked(grant)
    return grant


def sync_achievements(user) -> list[EffectGrant]:
    """Grant every achievement effect the user now qualifies for."""
    from .achievements import due_achievements

    if not getattr(user, "pk", None):
        return []
    new = []
    for achievement in due_achievements(user, owned_effects(user)):
        grant = grant_effect(user, achievement.slot, achievement.effect_key, source=f"achievement:{achievement.key}")
        if grant is not None:
            new.append(grant)
    return new


def sync_recent_achievements() -> int:
    """Hourly: achievements for people seen in the last two hours, so the unlock notification comes on its own.

    Users who already own every achievement effect are skipped without running the counters.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Count, Q

    from .achievements import ACHIEVEMENTS, recently_active_cutoff

    refs = Q()
    for achievement in ACHIEVEMENTS:
        refs |= Q(effect_grants__slot=achievement.slot, effect_grants__key=achievement.effect_key)
    total = len({(a.slot, a.effect_key) for a in ACHIEVEMENTS})
    granted = 0
    users = (
        get_user_model().objects.filter(profile_v2__last_seen_at__gte=recently_active_cutoff(), is_active=True)
        .annotate(owned=Count("effect_grants", filter=refs, distinct=True))
        .filter(owned__lt=total)
    )
    for user in users.iterator():
        granted += len(sync_achievements(user))
    return granted


def mark_grants_seen(user) -> None:
    EffectGrant.objects.filter(user=user, seen_at__isnull=True).update(seen_at=timezone.now())


def _compute_look(customization: UserCustomization | None, *, owned: set | None = None, has_pro: bool | None = None) -> dict:
    """{slot: key} of the effects the profile may show right now."""
    if customization is None:
        return {}
    picked = {slot: get_effect(slot, getattr(customization, field)) for slot, field in EFFECT_FIELDS.items()}
    picked = {slot: effect for slot, effect in picked.items() if effect is not None}
    if not picked:
        return {}
    user = customization.user
    if any(effect.is_pro or effect.is_reward for effect in picked.values()):
        if not any(effect.is_pro for effect in picked.values()):
            can_customize = False
        elif has_pro:
            can_customize = True
        else:
            can_customize = can_customize_profile(user)
        if owned is None:
            owned = owned_effects(user) if any(effect.is_reward for effect in picked.values()) else set()
        picked = {
            slot: effect
            for slot, effect in picked.items()
            if can_wear(user, effect, can_customize=can_customize, owned=owned)
        }
    return {slot: effect.key for slot, effect in picked.items()}


@request_memoize
def _look_keys(user_id) -> dict:
    """Memoized per request: a chat or a friends list shows the same people many times."""
    customization = (
        UserCustomization.objects.filter(user_id=user_id)
        .select_related("user", "user__profile_v2")
        .first()
    )
    return _compute_look(customization)


def _users_with_pro_plan(user_ids) -> set[int]:
    """Users with an active plan (any family) that includes profile customization, in one query.

    Only a positive answer is trusted; everyone else still goes through can_customize_profile,
    which also covers staff, store authors and lapsed plans.
    """
    from django.db.models import Q

    from modules.v2.subscriptions.models import UserSubscription

    ids = set(user_ids)
    if not ids:
        return set()
    now = timezone.now()
    return set(
        UserSubscription.objects.filter(
            user_id__in=ids,
            status=UserSubscription.Status.ACTIVE,
            plan__is_active=True,
            plan__features__code=PROFILE_CUSTOMIZATION,
            plan__features__is_active=True,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .values_list("user_id", flat=True)
    )


def prime_looks(user_ids) -> None:
    """Load the looks of many users in two queries, for lists (chat messages, friends, leaderboards).

    Fills the per-request memo of _look_keys, so the template tag finds them ready. Outside a
    request there is no memo and this does nothing.
    """
    from modules.v2.system.request_cache import _request_cache

    memo = _request_cache.get()
    if memo is None:
        return
    ids = {int(uid) for uid in user_ids if uid}
    key = lambda uid: (_look_keys.__module__, _look_keys.__qualname__, (uid,), ())  # noqa: E731 - same key as request_memoize
    ids = {uid for uid in ids if key(uid) not in memo}
    if not ids:
        return
    customizations = {
        c.user_id: c
        for c in UserCustomization.objects.filter(user_id__in=ids).select_related("user", "user__profile_v2")
    }
    pro_ids = _users_with_pro_plan(
        c.user_id for c in customizations.values()
        if any((effect := get_effect(slot, getattr(c, field))) and effect.is_pro for slot, field in EFFECT_FIELDS.items())
    )
    grants: dict[int, set] = {}
    for user_id, slot, effect_key in EffectGrant.objects.filter(user_id__in=customizations.keys()).values_list("user_id", "slot", "key"):
        grants.setdefault(user_id, set()).add((slot, effect_key))
    for uid in ids:
        customization = customizations.get(uid)
        memo[key(uid)] = (
            _compute_look(customization, owned=grants.get(uid, set()), has_pro=uid in pro_ids) if customization else {}
        )


def get_profile_look(user_or_id) -> dict:
    """What a profile shows: CSS classes for frame/name/hero/accent and the title effect.

    {"frame": "pfx-frame--neon", "name": "", "hero": "", "accent": "", "title": ProfileEffect | None}
    Pro effects show only while the owner still has access, rewards while the grant exists.
    """
    look = {slot: "" for slot in SLOTS}
    look[SLOT_TITLE] = None
    user_id = getattr(user_or_id, "pk", user_or_id)
    if not user_id:
        return look
    for slot, key in _look_keys(user_id).items():
        effect = get_effect(slot, key)
        if effect is None:
            continue
        look[slot] = effect if slot == SLOT_TITLE else effect.css_class
    return look


# Older name, used by profile and CS2 stats views.
get_profile_effect_classes = get_profile_look


# ---------------------------------------------------------------------------
# Showcase: skins pinned to the profile
# ---------------------------------------------------------------------------

def showcase_candidates(user, *, limit: int = 60) -> list:
    """Items already pinned, then those the user follows: favorites, price alerts and investments.

    Pinned items stay in the list even after the favorite or alert behind them is gone,
    so they can always be unpinned.
    """
    from modules.v2.steam_catalog.models import SteamItem, SteamItemFavorite, UserPriceAlertRule
    from modules.v2.steam_trading.models import SteamPurchase

    ids: list[int] = []
    for qs in (
        ShowcaseItem.objects.filter(user=user).order_by("position", "id").values_list("item_id", flat=True),
        SteamItemFavorite.objects.filter(user=user).order_by("-created_at").values_list("item_id", flat=True),
        UserPriceAlertRule.objects.filter(user=user, item__isnull=False).order_by("-id").values_list("item_id", flat=True),
        SteamPurchase.objects.filter(created_by=user).order_by("-purchased_at").values_list("item_id", flat=True),
    ):
        for item_id in qs[:limit]:
            if item_id not in ids:
                ids.append(item_id)
    items = SteamItem.objects.in_bulk(ids[:limit])
    return [items[item_id] for item_id in ids[:limit] if item_id in items]


def set_showcase(user, item_ids) -> list[ShowcaseItem]:
    if not getattr(user, "is_authenticated", False):
        raise PermissionError("Нужен вход в аккаунт.")
    clean: list[int] = []
    for raw in item_ids or []:
        try:
            item_id = int(raw)
        except (TypeError, ValueError):
            continue
        if item_id not in clean:
            clean.append(item_id)
    if len(clean) > ShowcaseItem.MAX_ITEMS:
        raise ValueError(f"В витрину помещается до {ShowcaseItem.MAX_ITEMS} предметов.")
    allowed = {item.pk for item in showcase_candidates(user, limit=500)}
    if any(item_id not in allowed for item_id in clean):
        raise ValueError("В витрину можно поставить только предметы из избранного, уведомлений или инвестиций.")
    with transaction.atomic():
        ShowcaseItem.objects.filter(user=user).delete()
        rows = [ShowcaseItem(user=user, item_id=item_id, position=index) for index, item_id in enumerate(clean)]
        ShowcaseItem.objects.bulk_create(rows)
    return rows


def showcase_for(user) -> list[dict]:
    """Pinned items with the current price and the change over the last 7 days."""
    from datetime import timedelta
    from decimal import Decimal

    from modules.v2.steam_catalog.models import SteamItemPriceHistory

    if not getattr(user, "pk", None):
        return []
    rows = list(ShowcaseItem.objects.filter(user=user).select_related("item").order_by("position", "id"))
    week_ago = timezone.now() - timedelta(days=7)
    out = []
    for row in rows:
        item = row.item
        price = item.price_rub
        old = (
            SteamItemPriceHistory.objects.filter(item=item, created_at__lte=week_ago)
            .order_by("-created_at")
            .values_list("price_rub", flat=True)
            .first()
        )
        change = None
        if price is not None and old:
            change = ((Decimal(price) - Decimal(old)) / Decimal(old) * 100).quantize(Decimal("0.1"))
        out.append({"item": item, "price": price, "change": change})
    return out
