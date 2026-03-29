from __future__ import annotations

from dataclasses import dataclass

from modules.v2.profiles.models import UserRole
from modules.v2.profiles.rbac import get_user_role


@dataclass(frozen=True)
class FarmAccess:
    is_farmer: bool
    is_farm_moderator: bool
    is_farm_admin: bool

    @property
    def has_access(self) -> bool:
        return self.is_farmer or self.is_farm_moderator or self.is_farm_admin

    @property
    def staff_access(self) -> bool:
        return self.is_farm_moderator or self.is_farm_admin


def get_farm_access(user) -> FarmAccess:
    if not getattr(user, "is_authenticated", False):
        return FarmAccess(False, False, False)

    if getattr(user, "is_superuser", False):
        return FarmAccess(True, True, True)

    if getattr(user, "is_staff", False):
        return FarmAccess(True, True, True)

    role = get_user_role(user)

    is_farmer = role.code in {
        UserRole.FARMER,
        UserRole.MOD,
        UserRole.ADMIN,
        UserRole.CHIEF_ADMIN,
        UserRole.SUPER_ADMIN,
    }
    is_mod = role.code in {
        UserRole.MOD,
        UserRole.ADMIN,
        UserRole.CHIEF_ADMIN,
        UserRole.SUPER_ADMIN,
    }
    is_admin = role.code in {
        UserRole.ADMIN,
        UserRole.CHIEF_ADMIN,
        UserRole.SUPER_ADMIN,
    }

    return FarmAccess(
        is_farmer=is_farmer,
        is_farm_moderator=is_mod,
        is_farm_admin=is_admin,
    )