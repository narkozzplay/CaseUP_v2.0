from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q

from modules.v2.notifications.services import discard_notifications, notify, resolve_notifications

from .models import (
    DMPrivacy,
    Friendship,
    FriendRequest,
    FriendRequestPrivacy,
    UserBlock,
    UserPrivacySettings,
)
from .notifications import KIND_FRIEND_ACCEPTED, KIND_FRIEND_REQUEST


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalized_pair(user_a, user_b):
    """Return (lower_pk_user, higher_pk_user) for Friendship lookup."""
    if int(user_a.pk) < int(user_b.pk):
        return user_a, user_b
    return user_b, user_a


def _require_users(*users):
    for u in users:
        if not getattr(u, "pk", None):
            raise ValueError("All users must be saved instances.")


# ---------------------------------------------------------------------------
# Privacy settings
# ---------------------------------------------------------------------------

def get_or_create_privacy_settings(user) -> UserPrivacySettings:
    settings_obj, _ = UserPrivacySettings.objects.get_or_create(user=user)
    return settings_obj


def update_privacy_settings(
    user,
    *,
    who_can_send_friend_request: str | None = None,
    who_can_send_direct: str | None = None,
    who_can_see_online: str | None = None,
    who_can_see_profile: str | None = None,
) -> UserPrivacySettings:
    settings_obj = get_or_create_privacy_settings(user)
    update_fields = ["updated_at"]

    if who_can_send_friend_request is not None:
        valid = {c for c, _ in FriendRequestPrivacy.choices}
        if who_can_send_friend_request not in valid:
            raise ValueError(f"Invalid who_can_send_friend_request: {who_can_send_friend_request!r}")
        settings_obj.who_can_send_friend_request = who_can_send_friend_request
        update_fields.append("who_can_send_friend_request")

    if who_can_send_direct is not None:
        valid = {c for c, _ in DMPrivacy.choices}
        if who_can_send_direct not in valid:
            raise ValueError(f"Invalid who_can_send_direct: {who_can_send_direct!r}")
        settings_obj.who_can_send_direct = who_can_send_direct
        update_fields.append("who_can_send_direct")

    if who_can_see_online is not None:
        valid = {c for c, _ in DMPrivacy.choices}
        if who_can_see_online not in valid:
            raise ValueError(f"Invalid who_can_see_online: {who_can_see_online!r}")
        settings_obj.who_can_see_online = who_can_see_online
        update_fields.append("who_can_see_online")

    if who_can_see_profile is not None:
        valid = {c for c, _ in DMPrivacy.choices}
        if who_can_see_profile not in valid:
            raise ValueError(f"Invalid who_can_see_profile: {who_can_see_profile!r}")
        settings_obj.who_can_see_profile = who_can_see_profile
        update_fields.append("who_can_see_profile")

    settings_obj.save(update_fields=sorted(set(update_fields)))
    return settings_obj


# ---------------------------------------------------------------------------
# Friendship
# ---------------------------------------------------------------------------

def are_friends(user_a, user_b) -> bool:
    _require_users(user_a, user_b)
    low, high = _normalized_pair(user_a, user_b)
    return Friendship.objects.filter(user_a=low, user_b=high).exists()


def get_friends_queryset(user):
    """Return a queryset of User objects who are friends with user."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    friend_ids = (
        Friendship.objects.filter(Q(user_a=user) | Q(user_b=user))
        .values_list("user_a_id", "user_b_id")
    )
    flat_ids = set()
    for a_id, b_id in friend_ids:
        flat_ids.add(a_id if a_id != user.pk else b_id)
    return User.objects.filter(pk__in=flat_ids).select_related("profile_v2")


def _get_pending_sent(from_user, to_user) -> FriendRequest | None:
    try:
        return FriendRequest.objects.get(
            from_user=from_user,
            to_user=to_user,
            status=FriendRequest.Status.PENDING,
        )
    except FriendRequest.DoesNotExist:
        return None


def send_friend_request(from_user, to_user) -> FriendRequest:
    _require_users(from_user, to_user)
    if from_user.pk == to_user.pk:
        raise ValueError("Cannot send friend request to yourself.")

    if is_blocked(from_user, to_user) or is_blocked(to_user, from_user):
        raise PermissionError("Cannot send friend request due to block.")

    if are_friends(from_user, to_user):
        raise ValueError("Already friends.")

    privacy = get_or_create_privacy_settings(to_user)
    if privacy.who_can_send_friend_request == FriendRequestPrivacy.NOBODY:
        raise PermissionError("This user does not accept friend requests.")

    with transaction.atomic():
        # The other user already asked us: sending back means accepting their request.
        incoming = (
            FriendRequest.objects.select_for_update()
            .filter(from_user=to_user, to_user=from_user, status=FriendRequest.Status.PENDING)
            .first()
        )
        if incoming:
            accept_friend_request(from_user, incoming.pk)
            incoming.refresh_from_db()
            return incoming

        existing = (
            FriendRequest.objects.select_for_update()
            .filter(from_user=from_user, to_user=to_user)
            .first()
        )
        if existing:
            if existing.status == FriendRequest.Status.PENDING:
                return existing
            # Cancelled, declined, or accepted earlier and the friendship was removed
            # since: the row is unique per pair, so a new request re-opens it.
            existing.status = FriendRequest.Status.PENDING
            existing.save(update_fields=["status", "updated_at"])
            notify(to_user, KIND_FRIEND_REQUEST, actor=from_user, object_id=existing.pk)
            return existing

        try:
            with transaction.atomic():
                req = FriendRequest.objects.create(from_user=from_user, to_user=to_user)
        except IntegrityError:
            return FriendRequest.objects.get(from_user=from_user, to_user=to_user)
        notify(to_user, KIND_FRIEND_REQUEST, actor=from_user, object_id=req.pk)
        return req


def cancel_friend_request(user, request_id: int) -> FriendRequest:
    with transaction.atomic():
        try:
            req = FriendRequest.objects.select_for_update().get(
                pk=request_id,
                from_user=user,
                status=FriendRequest.Status.PENDING,
            )
        except FriendRequest.DoesNotExist:
            raise PermissionError("Friend request not found or cannot be cancelled.")
        req.status = FriendRequest.Status.CANCELLED
        req.save(update_fields=["status", "updated_at"])
        discard_notifications(KIND_FRIEND_REQUEST, req.pk, recipient=req.to_user)
        return req


def accept_friend_request(user, request_id: int) -> Friendship:
    with transaction.atomic():
        try:
            req = FriendRequest.objects.select_for_update().get(
                pk=request_id,
                to_user=user,
                status=FriendRequest.Status.PENDING,
            )
        except FriendRequest.DoesNotExist:
            raise PermissionError("Friend request not found or cannot be accepted.")

        req.status = FriendRequest.Status.ACCEPTED
        req.save(update_fields=["status", "updated_at"])

        low, high = _normalized_pair(req.from_user, req.to_user)
        friendship, _ = Friendship.objects.get_or_create(user_a=low, user_b=high)
        resolve_notifications(KIND_FRIEND_REQUEST, req.pk, recipient=req.to_user)
        notify(req.from_user, KIND_FRIEND_ACCEPTED, actor=req.to_user, object_id=req.pk)
        return friendship


def decline_friend_request(user, request_id: int) -> FriendRequest:
    with transaction.atomic():
        try:
            req = FriendRequest.objects.select_for_update().get(
                pk=request_id,
                to_user=user,
                status=FriendRequest.Status.PENDING,
            )
        except FriendRequest.DoesNotExist:
            raise PermissionError("Friend request not found or cannot be declined.")
        req.status = FriendRequest.Status.DECLINED
        req.save(update_fields=["status", "updated_at"])
        resolve_notifications(KIND_FRIEND_REQUEST, req.pk, recipient=req.to_user)
        return req


def remove_friend(user_a, user_b) -> bool:
    _require_users(user_a, user_b)
    low, high = _normalized_pair(user_a, user_b)
    deleted, _ = Friendship.objects.filter(user_a=low, user_b=high).delete()
    return bool(deleted)


def get_incoming_requests(user):
    return (
        FriendRequest.objects.filter(to_user=user, status=FriendRequest.Status.PENDING)
        .select_related("from_user", "from_user__profile_v2")
        .order_by("-created_at")
    )


def get_outgoing_requests(user):
    return (
        FriendRequest.objects.filter(from_user=user, status=FriendRequest.Status.PENDING)
        .select_related("to_user", "to_user__profile_v2")
        .order_by("-created_at")
    )


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

def is_blocked(blocker, blocked) -> bool:
    _require_users(blocker, blocked)
    return UserBlock.objects.filter(blocker=blocker, blocked=blocked).exists()


def get_block(blocker, blocked) -> UserBlock | None:
    try:
        return UserBlock.objects.get(blocker=blocker, blocked=blocked)
    except UserBlock.DoesNotExist:
        return None


def block_user(blocker, blocked) -> UserBlock:
    _require_users(blocker, blocked)
    if blocker.pk == blocked.pk:
        raise ValueError("Cannot block yourself.")

    with transaction.atomic():
        # Remove friendship if exists
        remove_friend(blocker, blocked)

        # Cancel any pending friend requests between them
        pending = FriendRequest.objects.filter(
            Q(from_user=blocker, to_user=blocked) | Q(from_user=blocked, to_user=blocker),
            status=FriendRequest.Status.PENDING,
        )
        for pending_id in list(pending.values_list("pk", flat=True)):
            discard_notifications(KIND_FRIEND_REQUEST, pending_id)
        pending.update(status=FriendRequest.Status.CANCELLED)

        try:
            with transaction.atomic():
                return UserBlock.objects.create(blocker=blocker, blocked=blocked)
        except IntegrityError:
            return UserBlock.objects.get(blocker=blocker, blocked=blocked)


def unblock_user(blocker, blocked) -> bool:
    _require_users(blocker, blocked)
    deleted, _ = UserBlock.objects.filter(blocker=blocker, blocked=blocked).delete()
    return bool(deleted)


# ---------------------------------------------------------------------------
# DM permission
# ---------------------------------------------------------------------------

def can_send_direct(sender, recipient) -> tuple[bool, str]:
    """
    Returns (allowed, reason_code).

    reason_code values:
      "self"              — same user
      "blocked_target"    — sender blocked recipient
      "blocked_by_target" — recipient blocked sender
      "friends"           — friends, always allowed
      "everyone"          — recipient's DM privacy is everyone
      "privacy_friends_only" — recipient allows friends only, not friends
      "privacy_nobody"    — recipient allows nobody
    """
    if not getattr(sender, "pk", None) or not getattr(recipient, "pk", None):
        return False, "invalid_users"
    if sender.pk == recipient.pk:
        return False, "self"
    if is_blocked(sender, recipient):
        return False, "blocked_target"
    if is_blocked(recipient, sender):
        return False, "blocked_by_target"
    if are_friends(sender, recipient):
        return True, "friends"

    privacy = get_or_create_privacy_settings(recipient)
    if privacy.who_can_send_direct == DMPrivacy.EVERYONE:
        return True, "everyone"
    if privacy.who_can_send_direct == DMPrivacy.FRIENDS_ONLY:
        return False, "privacy_friends_only"
    return False, "privacy_nobody"


# ---------------------------------------------------------------------------
# Profile context (for templates)
# ---------------------------------------------------------------------------

def get_friendship_context(viewer, target_user) -> dict:
    """Build friendship/block state dict for rendering a target user's profile."""
    if not getattr(viewer, "is_authenticated", False):
        return _empty_friendship_context()
    if viewer.pk == target_user.pk:
        return _empty_friendship_context(is_self=True)

    friends = are_friends(viewer, target_user)
    viewer_blocked_target = is_blocked(viewer, target_user)
    target_blocked_viewer = is_blocked(target_user, viewer)

    pending_sent: FriendRequest | None = None
    pending_received: FriendRequest | None = None

    if not friends and not viewer_blocked_target:
        pending_sent = _get_pending_sent(viewer, target_user)
        pending_received = _get_pending_sent(target_user, viewer)

    privacy = get_or_create_privacy_settings(target_user)
    can_request = (
        not friends
        and not viewer_blocked_target
        and not target_blocked_viewer
        and pending_sent is None
        and pending_received is None
        and privacy.who_can_send_friend_request != FriendRequestPrivacy.NOBODY
    )
    can_dm, _ = can_send_direct(viewer, target_user)

    return {
        "is_friend": friends,
        "is_blocked": viewer_blocked_target,
        "is_blocked_by": target_blocked_viewer,
        "pending_sent": pending_sent,
        "pending_received": pending_received,
        "can_send_friend_request": can_request,
        "can_remove_friend": friends,
        "can_block": not viewer_blocked_target and viewer.pk != target_user.pk,
        "can_unblock": viewer_blocked_target,
        "can_send_direct": can_dm,
    }


def _empty_friendship_context(*, is_self: bool = False) -> dict:
    return {
        "is_friend": False,
        "is_blocked": False,
        "is_blocked_by": False,
        "pending_sent": None,
        "pending_received": None,
        "can_send_friend_request": False,
        "can_remove_friend": False,
        "can_block": False,
        "can_unblock": False,
        "can_send_direct": False,
        "is_self": is_self,
    }
