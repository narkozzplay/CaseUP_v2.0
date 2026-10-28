"""Info banners: CaseUP's own page banners (stats, market, donations). Unlike ads they ignore the ad settings."""
from __future__ import annotations

from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone

from .models import InfoBanner


def get_info_banners(slot: str, user=None) -> list[InfoBanner]:
    now = timezone.now()
    is_authenticated = bool(getattr(user, "is_authenticated", False))
    hidden_audience = InfoBanner.Audience.GUESTS if is_authenticated else InfoBanner.Audience.USERS
    try:
        return list(
            InfoBanner.objects.filter(slot=slot, is_enabled=True)
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            .exclude(audience=hidden_audience)
            .order_by("sort_order", "id")[:5]
        )
    except DatabaseError:
        return []
