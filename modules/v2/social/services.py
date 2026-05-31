from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.templatetags.static import static
from django.urls import reverse

from modules.v2.profiles.models import UserProfile
from modules.v2.profiles.presentation import get_profile_avatar_url, get_profile_display_name
from modules.v2.profiles.services.presence import is_profile_online

from .models import SocialMembership, SocialSpace, generate_unique_social_space_slug


VISIBLE_MEMBERSHIP_STATUSES = (SocialMembership.Status.ACTIVE, SocialMembership.Status.MUTED)

SPACE_TYPE_TO_ROUTE = {
    SocialSpace.SpaceType.DIRECT.value: "social:chats",
    SocialSpace.SpaceType.GROUP.value: "social:groups",
    SocialSpace.SpaceType.CONVERSATION.value: "social:conversations",
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


def _space_avatar_url(space: SocialSpace, preview_members: list[dict[str, object]]) -> str:
    avatar_url = _safe_file_url(space.avatar)
    if avatar_url:
        return avatar_url
    if preview_members:
        return str(preview_members[0].get("avatar_url") or "")
    return static("img/placeholders/no_image.svg")


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


def build_member_preview(space: SocialSpace, *, limit: int = 3) -> list[dict[str, object]]:
    memberships = (
        SocialMembership.objects.filter(space=space, status__in=VISIBLE_MEMBERSHIP_STATUSES)
        .select_related("user", "user__profile_v2")
        .order_by("joined_at", "id")[: max(0, limit)]
    )
    preview = []
    for membership in memberships:
        profile = _get_user_profile(membership.user)
        preview.append(
            {
                "user": membership.user,
                "user_id": membership.user_id,
                "display_name": get_profile_display_name(profile, fallback_username=membership.user.username),
                "avatar_url": get_profile_avatar_url(profile),
                "is_online": is_profile_online(profile),
                "role": membership.role,
                "role_label": membership.get_role_display(),
            }
        )
    return preview


def _build_space_card(space: SocialSpace, membership: SocialMembership | None) -> dict[str, object]:
    preview_members = build_member_preview(space)
    return {
        "space": space,
        "id": space.id,
        "title": space.title,
        "slug": space.slug,
        "description": space.description,
        "space_type": space.space_type,
        "space_type_label": space.get_space_type_display(),
        "privacy": space.privacy,
        "privacy_label": space.get_privacy_display(),
        "member_count": getattr(space, "member_count", None) or space.memberships.filter(status__in=VISIBLE_MEMBERSHIP_STATUSES).count(),
        "role": membership.role if membership else "",
        "role_label": membership.get_role_display() if membership else "",
        "status": membership.status if membership else "",
        "member_preview": preview_members,
        "avatar_url": _space_avatar_url(space, preview_members),
        "last_activity_at": space.last_activity_at,
        "url": _space_url(space.space_type),
    }


def _membership_map_for_user(user, spaces: list[SocialSpace]) -> dict[int, SocialMembership]:
    if not spaces:
        return {}
    memberships = SocialMembership.objects.filter(user=user, space__in=spaces).select_related("space")
    return {membership.space_id: membership for membership in memberships}


def build_space_cards(user, *, space_type: str) -> list[dict[str, object]]:
    spaces = list(list_user_spaces(user, space_type=space_type))
    memberships_by_space = _membership_map_for_user(user, spaces)
    return [_build_space_card(space, memberships_by_space.get(space.id)) for space in spaces]


def build_sidebar_context(user) -> dict[str, object]:
    counts = {
        SocialSpace.SpaceType.DIRECT.value: 0,
        SocialSpace.SpaceType.GROUP.value: 0,
        SocialSpace.SpaceType.CONVERSATION.value: 0,
    }
    spaces = list(list_user_spaces(user))
    for space in spaces:
        counts[space.space_type] = counts.get(space.space_type, 0) + 1

    memberships_by_space = _membership_map_for_user(user, spaces[:5])
    recent_spaces = [_build_space_card(space, memberships_by_space.get(space.id)) for space in spaces[:5]]
    return {
        "social_counts": counts,
        "social_recent_spaces": recent_spaces,
    }


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
