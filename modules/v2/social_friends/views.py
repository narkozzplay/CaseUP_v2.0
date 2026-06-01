from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from modules.v2.profiles.presentation import get_profile_avatar_url, get_profile_display_name
from modules.v2.profiles.services.presence import is_profile_online
from modules.v2.system.dashboard_shell import render_dashboard_page
from modules.v2.system.services import build_page_title

from .forms import PrivacySettingsForm
from .models import FriendRequest
from .services import (
    accept_friend_request,
    block_user,
    cancel_friend_request,
    decline_friend_request,
    get_friends_queryset,
    get_incoming_requests,
    get_or_create_privacy_settings,
    get_outgoing_requests,
    remove_friend,
    send_friend_request,
    unblock_user,
    update_privacy_settings,
)


User = get_user_model()


def _build_user_row(user, *, viewer=None) -> dict:
    profile = getattr(user, "profile_v2", None)
    return {
        "user": user,
        "user_id": user.pk,
        "display_name": get_profile_display_name(profile, fallback_username=user.username),
        "avatar_url": get_profile_avatar_url(profile),
        "is_online": is_profile_online(profile),
    }


def _build_request_row(req: FriendRequest, *, direction: str) -> dict:
    if direction == "incoming":
        other_user = req.from_user
    else:
        other_user = req.to_user
    profile = getattr(other_user, "profile_v2", None)
    return {
        "request": req,
        "request_id": req.pk,
        "other_user": other_user,
        "display_name": get_profile_display_name(profile, fallback_username=other_user.username),
        "avatar_url": get_profile_avatar_url(profile),
        "is_online": is_profile_online(profile),
        "created_at": req.created_at,
        "direction": direction,
    }


@login_required
def friends_view(request):
    friends_qs = get_friends_queryset(request.user)
    friend_rows = [_build_user_row(u) for u in friends_qs]
    return render_dashboard_page(
        request,
        full_template="social_friends/friends.html",
        partial_template="social_friends/partials/friends_content.html",
        context={
            "friend_rows": friend_rows,
            "friend_count": len(friend_rows),
            "social_active_key": "social_friends",
        },
        page_title=build_page_title("Друзья"),
        active_menu_key="social_friends",
    )


@login_required
def friend_requests_view(request):
    incoming_qs = get_incoming_requests(request.user)
    outgoing_qs = get_outgoing_requests(request.user)
    incoming_rows = [_build_request_row(r, direction="incoming") for r in incoming_qs]
    outgoing_rows = [_build_request_row(r, direction="outgoing") for r in outgoing_qs]
    return render_dashboard_page(
        request,
        full_template="social_friends/friend_requests.html",
        partial_template="social_friends/partials/friend_requests_content.html",
        context={
            "incoming_rows": incoming_rows,
            "outgoing_rows": outgoing_rows,
            "incoming_count": len(incoming_rows),
            "outgoing_count": len(outgoing_rows),
            "social_active_key": "social_friends",
        },
        page_title=build_page_title("Заявки в друзья"),
        active_menu_key="social_friends",
    )


@login_required
def send_friend_request_view(request, user_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id, is_active=True)
    try:
        send_friend_request(request.user, target)
        messages.success(request, "Заявка отправлена.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "social_friends:friends")


@login_required
def cancel_friend_request_view(request, request_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        cancel_friend_request(request.user, request_id)
        messages.success(request, "Заявка отозвана.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "social_friends:friend_requests")


@login_required
def accept_friend_request_view(request, request_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        accept_friend_request(request.user, request_id)
        messages.success(request, "Заявка принята. Теперь вы друзья.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "social_friends:friend_requests")


@login_required
def decline_friend_request_view(request, request_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        decline_friend_request(request.user, request_id)
        messages.success(request, "Заявка отклонена.")
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "social_friends:friend_requests")


@login_required
def remove_friend_view(request, user_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id)
    remove_friend(request.user, target)
    messages.success(request, "Пользователь удалён из друзей.")
    return redirect(request.POST.get("next") or "social_friends:friends")


@login_required
def block_user_view(request, user_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id, is_active=True)
    try:
        block_user(request.user, target)
        messages.success(request, "Пользователь заблокирован.")
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "social_friends:friends")


@login_required
def unblock_user_view(request, user_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id)
    unblock_user(request.user, target)
    messages.success(request, "Пользователь разблокирован.")
    return redirect(request.POST.get("next") or "social_friends:friends")


@login_required
def privacy_settings_view(request):
    settings_obj = get_or_create_privacy_settings(request.user)
    if request.method == "POST":
        form = PrivacySettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            update_privacy_settings(
                request.user,
                who_can_send_friend_request=form.cleaned_data["who_can_send_friend_request"],
                who_can_send_direct=form.cleaned_data["who_can_send_direct"],
                who_can_see_online=form.cleaned_data["who_can_see_online"],
                who_can_see_profile=form.cleaned_data["who_can_see_profile"],
            )
            messages.success(request, "Настройки приватности сохранены.")
            return redirect("social_friends:privacy_settings")
    else:
        form = PrivacySettingsForm(instance=settings_obj)

    return render_dashboard_page(
        request,
        full_template="social_friends/privacy_settings.html",
        partial_template="social_friends/partials/privacy_settings_content.html",
        context={
            "form": form,
            "social_active_key": "social_friends",
        },
        page_title=build_page_title("Настройки приватности"),
        active_menu_key="social_friends",
    )
