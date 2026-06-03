from __future__ import annotations

from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.http import Http404
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile
from modules.v2.profiles.presentation import get_profile_avatar_url, get_profile_display_name, get_verification_badge
from modules.v2.profiles.services.presence import is_profile_online

from .models import SocialMembership, SocialMessage, SocialSpace, generate_unique_social_space_slug


MESSAGE_PAGE_SIZE = 50
VISIBLE_MEMBERSHIP_STATUSES = (SocialMembership.Status.ACTIVE, SocialMembership.Status.MUTED)

SPACE_TYPE_TO_ROUTE = {
    SocialSpace.SpaceType.DIRECT.value: "social:chats",
    SocialSpace.SpaceType.GROUP.value: "social:groups",
    SocialSpace.SpaceType.CONVERSATION.value: "social:conversations",
}

SPACE_TYPE_TO_ACTIVE_MENU = {
    SocialSpace.SpaceType.DIRECT.value: "social_chats",
    SocialSpace.SpaceType.GROUP.value: "social_groups",
    SocialSpace.SpaceType.CONVERSATION.value: "social_conversations",
}


def _validate_choice(value: str, valid_values: set[str], field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in valid_values:
        raise ValueError(f"Invalid {field_name}: {value!r}")
    return normalized


def _get_user_profile(user) -> UserProfile | None:
    try:
        return user.profile_v2
    except UserProfile.DoesNotExist:
        return None


def _safe_file_url(file_field) -> str:
    if not file_field:
        return ""
    try:
        return str(file_field.url or "").strip()
    except Exception:
        return ""


def _space_url(space_type: str) -> str:
    return reverse(SPACE_TYPE_TO_ROUTE.get(space_type, "social:chats"))


def build_space_url(space: SocialSpace) -> str:
    return reverse("social:space_detail", args=[space.id, space.slug])


def build_message_send_url(space: SocialSpace) -> str:
    return reverse("social:space_message_create", args=[space.id, space.slug])


def build_members_manage_url(space: SocialSpace) -> str:
    return reverse("social:space_members_manage", args=[space.id, space.slug])


def build_member_add_url(space: SocialSpace) -> str:
    return reverse("social:space_member_add", args=[space.id, space.slug])


def build_member_remove_url(space: SocialSpace, user_id: int) -> str:
    return reverse("social:space_member_remove", args=[space.id, space.slug, user_id])


def build_member_role_url(space: SocialSpace, user_id: int) -> str:
    return reverse("social:space_member_role", args=[space.id, space.slug, user_id])


def build_member_block_url(space: SocialSpace, user_id: int) -> str:
    return reverse("social:space_member_block", args=[space.id, space.slug, user_id])


def build_member_unblock_url(space: SocialSpace, user_id: int) -> str:
    return reverse("social:space_member_unblock", args=[space.id, space.slug, user_id])


def build_member_transfer_owner_url(space: SocialSpace, user_id: int) -> str:
    return reverse("social:space_member_transfer_owner", args=[space.id, space.slug, user_id])


def build_leave_space_url(space: SocialSpace) -> str:
    return reverse("social:space_leave", args=[space.id, space.slug])


def build_space_settings_url(space: SocialSpace) -> str:
    return reverse("social:space_settings", args=[space.id, space.slug])


def _space_avatar_url(space: SocialSpace, preview_members: list[dict[str, object]]) -> str:
    avatar_url = _safe_file_url(space.avatar)
    if avatar_url:
        return avatar_url
    return static("img/placeholders/no_image.svg")


def _profile_has_avatar(profile: UserProfile | None) -> bool:
    if profile is None:
        return False
    return bool(_safe_file_url(getattr(profile, "uploaded_avatar", None)) or str(getattr(profile, "steam_avatar", "") or "").strip())


def _avatar_initial(title: str, *, fallback: str = "C") -> str:
    clean_title = str(title or "").strip()
    for char in clean_title:
        if char.isalnum():
            return char.upper()
    return fallback


def get_direct_counterpart(user, space: SocialSpace):
    if not getattr(user, "pk", None) or space.space_type != SocialSpace.SpaceType.DIRECT:
        return None
    membership = (
        SocialMembership.objects.filter(
            space=space,
            status__in=VISIBLE_MEMBERSHIP_STATUSES,
        )
        .exclude(user=user)
        .select_related("user", "user__profile_v2")
        .order_by("joined_at", "id")
        .first()
    )
    return membership.user if membership else None


def build_space_presentation(user, space: SocialSpace) -> dict[str, object]:
    if space.space_type == SocialSpace.SpaceType.DIRECT:
        counterpart = get_direct_counterpart(user, space)
        if counterpart is not None:
            profile = _get_user_profile(counterpart)
            title = get_profile_display_name(profile, fallback_username=counterpart.username)
            return {
                "title": title,
                "avatar_url": get_profile_avatar_url(profile),
                "avatar_initial": _avatar_initial(title, fallback="U"),
                "has_custom_avatar": _profile_has_avatar(profile),
                "space_type": space.space_type,
                "space_type_label": space.get_space_type_display(),
                "privacy": space.privacy,
                "privacy_label": space.get_privacy_display(),
                "is_direct": True,
            }

        fallback_title = str(space.title or "").strip() or "Direct chat"
        return {
            "title": fallback_title,
            "avatar_url": static("img/placeholders/no_image.svg"),
            "avatar_initial": _avatar_initial(fallback_title, fallback="D"),
            "has_custom_avatar": False,
            "space_type": space.space_type,
            "space_type_label": space.get_space_type_display(),
            "privacy": space.privacy,
            "privacy_label": space.get_privacy_display(),
            "is_direct": True,
        }

    title = str(space.title or "").strip() or space.get_space_type_display()
    avatar_url = _safe_file_url(space.avatar)
    return {
        "title": title,
        "avatar_url": avatar_url or static("img/placeholders/no_image.svg"),
        "avatar_initial": _avatar_initial(title, fallback="S"),
        "has_custom_avatar": bool(avatar_url),
        "space_type": space.space_type,
        "space_type_label": space.get_space_type_display(),
        "privacy": space.privacy,
        "privacy_label": space.get_privacy_display(),
        "is_direct": False,
    }


def list_user_spaces(user, *, space_type: str | None = None):
    if not getattr(user, "is_authenticated", False):
        return SocialSpace.objects.none()

    user_membership = SocialMembership.objects.filter(
        space=OuterRef("pk"),
        user=user,
        status__in=VISIBLE_MEMBERSHIP_STATUSES,
    )
    qs = (
        SocialSpace.objects.filter(is_active=True)
        .annotate(current_user_is_member=Exists(user_membership))
        .filter(current_user_is_member=True)
        .select_related("owner", "owner__profile_v2")
        .annotate(
            member_count=Count(
                "memberships",
                filter=Q(memberships__status__in=VISIBLE_MEMBERSHIP_STATUSES),
                distinct=True,
            )
        )
        .order_by("-last_activity_at", "-updated_at", "-id")
    )

    if space_type is not None:
        valid_types = {choice for choice, _label in SocialSpace.SpaceType.choices}
        qs = qs.filter(space_type=_validate_choice(space_type, valid_types, "space_type"))

    return qs


def _build_member_row(membership: SocialMembership, *, viewer=None) -> dict[str, object]:
    profile = _get_user_profile(membership.user)
    display_name = get_profile_display_name(profile, fallback_username=membership.user.username)
    is_online = is_profile_online(profile)
    row = {
        "membership": membership,
        "user": membership.user,
        "user_id": membership.user_id,
        "display_name": display_name,
        "avatar_url": get_profile_avatar_url(profile),
        "is_online": is_online,
        "online_label": "Online" if is_online else "Offline",
        "role": membership.role,
        "role_label": membership.get_role_display(),
        "status": membership.status,
        "status_label": membership.get_status_display(),
        "profile_url": reverse("profiles:profile_detail", args=[membership.user_id]),
        "message_url": reverse("profiles:profile_start_direct_chat", args=[membership.user_id]),
        "is_current_user": bool(getattr(viewer, "pk", None) == membership.user_id),
        "verification_badge": get_verification_badge(profile),
        "actions": {},
    }
    if viewer is not None:
        row["actions"] = build_member_action_flags(viewer, membership)
    return row


def _build_member_rows(space: SocialSpace, *, limit: int | None = None, viewer=None) -> list[dict[str, object]]:
    memberships = (
        SocialMembership.objects.filter(space=space, status__in=VISIBLE_MEMBERSHIP_STATUSES)
        .select_related("user", "user__profile_v2")
        .order_by("joined_at", "id")
    )
    if limit is not None:
        memberships = memberships[: max(0, limit)]

    preview = []
    for membership in memberships:
        preview.append(_build_member_row(membership, viewer=viewer))
    return preview


def build_member_preview(space: SocialSpace, *, limit: int = 3, viewer=None) -> list[dict[str, object]]:
    return _build_member_rows(space, limit=limit, viewer=viewer)


def _build_space_card(viewer, space: SocialSpace, membership: SocialMembership | None) -> dict[str, object]:
    presentation = build_space_presentation(viewer, space)
    preview_members = build_member_preview(space, viewer=viewer)
    member_count = getattr(space, "member_count", None)
    if member_count is None:
        member_count = space.memberships.filter(status__in=VISIBLE_MEMBERSHIP_STATUSES).count()
    can_manage_members = can_manage_space(viewer, space)
    space_url = build_space_url(space)
    members_manage_url = build_members_manage_url(space)
    return {
        "space": space,
        "id": space.id,
        "title": space.title,
        "display_title": presentation["title"],
        "slug": space.slug,
        "description": space.description,
        "space_type": space.space_type,
        "space_type_label": space.get_space_type_display(),
        "privacy": space.privacy,
        "privacy_label": space.get_privacy_display(),
        "member_count": member_count,
        "role": membership.role if membership else "",
        "role_label": membership.get_role_display() if membership else "",
        "status": membership.status if membership else "",
        "member_preview": preview_members,
        "avatar_url": presentation["avatar_url"],
        "display_avatar_url": presentation["avatar_url"],
        "avatar_initial": presentation["avatar_initial"],
        "has_custom_avatar": presentation["has_custom_avatar"],
        "presentation": presentation,
        "last_activity_at": space.last_activity_at,
        "url": space_url,
        "members_manage_url": members_manage_url,
        "members_url": members_manage_url if can_manage_members else f"{space_url}#social-members",
        "can_manage_members": can_manage_members,
        "unread_count": 0,
    }


def _membership_map_for_user(user, spaces: list[SocialSpace]) -> dict[int, SocialMembership]:
    if not spaces:
        return {}
    memberships = SocialMembership.objects.filter(user=user, space__in=spaces).select_related("space")
    return {membership.space_id: membership for membership in memberships}


def build_space_cards(user, *, space_type: str) -> list[dict[str, object]]:
    spaces = list(list_user_spaces(user, space_type=space_type))
    memberships_by_space = _membership_map_for_user(user, spaces)
    return [_build_space_card(user, space, memberships_by_space.get(space.id)) for space in spaces]


def build_sidebar_context(user) -> dict[str, object]:
    counts = {
        SocialSpace.SpaceType.DIRECT.value: 0,
        SocialSpace.SpaceType.GROUP.value: 0,
        SocialSpace.SpaceType.CONVERSATION.value: 0,
    }
    if not getattr(user, "is_authenticated", False):
        return {
            "social_counts": counts,
            "social_recent_spaces": [],
            "social_unread_total": 0,
            "social_unread_counts": {
                "by_type": dict(counts),
                "by_space_id": {},
            },
        }

    count_rows = (
        SocialMembership.objects.filter(
            user=user,
            status__in=VISIBLE_MEMBERSHIP_STATUSES,
            space__is_active=True,
        )
        .values("space__space_type")
        .annotate(total=Count("space_id", distinct=True))
    )
    for row in count_rows:
        counts[row["space__space_type"]] = row["total"]

    recent_spaces_qs = list_user_spaces(user)[:5]
    recent_spaces = list(recent_spaces_qs)
    memberships_by_space = _membership_map_for_user(user, recent_spaces)
    unread_counts = count_unread_messages_for_user(user)
    unread_by_space_id = unread_counts["by_space_id"]
    recent_cards = [_build_space_card(user, space, memberships_by_space.get(space.id)) for space in recent_spaces]
    for card in recent_cards:
        card["unread_count"] = unread_by_space_id.get(card["id"], 0)
    return {
        "social_counts": counts,
        "social_recent_spaces": recent_cards,
        "social_unread_total": unread_counts["total"],
        "social_unread_counts": unread_counts,
    }


def get_user_space_or_404(user, *, space_id: int, slug: str | None = None) -> SocialSpace:
    if not getattr(user, "is_authenticated", False):
        raise Http404("Social space not found.")

    user_membership = SocialMembership.objects.filter(
        space=OuterRef("pk"),
        user=user,
        status__in=VISIBLE_MEMBERSHIP_STATUSES,
    )
    try:
        return (
            SocialSpace.objects.filter(pk=space_id, is_active=True)
            .annotate(current_user_is_member=Exists(user_membership))
            .filter(current_user_is_member=True)
            .select_related("owner", "owner__profile_v2")
            .annotate(
                member_count=Count(
                    "memberships",
                    filter=Q(memberships__status__in=VISIBLE_MEMBERSHIP_STATUSES),
                    distinct=True,
                )
            )
            .get()
        )
    except SocialSpace.DoesNotExist as exc:
        raise Http404("Social space not found.") from exc


def get_user_membership(user, space: SocialSpace) -> SocialMembership | None:
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return SocialMembership.objects.select_related("space", "user").get(user=user, space=space)
    except SocialMembership.DoesNotExist:
        return None


def get_active_user_membership(user, space: SocialSpace) -> SocialMembership | None:
    membership = get_user_membership(user, space)
    if membership and membership.status in VISIBLE_MEMBERSHIP_STATUSES:
        return membership
    return None


def _is_group_manage_space(space: SocialSpace) -> bool:
    return bool(space and space.space_type != SocialSpace.SpaceType.DIRECT)


def _actor_membership(actor, space: SocialSpace) -> SocialMembership | None:
    return get_active_user_membership(actor, space)


def _is_owner(membership: SocialMembership | None) -> bool:
    return bool(membership and membership.role == SocialMembership.Role.OWNER)


def _is_admin(membership: SocialMembership | None) -> bool:
    return bool(membership and membership.role == SocialMembership.Role.ADMIN)


def can_manage_space(user, space: SocialSpace) -> bool:
    if not _is_group_manage_space(space):
        return False
    membership = _actor_membership(user, space)
    return _is_owner(membership) or _is_admin(membership)


def can_update_space_settings(user, space: SocialSpace) -> bool:
    if not _is_group_manage_space(space):
        return False
    membership = _actor_membership(user, space)
    return _is_owner(membership) or _is_admin(membership)


def can_add_member(user, space: SocialSpace) -> bool:
    return can_manage_space(user, space)


def can_remove_member(actor, target_membership: SocialMembership) -> bool:
    if not target_membership or not _is_group_manage_space(target_membership.space):
        return False
    actor_membership = _actor_membership(actor, target_membership.space)
    if not actor_membership or actor_membership.user_id == target_membership.user_id:
        return False
    if target_membership.role == SocialMembership.Role.OWNER:
        return False
    if target_membership.status == SocialMembership.Status.LEFT:
        return False
    if _is_owner(actor_membership):
        return target_membership.role in {SocialMembership.Role.ADMIN, SocialMembership.Role.MEMBER}
    if _is_admin(actor_membership):
        return target_membership.role == SocialMembership.Role.MEMBER
    return False


def can_change_member_role(actor, target_membership: SocialMembership, new_role: str) -> bool:
    if not target_membership or not _is_group_manage_space(target_membership.space):
        return False
    if new_role not in {SocialMembership.Role.ADMIN, SocialMembership.Role.MEMBER}:
        return False
    actor_membership = _actor_membership(actor, target_membership.space)
    if not _is_owner(actor_membership):
        return False
    if actor_membership.user_id == target_membership.user_id:
        return False
    if target_membership.role == SocialMembership.Role.OWNER:
        return False
    if target_membership.role == new_role:
        return False
    return target_membership.status in VISIBLE_MEMBERSHIP_STATUSES


def can_block_member(actor, target_membership: SocialMembership) -> bool:
    if not target_membership or not _is_group_manage_space(target_membership.space):
        return False
    actor_membership = _actor_membership(actor, target_membership.space)
    if not actor_membership or actor_membership.user_id == target_membership.user_id:
        return False
    if target_membership.role == SocialMembership.Role.OWNER:
        return False
    if target_membership.status not in VISIBLE_MEMBERSHIP_STATUSES:
        return False
    if _is_owner(actor_membership):
        return target_membership.role in {SocialMembership.Role.ADMIN, SocialMembership.Role.MEMBER}
    if _is_admin(actor_membership):
        return target_membership.role == SocialMembership.Role.MEMBER
    return False


def can_unblock_member(actor, target_membership: SocialMembership) -> bool:
    if not target_membership or not _is_group_manage_space(target_membership.space):
        return False
    actor_membership = _actor_membership(actor, target_membership.space)
    if not actor_membership or actor_membership.user_id == target_membership.user_id:
        return False
    if target_membership.status != SocialMembership.Status.BLOCKED:
        return False
    if target_membership.role == SocialMembership.Role.OWNER:
        return False
    if _is_owner(actor_membership):
        return target_membership.role in {SocialMembership.Role.ADMIN, SocialMembership.Role.MEMBER}
    if _is_admin(actor_membership):
        return target_membership.role == SocialMembership.Role.MEMBER
    return False


def can_transfer_ownership(actor, target_membership: SocialMembership) -> bool:
    if not target_membership or not _is_group_manage_space(target_membership.space):
        return False
    actor_membership = _actor_membership(actor, target_membership.space)
    if not _is_owner(actor_membership):
        return False
    if actor_membership.user_id == target_membership.user_id:
        return False
    if target_membership.role not in {SocialMembership.Role.ADMIN, SocialMembership.Role.MEMBER}:
        return False
    return target_membership.status in VISIBLE_MEMBERSHIP_STATUSES


def can_leave_space(user, space: SocialSpace) -> bool:
    if not _is_group_manage_space(space):
        return False
    membership = _actor_membership(user, space)
    if not membership:
        return False
    return membership.role in {SocialMembership.Role.ADMIN, SocialMembership.Role.MEMBER}


def build_member_action_flags(actor, target_membership: SocialMembership) -> dict[str, object]:
    space = target_membership.space
    user_id = target_membership.user_id
    can_make_admin = can_change_member_role(actor, target_membership, SocialMembership.Role.ADMIN)
    can_make_member = can_change_member_role(actor, target_membership, SocialMembership.Role.MEMBER)
    return {
        "can_message": bool(getattr(actor, "pk", None) and actor.pk != user_id),
        "can_remove": can_remove_member(actor, target_membership),
        "can_make_admin": can_make_admin,
        "can_make_member": can_make_member,
        "can_block": can_block_member(actor, target_membership),
        "can_unblock": can_unblock_member(actor, target_membership),
        "can_transfer_owner": can_transfer_ownership(actor, target_membership),
        "remove_url": build_member_remove_url(space, user_id),
        "role_url": build_member_role_url(space, user_id),
        "block_url": build_member_block_url(space, user_id),
        "unblock_url": build_member_unblock_url(space, user_id),
        "transfer_owner_url": build_member_transfer_owner_url(space, user_id),
    }


def _require_group_manage_space(space: SocialSpace):
    if not _is_group_manage_space(space):
        raise PermissionError("Direct chats do not support member management.")


def _locked_membership(space: SocialSpace, user) -> SocialMembership | None:
    if not getattr(user, "pk", None):
        return None
    try:
        return (
            SocialMembership.objects.select_for_update()
            .select_related("space", "user")
            .get(space=space, user=user)
        )
    except SocialMembership.DoesNotExist:
        return None


def add_space_member(actor, space: SocialSpace, user) -> SocialMembership:
    _require_group_manage_space(space)
    if not can_add_member(actor, space):
        raise PermissionError("You cannot add members to this space.")
    if not getattr(user, "pk", None) or not getattr(user, "is_active", True):
        raise ValueError("Target user must be active.")
    if user.pk == getattr(actor, "pk", None):
        raise ValueError("Cannot add yourself.")

    with transaction.atomic():
        membership = _locked_membership(space, user)
        if membership is None:
            return SocialMembership.objects.create(
                space=space,
                user=user,
                role=SocialMembership.Role.MEMBER,
                status=SocialMembership.Status.ACTIVE,
            )
        if membership.status in VISIBLE_MEMBERSHIP_STATUSES:
            return membership
        if membership.status == SocialMembership.Status.LEFT:
            membership.role = SocialMembership.Role.MEMBER
            membership.status = SocialMembership.Status.ACTIVE
            membership.joined_at = timezone.now()
            membership.save(update_fields=["role", "status", "joined_at"])
            return membership
        if membership.status == SocialMembership.Status.BLOCKED:
            raise PermissionError("Blocked members must be unblocked explicitly.")
        raise PermissionError("Cannot add this member.")


def remove_space_member(actor, space: SocialSpace, target_user) -> SocialMembership:
    _require_group_manage_space(space)
    with transaction.atomic():
        membership = _locked_membership(space, target_user)
        if membership is None or not can_remove_member(actor, membership):
            raise PermissionError("You cannot remove this member.")
        membership.status = SocialMembership.Status.LEFT
        membership.save(update_fields=["status"])
        return membership


def change_member_role(actor, space: SocialSpace, target_user, new_role: str) -> SocialMembership:
    _require_group_manage_space(space)
    valid_roles = {SocialMembership.Role.ADMIN, SocialMembership.Role.MEMBER}
    role = _validate_choice(new_role, valid_roles, "role")
    with transaction.atomic():
        membership = _locked_membership(space, target_user)
        if membership is None or not can_change_member_role(actor, membership, role):
            raise PermissionError("You cannot change this member role.")
        membership.role = role
        membership.save(update_fields=["role"])
        return membership


def block_space_member(actor, space: SocialSpace, target_user) -> SocialMembership:
    _require_group_manage_space(space)
    with transaction.atomic():
        membership = _locked_membership(space, target_user)
        if membership is None or not can_block_member(actor, membership):
            raise PermissionError("You cannot block this member.")
        membership.status = SocialMembership.Status.BLOCKED
        membership.save(update_fields=["status"])
        return membership


def unblock_space_member(actor, space: SocialSpace, target_user) -> SocialMembership:
    _require_group_manage_space(space)
    with transaction.atomic():
        membership = _locked_membership(space, target_user)
        if membership is None or not can_unblock_member(actor, membership):
            raise PermissionError("You cannot unblock this member.")
        membership.status = SocialMembership.Status.ACTIVE
        membership.save(update_fields=["status"])
        return membership


def leave_space(user, space: SocialSpace) -> SocialMembership:
    _require_group_manage_space(space)
    with transaction.atomic():
        membership = _locked_membership(space, user)
        if membership is None or not can_leave_space(user, space):
            raise PermissionError("You cannot leave this space before transferring ownership.")
        membership.status = SocialMembership.Status.LEFT
        membership.save(update_fields=["status"])
        return membership


def transfer_space_ownership(actor, space: SocialSpace, target_user) -> SocialMembership:
    _require_group_manage_space(space)
    with transaction.atomic():
        actor_membership = _locked_membership(space, actor)
        target_membership = _locked_membership(space, target_user)
        if actor_membership is None or target_membership is None:
            raise PermissionError("Ownership transfer is not available.")
        if not can_transfer_ownership(actor, target_membership):
            raise PermissionError("You cannot transfer ownership to this member.")

        actor_membership.role = SocialMembership.Role.ADMIN
        actor_membership.status = SocialMembership.Status.ACTIVE
        target_membership.role = SocialMembership.Role.OWNER
        target_membership.status = SocialMembership.Status.ACTIVE
        space.owner = target_user

        actor_membership.save(update_fields=["role", "status"])
        target_membership.save(update_fields=["role", "status"])
        space.save(update_fields=["owner", "updated_at"])
        return target_membership


def update_social_space_settings(actor, space: SocialSpace, *, title: str, description: str, privacy: str, avatar=None) -> SocialSpace:
    _require_group_manage_space(space)
    valid_privacy = {choice for choice, _label in SocialSpace.Privacy.choices}

    with transaction.atomic():
        locked_space = SocialSpace.objects.select_for_update().get(pk=space.pk)
        actor_membership = _locked_membership(locked_space, actor)
        if not actor_membership or not (_is_owner(actor_membership) or _is_admin(actor_membership)):
            raise PermissionError("You cannot update this social space settings.")

        update_fields = ["description", "updated_at"]
        locked_space.description = str(description or "").strip()

        if _is_owner(actor_membership):
            clean_title = str(title or "").strip()
            if not clean_title:
                raise ValueError("Social space title is required.")
            clean_privacy = _validate_choice(privacy, valid_privacy, "privacy")
            if clean_title != locked_space.title:
                locked_space.title = clean_title
                locked_space.slug = generate_unique_social_space_slug(
                    title=clean_title,
                    space_type=locked_space.space_type,
                    exclude_space_id=locked_space.id,
                )
                update_fields.extend(["title", "slug"])
            if clean_privacy != locked_space.privacy:
                locked_space.privacy = clean_privacy
                update_fields.append("privacy")

        if avatar is not None:
            locked_space.avatar = avatar
            update_fields.append("avatar")

        locked_space.save(update_fields=sorted(set(update_fields)))
        space.title = locked_space.title
        space.slug = locked_space.slug
        space.description = locked_space.description
        space.privacy = locked_space.privacy
        space.avatar = locked_space.avatar
        return locked_space


def build_space_members_manage_context(user, space: SocialSpace) -> dict[str, object]:
    _require_group_manage_space(space)
    current_membership = get_active_user_membership(user, space)
    if current_membership is None:
        raise PermissionError("Social space access denied.")

    presentation = build_space_presentation(user, space)
    memberships = (
        SocialMembership.objects.filter(space=space)
        .select_related("space", "user", "user__profile_v2")
        .order_by("status", "role", "joined_at", "id")
    )
    member_rows = [_build_member_row(membership, viewer=user) for membership in memberships]
    return {
        "space": space,
        "space_presentation": presentation,
        "space_url": build_space_url(space),
        "members_manage_url": build_members_manage_url(space),
        "settings_url": build_space_settings_url(space),
        "member_add_url": build_member_add_url(space),
        "space_active_menu_key": SPACE_TYPE_TO_ACTIVE_MENU.get(space.space_type, "social_groups"),
        "space_type_label": presentation["space_type_label"],
        "privacy_label": presentation["privacy_label"],
        "current_membership": current_membership,
        "current_membership_role_label": current_membership.get_role_display(),
        "members": member_rows,
        "member_count": len(member_rows),
        "can_add_member": can_add_member(user, space),
        "can_leave_space": can_leave_space(user, space),
        "can_manage_space": can_manage_space(user, space),
        "can_update_space_settings": can_update_space_settings(user, space),
        "leave_space_url": build_leave_space_url(space),
    }


def build_space_settings_context(user, space: SocialSpace) -> dict[str, object]:
    _require_group_manage_space(space)
    membership = get_active_user_membership(user, space)
    if membership is None or not can_update_space_settings(user, space):
        raise PermissionError("Social space settings access denied.")
    presentation = build_space_presentation(user, space)
    return {
        "space": space,
        "space_presentation": presentation,
        "space_url": build_space_url(space),
        "members_manage_url": build_members_manage_url(space),
        "settings_url": build_space_settings_url(space),
        "space_active_menu_key": SPACE_TYPE_TO_ACTIVE_MENU.get(space.space_type, "social_groups"),
        "space_type_label": presentation["space_type_label"],
        "privacy_label": presentation["privacy_label"],
        "membership": membership,
        "membership_role_label": membership.get_role_display(),
        "member_count": space.memberships.filter(status__in=VISIBLE_MEMBERSHIP_STATUSES).count(),
        "can_manage_space": can_manage_space(user, space),
        "can_update_space_settings": True,
        "is_direct_space": False,
    }


def build_space_detail_context(user, space: SocialSpace) -> dict[str, object]:
    membership = get_active_user_membership(user, space)
    if membership is None:
        raise PermissionError("Social space access denied.")
    member_rows = _build_member_rows(space, viewer=user)
    member_count = getattr(space, "member_count", None)
    if member_count is None:
        member_count = len(member_rows)
    unread_counts = count_unread_messages_for_user(user)
    presentation = build_space_presentation(user, space)
    return {
        "space": space,
        "space_presentation": presentation,
        "space_url": build_space_url(space),
        "message_send_url": build_message_send_url(space),
        "space_active_menu_key": SPACE_TYPE_TO_ACTIVE_MENU.get(space.space_type, "social_chats"),
        "space_type_label": presentation["space_type_label"],
        "privacy_label": presentation["privacy_label"],
        "membership": membership,
        "membership_role_label": membership.get_role_display(),
        "member_count": member_count,
        "members": member_rows,
        "social_messages": list_space_messages(user, space),
        "unread_count": unread_counts["by_space_id"].get(space.id, 0),
        "avatar_url": presentation["avatar_url"],
        "avatar_initial": presentation["avatar_initial"],
        "has_custom_avatar": presentation["has_custom_avatar"],
        "members_manage_url": build_members_manage_url(space),
        "settings_url": build_space_settings_url(space),
        "leave_space_url": build_leave_space_url(space),
        "can_manage_space": can_manage_space(user, space),
        "can_update_space_settings": can_update_space_settings(user, space),
        "can_leave_space": can_leave_space(user, space),
        "is_direct_space": space.space_type == SocialSpace.SpaceType.DIRECT,
    }


def _require_visible_membership(user, space: SocialSpace) -> SocialMembership:
    if not getattr(user, "is_authenticated", False):
        raise PermissionError("Social space access denied.")
    try:
        return SocialMembership.objects.get(
            user=user,
            space=space,
            status__in=VISIBLE_MEMBERSHIP_STATUSES,
        )
    except SocialMembership.DoesNotExist as exc:
        raise PermissionError("Social space access denied.") from exc


def _build_message_row(user, message: SocialMessage) -> dict[str, object]:
    profile = _get_user_profile(message.author)
    return {
        "id": message.id,
        "body": message.body,
        "created_at": message.created_at,
        "author_id": message.author_id,
        "author_display_name": get_profile_display_name(profile, fallback_username=message.author.username),
        "author_avatar_url": get_profile_avatar_url(profile),
        "author_is_online": is_profile_online(profile),
        "author_verification_badge": get_verification_badge(profile),
        "is_own": message.author_id == user.pk,
        "message_type": message.message_type,
    }


def list_space_messages(user, space: SocialSpace, *, limit: int = MESSAGE_PAGE_SIZE) -> list[dict[str, object]]:
    _require_visible_membership(user, space)
    safe_limit = max(0, int(limit or 0))
    if safe_limit == 0:
        return []

    messages = list(
        SocialMessage.objects.filter(space=space, is_deleted=False)
        .select_related("author", "author__profile_v2")
        .order_by("-created_at", "-id")[:safe_limit]
    )
    return [_build_message_row(user, message) for message in reversed(messages)]


def create_space_message(user, space: SocialSpace, body: str) -> SocialMessage:
    membership = _require_visible_membership(user, space)
    clean_body = str(body or "").strip()
    if not clean_body:
        raise ValueError("Message body is required.")
    if len(clean_body) > 4000:
        raise ValueError("Message body must be 4000 characters or fewer.")

    with transaction.atomic():
        message = SocialMessage.objects.create(
            space=space,
            author=user,
            body=clean_body,
            message_type=SocialMessage.MessageType.TEXT,
        )
        timestamp = message.created_at or timezone.now()
        SocialSpace.objects.filter(pk=space.pk).update(last_activity_at=timestamp)
        SocialMembership.objects.filter(pk=membership.pk).update(last_read_at=timestamp)
        space.last_activity_at = timestamp
        membership.last_read_at = timestamp
    return message


def mark_space_read(user, space: SocialSpace):
    if not getattr(user, "is_authenticated", False):
        return None
    now = timezone.now()
    updated = SocialMembership.objects.filter(
        user=user,
        space=space,
        status__in=VISIBLE_MEMBERSHIP_STATUSES,
    ).update(last_read_at=now)
    if updated:
        return now
    return None


def count_unread_messages_for_user(user) -> dict[str, object]:
    by_type = {
        SocialSpace.SpaceType.DIRECT.value: 0,
        SocialSpace.SpaceType.GROUP.value: 0,
        SocialSpace.SpaceType.CONVERSATION.value: 0,
    }
    result = {
        "total": 0,
        "by_type": by_type,
        "by_space_id": {},
    }
    if not getattr(user, "is_authenticated", False):
        return result

    unread_rows = (
        SocialMessage.objects.filter(
            space__memberships__user=user,
            space__memberships__status__in=VISIBLE_MEMBERSHIP_STATUSES,
            space__is_active=True,
            is_deleted=False,
        )
        .exclude(author=user)
        .filter(
            Q(
                space__memberships__last_read_at__isnull=False,
                created_at__gt=F("space__memberships__last_read_at"),
            )
            | Q(
                space__memberships__last_read_at__isnull=True,
                created_at__gt=F("space__memberships__joined_at"),
            )
        )
        .values("space_id", "space__space_type")
        .annotate(total=Count("id"))
    )
    for row in unread_rows:
        count = row["total"]
        space_type = row["space__space_type"]
        space_id = row["space_id"]
        by_type[space_type] = by_type.get(space_type, 0) + count
        result["by_space_id"][space_id] = count
        result["total"] += count
    return result


def create_social_space(
    owner,
    title: str,
    space_type: str,
    privacy: str = SocialSpace.Privacy.PRIVATE,
    description: str = "",
    members=None,
) -> SocialSpace:
    if not getattr(owner, "is_authenticated", False) or not getattr(owner, "pk", None):
        raise ValueError("A saved authenticated owner is required.")

    clean_title = str(title or "").strip()
    if not clean_title:
        raise ValueError("Social space title is required.")

    valid_types = {choice for choice, _label in SocialSpace.SpaceType.choices}
    valid_privacy = {choice for choice, _label in SocialSpace.Privacy.choices}
    clean_space_type = _validate_choice(space_type, valid_types, "space_type")
    clean_privacy = _validate_choice(privacy, valid_privacy, "privacy")

    normalized_members = []
    seen_user_ids = {owner.pk}
    for member in members or []:
        if not getattr(member, "pk", None):
            raise ValueError("All social space members must be saved users.")
        if member.pk in seen_user_ids:
            continue
        seen_user_ids.add(member.pk)
        normalized_members.append(member)

    with transaction.atomic():
        space = SocialSpace.objects.create(
            owner=owner,
            title=clean_title,
            slug=generate_unique_social_space_slug(title=clean_title, space_type=clean_space_type),
            space_type=clean_space_type,
            privacy=clean_privacy,
            description=str(description or "").strip(),
        )
        SocialMembership.objects.create(
            space=space,
            user=owner,
            role=SocialMembership.Role.OWNER,
            status=SocialMembership.Status.ACTIVE,
        )
        for member in normalized_members:
            SocialMembership.objects.create(
                space=space,
                user=member,
                role=SocialMembership.Role.MEMBER,
                status=SocialMembership.Status.ACTIVE,
            )
    return space


def _direct_pair_slug(user_a, user_b) -> str:
    first_id, second_id = sorted((int(user_a.pk), int(user_b.pk)))
    return f"direct-{first_id}-{second_id}"


def _find_existing_direct_space(user_a, user_b) -> SocialSpace | None:
    first_membership_spaces = SocialMembership.objects.filter(
        user=user_a,
        status__in=VISIBLE_MEMBERSHIP_STATUSES,
        space__space_type=SocialSpace.SpaceType.DIRECT,
        space__is_active=True,
    ).values("space_id")
    return (
        SocialSpace.objects.filter(
            pk__in=first_membership_spaces,
            memberships__user=user_b,
            memberships__status__in=VISIBLE_MEMBERSHIP_STATUSES,
            is_active=True,
            space_type=SocialSpace.SpaceType.DIRECT,
        )
        .annotate(
            visible_members_count=Count(
                "memberships",
                filter=Q(memberships__status__in=VISIBLE_MEMBERSHIP_STATUSES),
                distinct=True,
            )
        )
        .filter(visible_members_count=2)
        .order_by("-last_activity_at", "-id")
        .first()
    )


def get_or_create_direct_space(user_a, user_b) -> SocialSpace:
    if not getattr(user_a, "pk", None) or not getattr(user_b, "pk", None):
        raise ValueError("Both users must be saved.")
    if user_a.pk == user_b.pk:
        raise ValueError("Cannot create a direct chat with yourself.")

    with transaction.atomic():
        existing = _find_existing_direct_space(user_a, user_b)
        if existing:
            return existing

        from modules.v2.social_friends.services import can_send_direct
        allowed, reason = can_send_direct(user_a, user_b)
        if not allowed:
            _DM_REASON_MESSAGES = {
                "blocked_target": "Вы заблокировали этого пользователя.",
                "blocked_by_target": "Этот пользователь вас заблокировал.",
                "privacy_friends_only": "Этот пользователь принимает сообщения только от друзей.",
                "privacy_nobody": "Этот пользователь не принимает личные сообщения.",
            }
            raise PermissionError(_DM_REASON_MESSAGES.get(reason, "Нельзя начать чат с этим пользователем."))

        ordered_users = sorted((user_a, user_b), key=lambda user: int(user.pk))
        title = f"Direct: {ordered_users[0].username} / {ordered_users[1].username}"
        slug = _direct_pair_slug(user_a, user_b)
        try:
            with transaction.atomic():
                space = SocialSpace.objects.create(
                    owner=user_a,
                    title=title,
                    slug=slug,
                    space_type=SocialSpace.SpaceType.DIRECT,
                    privacy=SocialSpace.Privacy.PRIVATE,
                    description="",
                )
                SocialMembership.objects.create(
                    space=space,
                    user=user_a,
                    role=SocialMembership.Role.OWNER,
                    status=SocialMembership.Status.ACTIVE,
                )
                SocialMembership.objects.create(
                    space=space,
                    user=user_b,
                    role=SocialMembership.Role.MEMBER,
                    status=SocialMembership.Status.ACTIVE,
                )
            return space
        except IntegrityError:
            existing = SocialSpace.objects.filter(
                space_type=SocialSpace.SpaceType.DIRECT,
                slug=slug,
                is_active=True,
            ).first()
            if existing:
                return existing
            raise
