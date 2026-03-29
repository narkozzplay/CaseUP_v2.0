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


def require_role(user, min_role: str) -> None:
    access = get_user_role(user)
    if access.rank < ROLE_RANK[min_role]:
        raise PermissionDenied("Недостаточно прав")


def is_feature_disabled(*_args, **_kwargs) -> None:
    raise PermissionDenied("Функционал временно отключён")
