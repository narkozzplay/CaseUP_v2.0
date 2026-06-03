from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import redirect

from modules.v2.profiles.rbac import get_user_role
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

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
    set_avatar,
    set_background,
)


@login_required
def catalog_view(request):
    can_customize = can_customize_profile(request.user)
    customization: UserCustomization | None = None
    selected_bg_id: int | None = None
    selected_av_id: int | None = None

    if request.user.is_authenticated:
        customization = get_or_create_customization(request.user)
        selected_bg_id = customization.selected_background_id
        selected_av_id = customization.selected_avatar_id

    backgrounds = list(list_backgrounds())
    avatars = list(list_avatars())
    is_admin = get_user_role(request.user).is_admin_plus

    return render_dashboard_page(
        request,
        full_template="profile_customization/catalog.html",
        partial_template="profile_customization/partials/catalog_content.html",
        context={
            "backgrounds": backgrounds,
            "avatars": avatars,
            "can_customize": can_customize,
            "selected_bg_id": selected_bg_id,
            "selected_av_id": selected_av_id,
            "is_admin": is_admin,
            "social_active_key": "customization_catalog",
        },
        page_title=build_page_title("Каталог кастомизации"),
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
                return redirect("profile_customization:catalog")
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
                return redirect("profile_customization:catalog")
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
    return redirect(request.POST.get("next") or "profile_customization:catalog")


@login_required
def clear_background_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        clear_background(request.user)
        messages.success(request, "Фон профиля сброшен.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "profile_customization:catalog")


@login_required
def select_avatar_view(request, avatar_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        set_avatar(request.user, avatar_id)
        messages.success(request, "Аватарка профиля обновлена.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "profile_customization:catalog")


@login_required
def clear_avatar_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        clear_avatar(request.user)
        messages.success(request, "Аватарка профиля сброшена.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "profile_customization:catalog")


@login_required
def admin_delete_background_view(request, background_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        delete_background(request.user, background_id)
        messages.success(request, "Фон деактивирован.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "profile_customization:catalog")


@login_required
def admin_delete_avatar_view(request, avatar_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        delete_avatar(request.user, avatar_id)
        messages.success(request, "Аватарка деактивирована.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "profile_customization:catalog")
