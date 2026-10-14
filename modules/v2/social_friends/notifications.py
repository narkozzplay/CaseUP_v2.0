"""Notification kinds emitted by the friends app."""
from __future__ import annotations

from django.urls import reverse

from modules.v2.notifications.registry import NotificationAction, NotificationView, register_kind

KIND_FRIEND_REQUEST = "friend_request"
KIND_FRIEND_ACCEPTED = "friend_request_accepted"


def _actor_name(notification) -> str:
    from modules.v2.profiles.presentation import get_profile_display_name

    actor = notification.actor
    if actor is None:
        return "—"
    return get_profile_display_name(getattr(actor, "profile_v2", None), fallback_username=actor.username)


def _actor_avatar(notification) -> str:
    from modules.v2.profiles.presentation import get_profile_avatar_url

    actor = notification.actor
    return get_profile_avatar_url(getattr(actor, "profile_v2", None) if actor else None)


def render_friend_request(notification, lang: str) -> NotificationView:
    from .models import FriendRequest

    ru = lang == "ru"
    name = _actor_name(notification)
    status = (
        FriendRequest.objects.filter(pk=notification.object_id, to_user_id=notification.recipient_id)
        .values_list("status", flat=True)
        .first()
    )

    actions: tuple[NotificationAction, ...] = ()
    state_label = ""
    if status == FriendRequest.Status.PENDING:
        actions = (
            NotificationAction(
                label="Принять" if ru else "Accept",
                url=reverse("social_friends:accept_friend_request", args=[notification.object_id]),
                style="primary",
            ),
            NotificationAction(
                label="Отклонить" if ru else "Decline",
                url=reverse("social_friends:decline_friend_request", args=[notification.object_id]),
                style="secondary",
            ),
        )
    elif status == FriendRequest.Status.ACCEPTED:
        state_label = "Принята" if ru else "Accepted"
    elif status == FriendRequest.Status.DECLINED:
        state_label = "Отклонена" if ru else "Declined"
    else:
        state_label = "Неактуальна" if ru else "No longer active"

    return NotificationView(
        title=f"{name} хочет добавить вас в друзья" if ru else f"{name} wants to be your friend",
        body="Заявка в друзья" if ru else "Friend request",
        url=reverse("social_friends:friend_requests"),
        icon="user_plus",
        actor_avatar_url=_actor_avatar(notification),
        actions=actions,
        state_label=state_label,
    )


def render_friend_accepted(notification, lang: str) -> NotificationView:
    ru = lang == "ru"
    name = _actor_name(notification)
    url = reverse("profiles:profile_detail", args=[notification.actor_id]) if notification.actor_id else ""
    return NotificationView(
        title=f"{name} принял(а) вашу заявку в друзья" if ru else f"{name} accepted your friend request",
        body="Теперь вы друзья" if ru else "You are now friends",
        url=url or reverse("social_friends:friends"),
        icon="friends",
        actor_avatar_url=_actor_avatar(notification),
    )


def register_notification_kinds() -> None:
    register_kind(KIND_FRIEND_REQUEST, render_friend_request)
    register_kind(KIND_FRIEND_ACCEPTED, render_friend_accepted)
