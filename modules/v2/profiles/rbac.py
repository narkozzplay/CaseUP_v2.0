from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied

from .models import UserProfile, UserRole


ROLE_RANK = {
    UserRole.USER: 0,
    UserRole.FARMER: 1,
    UserRole.MOD: 2,
    UserRole.ADMIN: 3,
    UserRole.CHIEF_ADMIN: 4,
    UserRole.SUPER_ADMIN: 5,
}


@dataclass(frozen=True)
class RoleAccess:
    code: str
    rank: int

    @property
    def is_moder_plus(self) -> bool:
        return self.rank >= ROLE_RANK[UserRole.MOD]

    @property
    def is_admin_plus(self) -> bool:
        return self.rank >= ROLE_RANK[UserRole.ADMIN]

    @property
    def is_chief_plus(self) -> bool:
        return self.rank >= ROLE_RANK[UserRole.CHIEF_ADMIN]

    @property
    def is_farmer_plus(self) -> bool:
        return self.rank >= ROLE_RANK[UserRole.FARMER]


def get_user_role(user) -> RoleAccess:
    if not getattr(user, "is_authenticated", False):
        return RoleAccess(code=UserRole.USER, rank=ROLE_RANK[UserRole.USER])

    # An admin previewing the site as a regular user (see view_as_user.py).
    if getattr(user, "_caseup_view_as_user", False):
        return RoleAccess(code=UserRole.USER, rank=ROLE_RANK[UserRole.USER])

    if getattr(user, "is_superuser", False):
        return RoleAccess(code=UserRole.SUPER_ADMIN, rank=ROLE_RANK[UserRole.SUPER_ADMIN])

    profile = getattr(user, "profile_v2", None)
    if profile is None:
        try:
            profile = user.profile_v2
        except UserProfile.DoesNotExist:
            profile, _ = UserProfile.objects.get_or_create(user=user)

    code = profile.role or UserRole.USER
    rank = ROLE_RANK.get(code, ROLE_RANK[UserRole.USER])
    return RoleAccess(code=code, rank=rank)


def can_manage_user(actor, target) -> bool:
    """Admin+ may manage only users ranked strictly below them, never themselves.

    A super admin may manage anyone except Django superusers, unless the actor is a real superuser too.
    """
    if not getattr(actor, "is_authenticated", False) or target is None or actor.pk == target.pk:
        return False
    actor_access = get_user_role(actor)
    if not actor_access.is_admin_plus:
        return False
    target_rank = get_user_role(target).rank
    if actor_access.code == UserRole.SUPER_ADMIN:
        return not target.is_superuser or actor.is_superuser
    return actor_access.rank > target_rank


def assignable_roles(actor) -> list[tuple[str, str]]:
    access = get_user_role(actor)
    if not access.is_admin_plus:
        return []
    if access.code == UserRole.SUPER_ADMIN:
        return list(UserRole.choices)
    return [(code, label) for code, label in UserRole.choices if ROLE_RANK[code] < access.rank]


def require_role(user, min_role: str) -> None:
    access = get_user_role(user)
    if access.rank < ROLE_RANK[min_role]:
        raise PermissionDenied("Недостаточно прав")


def is_feature_disabled(*_args, **_kwargs) -> None:
    raise PermissionDenied("Функционал временно отключён")
