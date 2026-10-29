from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from modules.v2.profiles.models import UserProfile
from modules.v2.profiles.presentation import get_profile_avatar_url, get_profile_display_name
from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .achievements import progress as achievement_progress
from .effects import KITS, SLOT_LABELS, SLOTS, effects_for_slot, get_effect
from .forms import AddAvatarForm, AddBackgroundForm
from .models import EffectGrant, ShowcaseItem, UserCustomization
from .services import (
    EFFECT_FIELDS,
    add_avatar,
    add_background,
    apply_kit,
    can_customize_profile,
    can_wear,
    clear_avatar,
    clear_background,
    delete_avatar,
    delete_background,
    get_or_create_customization,
    list_avatars,
    list_backgrounds,
    mark_grants_seen,
    restore_avatar,
    restore_background,
    set_avatar,
    set_background,
    set_effect,
    set_showcase,
    showcase_candidates,
    sync_achievements,
)


CATALOG_TABS = ("effects", "showcase", "backgrounds", "avatars", "hidden")
FX_SLOT_ORDER = ("frame", "name", "title", "hero", "accent")


def _effect_groups(user, customization: UserCustomization, can_customize: bool) -> tuple[list, dict, list]:
    """Effect tiles grouped by slot, each with its state for this user, plus the kits."""
    grants = {(g.slot, g.key): g for g in EffectGrant.objects.filter(user=user)}
    owned = set(grants)
    have_need = achievement_progress(user, owned)
    current = {slot: get_effect(slot, getattr(customization, field)) for slot, field in EFFECT_FIELDS.items()}
    groups = []
    for slot in FX_SLOT_ORDER:
        tiles = []
        for fx in effects_for_slot(slot):
            wearable = can_wear(user, fx, can_customize=can_customize, owned=owned)
            if wearable:
                state = "open"
            elif fx.is_pro:
                state = "pro"
            else:
                state = "reward"
            grant = grants.get((fx.slot, fx.key))
            have, need = have_need.get((fx.slot, fx.key), (0, 0))
            tiles.append({
                "fx": fx,
                "state": state,
                "is_current": current[slot] is not None and current[slot].key == fx.key,
                "is_new": bool(grant and grant.seen_at is None),
                "grant": grant,
                "have": have,
                "need": need,
                "percent": int(have * 100 / need) if need else 0,
            })
        groups.append({
            "slot": slot,
            "title_ru": SLOT_LABELS[slot][0],
            "title_en": SLOT_LABELS[slot][1],
            "tiles": tiles,
            "current": current[slot],
            "open_count": sum(1 for tile in tiles if tile["state"] == "open"),
        })
    kits = []
    for kit in KITS:
        parts = [(fx, can_wear(user, fx, can_customize=can_customize, owned=owned)) for fx in kit.effects]
        kits.append({
            "kit": kit,
            "parts": parts,
            "is_open": all(ok for _fx, ok in parts),
            "is_current": all(current[fx.slot] is not None and current[fx.slot].key == fx.key for fx, _ok in parts),
        })
    return groups, current, kits


def _redirect_back(request, *, tab: str = ""):
    """Redirect to the POSTed `next` only when it points to this site."""
    next_url = request.POST.get("next") or ""
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    url = reverse("profile_customization:catalog")
    return redirect(f"{url}?tab={tab}" if tab else url)


@login_required
def catalog_view(request):
    user = request.user
    can_customize = can_customize_profile(user)
    is_admin = get_user_role(user).is_admin_plus
    customization: UserCustomization = get_or_create_customization(user)

    tab = request.GET.get("tab") or "effects"
    if tab not in CATALOG_TABS or (tab == "hidden" and not is_admin):
        tab = "effects"

    backgrounds = list(list_backgrounds())
    avatars = list(list_avatars())
    hidden_backgrounds = []
    hidden_avatars = []
    if is_admin:
        hidden_backgrounds = list(list_backgrounds(active_only=False).filter(is_active=False))
        hidden_avatars = list(list_avatars(active_only=False).filter(is_active=False))

    profile = UserProfile.objects.filter(user=user).first()
    selected_bg = customization.selected_background
    selected_av = customization.selected_avatar
    # What the profile hero shows right now: catalog pick first, then the Steam background.
    current_bg_static = selected_bg.static_url if selected_bg else str(getattr(profile, "steam_profile_background", "") or "")
    current_bg_video = selected_bg.video_url if selected_bg else str(getattr(profile, "steam_profile_background_video", "") or "")

    new_grants = sync_achievements(user)
    for grant in new_grants:
        effect = get_effect(grant.slot, grant.key)
        if effect:
            messages.success(request, f"Открыто новое оформление: «{effect.title_ru}».")
    effect_groups, current_effects, kits = _effect_groups(user, customization, can_customize)
    new_count = sum(1 for group in effect_groups for tile in group["tiles"] if tile["is_new"])

    showcase_ids = list(ShowcaseItem.objects.filter(user=user).order_by("position", "id").values_list("item_id", flat=True))
    candidates = showcase_candidates(user) if tab == "showcase" else []

    response = render_dashboard_page(
        request,
        full_template="profile_customization/catalog.html",
        partial_template="profile_customization/partials/catalog_content.html",
        context={
            "active_tab": tab,
            "effect_groups": effect_groups,
            "current_effects": current_effects,
            "effects_count": sum(len(group["tiles"]) for group in effect_groups),
            "open_effects_count": sum(group["open_count"] for group in effect_groups),
            "new_effects_count": new_count,
            "kits": kits,
            "showcase_ids": showcase_ids,
            "showcase_candidates": candidates,
            "showcase_max": ShowcaseItem.MAX_ITEMS,
            "backgrounds": backgrounds,
            "avatars": avatars,
            "hidden_backgrounds": hidden_backgrounds,
            "hidden_avatars": hidden_avatars,
            "hidden_count": len(hidden_backgrounds) + len(hidden_avatars),
            "can_customize": can_customize,
            "selected_bg": selected_bg,
            "selected_av": selected_av,
            "selected_bg_id": customization.selected_background_id,
            "selected_av_id": customization.selected_avatar_id,
            "current_bg_static": current_bg_static,
            "current_bg_video": current_bg_video,
            "current_avatar_url": get_profile_avatar_url(profile),
            "preview_display_name": get_profile_display_name(profile, fallback_username=user.username),
            "is_admin": is_admin,
            "add_background_form": AddBackgroundForm(auto_id="bg_%s") if is_admin else None,
            "add_avatar_form": AddAvatarForm(auto_id="av_%s") if is_admin else None,
            "social_active_key": "customization_catalog",
        },
        page_title=build_page_title("Оформление профиля"),
        active_menu_key="customization_catalog",
    )
    if tab == "effects" and new_count:
        mark_grants_seen(user)
    return response


@login_required
def admin_add_background_view(request):
    if not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("Только admin+ может добавлять фоны.")
    if request.method == "POST":
        form = AddBackgroundForm(request.POST)
        if form.is_valid():
            try:
                add_background(
                    request.user,
                    title=form.cleaned_data["title"],
                    static_url=form.cleaned_data["static_url"],
                    video_url=form.cleaned_data["video_url"],
                )
                messages.success(request, "Фон добавлен в каталог.")
                return _redirect_back(request, tab="backgrounds")
            except (ValueError, PermissionError) as exc:
                messages.error(request, str(exc))
    else:
        form = AddBackgroundForm()

    return render_dashboard_page(
        request,
        full_template="profile_customization/admin_add_background.html",
        partial_template="profile_customization/partials/admin_add_background_content.html",
        context={"form": form},
        page_title=build_page_title("Добавить фон"),
        active_menu_key="customization_catalog",
    )


@login_required
def admin_add_avatar_view(request):
    if not get_user_role(request.user).is_admin_plus:
        return HttpResponseForbidden("Только admin+ может добавлять аватарки.")
    if request.method == "POST":
        form = AddAvatarForm(request.POST)
        if form.is_valid():
            try:
                add_avatar(
                    request.user,
                    title=form.cleaned_data["title"],
                    avatar_url=form.cleaned_data["avatar_url"],
                )
                messages.success(request, "Аватарка добавлена в каталог.")
                return _redirect_back(request, tab="avatars")
            except (ValueError, PermissionError) as exc:
                messages.error(request, str(exc))
    else:
        form = AddAvatarForm()

    return render_dashboard_page(
        request,
        full_template="profile_customization/admin_add_avatar.html",
        partial_template="profile_customization/partials/admin_add_avatar_content.html",
        context={"form": form},
        page_title=build_page_title("Добавить аватарку"),
        active_menu_key="customization_catalog",
    )


@login_required
def select_background_view(request, background_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        set_background(request.user, background_id)
        messages.success(request, "Фон профиля обновлён.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="backgrounds")


@login_required
def clear_background_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        clear_background(request.user)
        messages.success(request, "Фон профиля сброшен.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="backgrounds")


@login_required
def select_avatar_view(request, avatar_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        set_avatar(request.user, avatar_id)
        messages.success(request, "Аватарка профиля обновлена.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="avatars")


@login_required
def clear_avatar_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        clear_avatar(request.user)
        messages.success(request, "Аватарка профиля сброшена.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="avatars")


@login_required
def admin_delete_background_view(request, background_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        delete_background(request.user, background_id)
        messages.success(request, "Фон скрыт из каталога.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="backgrounds")


@login_required
def admin_delete_avatar_view(request, avatar_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        delete_avatar(request.user, avatar_id)
        messages.success(request, "Аватарка скрыта из каталога.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="avatars")


@login_required
def admin_restore_background_view(request, background_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        restore_background(request.user, background_id)
        messages.success(request, "Фон снова в каталоге.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="hidden")


@login_required
def admin_restore_avatar_view(request, avatar_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        restore_avatar(request.user, avatar_id)
        messages.success(request, "Аватарка снова в каталоге.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="hidden")


@login_required
def select_effect_view(request, slot: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if slot not in SLOTS:
        return redirect("profile_customization:catalog")
    key = request.POST.get("key", "")
    try:
        set_effect(request.user, slot, key)
        messages.success(request, "Эффект применён." if key else "Эффект снят.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="effects")


@login_required
def apply_kit_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        apply_kit(request.user, request.POST.get("kit", ""))
        messages.success(request, "Комплект надет.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="effects")


@login_required
def save_showcase_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        rows = set_showcase(request.user, request.POST.getlist("items"))
        messages.success(request, "Витрина сохранена." if rows else "Витрина очищена.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return _redirect_back(request, tab="showcase")
