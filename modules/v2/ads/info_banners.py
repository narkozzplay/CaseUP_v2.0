"""Info banners: CaseUP's own page banners (stats, market, donations). Unlike ads they ignore the ad settings."""
from __future__ import annotations

from django.db import DatabaseError
from django.db.models import Q
from django.urls import Resolver404, resolve
from django.utils import timezone

from modules.v2.system.services import is_module_enabled, resolve_module_for_namespace

from .models import InfoBanner


MAX_BANNERS = 5


def _target_enabled(url: str) -> bool:
    """A banner leading into a switched-off module would end on its 404 page, so it is hidden."""
    if not url.startswith("/"):
        return True
    try:
        match = resolve(url.split("?", 1)[0].split("#", 1)[0])
    except Resolver404:
        return True
    module = resolve_module_for_namespace(match.namespace)
    return module is None or is_module_enabled(module.key)


def get_info_banners(slot: str, user=None) -> list[InfoBanner]:
    now = timezone.now()
    is_authenticated = bool(getattr(user, "is_authenticated", False))
    hidden_audience = InfoBanner.Audience.GUESTS if is_authenticated else InfoBanner.Audience.USERS
    try:
        banners = list(
            InfoBanner.objects.filter(slot=slot, is_enabled=True)
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            .exclude(audience=hidden_audience)
            .order_by("sort_order", "id")[: MAX_BANNERS * 2]
        )
    except DatabaseError:
        return []
    return [banner for banner in banners if _target_enabled(banner.url or "")][:MAX_BANNERS]
