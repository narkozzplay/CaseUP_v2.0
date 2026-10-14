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

from .effects import SLOT_FRAME, SLOT_HERO, SLOT_NAME, SLOTS, effects_for_slot, get_effect
from .forms import AddAvatarForm, AddBackgroundForm
from .models import UserCustomization
from .services import (
    add_avatar,
    add_background,
    can_customize_profile,
    clear_avatar,
    clear_background,
    delete_avatar,
    delete_background,
    get_or_create_customization,
    list_avatars,
    list_backgrounds,
    restore_avatar,
    restore_background,
    set_avatar,
    set_background,
    set_effect,
)


CATALOG_TABS = ("effects", "backgrounds", "avatars", "hidden")


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

    current_effects = {
        SLOT_FRAME: get_effect(SLOT_FRAME, customization.avatar_frame),
        SLOT_NAME: get_effect(SLOT_NAME, customization.name_effect),
        SLOT_HERO: get_effect(SLOT_HERO, customization.hero_effect),
    }
    effect_groups = [
        {
            "slot": slot,
            "title_ru": title_ru,
            "title_en": title_en,
            "effects": effects_for_slot(slot),
            "current": current_effects[slot],
        }
        for slot, title_ru, title_en in (
            (SLOT_FRAME, "Рамка аватарки", "Avatar frame"),
            (SLOT_NAME, "Ник", "Nickname"),
            (SLOT_HERO, "Шапка профиля", "Profile header"),
        )
    ]

    return render_dashboard_page(
        request,
        full_template="profile_customization/catalog.html",
        partial_template="profile_customization/partials/catalog_content.html",
        context={
            "active_tab": tab,
            "effect_groups": effect_groups,
            "current_effects": current_effects,
            "effects_count": sum(len(group["effects"]) for group in effect_groups),
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
