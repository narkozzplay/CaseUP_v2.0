from __future__ import annotations

from django.utils import timezone

from ..models import UserProfile, UserRole, VerificationRequest


def grant_verification(ver_req: VerificationRequest) -> None:
    """Give the user what an approved request asked for.

    A farmer request also opens the Farm section: a plain user becomes a farmer,
    higher roles keep their role.
    """
    now = timezone.now()
    UserProfile.objects.filter(user=ver_req.user).update(verification_level=ver_req.requested_level, updated_at=now)
    if ver_req.requested_level == VerificationRequest.RequestedLevel.FARMER:
        UserProfile.objects.filter(user=ver_req.user, role=UserRole.USER).update(role=UserRole.FARMER, updated_at=now)
