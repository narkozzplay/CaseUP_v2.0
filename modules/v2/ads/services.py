"""Who sees ads and what to render. Paid plans carry the no_ads feature; the check never hides ads with CSS.

Own banners (HouseBanner) fill a placement whenever the ad network has nothing to show there: the house
provider, a network without the visitor's cookie consent or a placement without a block ID. Subscribers
without ads see only the banners marked show_to_ad_free, labelled as CaseUP's own and not as an ad.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from modules.v2.system.request_cache import request_memoize

from .models import AdPlacement, AdSettings, HouseBanner

# Set by the consent bar; third-party ad networks load only after the visitor agrees to their cookies.
CONSENT_COOKIE = "caseup_ad_consent"
NETWORK_PROVIDERS = (AdSettings.Provider.YANDEX, AdSettings.Provider.GOOGLE)

AUDIENCE_ADS = "ads"
AUDIENCE_AD_FREE = "ad_free"
# Banner ids already shown while rendering one page, so a repeated placement (the catalog feed) rotates.
_SHOWN_ATTR = "_caseup_house_banners_shown"


@dataclass(frozen=True)
class AdSlot:
    key: str
    provider: str
    placement: AdPlacement
    block_id: str
    custom_html: str
    adsense_client_id: str
    show_remove_link: bool
    banner: HouseBanner | None = None
    is_ad: bool = True


@request_memoize
def get_ad_settings() -> AdSettings:
    settings_obj = AdSettings.objects.filter(singleton_key=1).first()
    if settings_obj is None:
        settings_obj, _created = AdSettings.objects.get_or_create(singleton_key=1)
    return settings_obj


@request_memoize
def _enabled_placements() -> dict[str, AdPlacement]:
    return {placement.key: placement for placement in AdPlacement.objects.filter(is_enabled=True)}


def _is_staff(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    from modules.v2.profiles.rbac import get_user_role

    return get_user_role(user).is_admin_plus


def _has_paid_no_ads(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    from modules.v2.subscriptions.feature_codes import NO_ADS
    from modules.v2.subscriptions.services import FEATURE_ACCESS_OK, get_feature_access_state

    state = get_feature_access_state(user, NO_ADS)
    # Only a real plan turns ads off; the staff bypass is handled by show_to_staff.
    return state.is_available and state.reason == FEATURE_ACCESS_OK


def get_ad_audience(user) -> str | None:
    """AUDIENCE_ADS — sees ads, AUDIENCE_AD_FREE — a plan turned them off, None — nothing at all."""
    ad_settings = get_ad_settings()
    if not ad_settings.is_enabled:
        return None
    if _is_staff(user):
        return AUDIENCE_ADS if ad_settings.show_to_staff else None
    return AUDIENCE_AD_FREE if _has_paid_no_ads(user) else AUDIENCE_ADS


def user_sees_ads(user) -> bool:
    return get_ad_audience(user) == AUDIENCE_ADS


def uses_ad_network() -> bool:
    return get_ad_settings().provider in NETWORK_PROVIDERS


def has_ad_consent(request) -> bool:
    return getattr(request, "COOKIES", {}).get(CONSENT_COOKIE) == "1"


LIVE_BANNERS_CACHE_KEY = "ads:house_banners:v1"
LIVE_BANNERS_CACHE_TTL = 300


def drop_banner_cache(*_args, **_kwargs) -> None:
    try:
        cache.delete(LIVE_BANNERS_CACHE_KEY)
    except Exception:  # noqa: BLE001 - a cache outage must not break saving a banner
        pass


@request_memoize
def _live_banners() -> tuple[tuple[HouseBanner, frozenset[int]], ...]:
    # Enabled banners are cached between requests (saves drop the cache), so a page with ads costs no extra
    # queries besides the impression counter; the date window is checked on every call.
    try:
        enabled = cache.get(LIVE_BANNERS_CACHE_KEY)
    except Exception:  # noqa: BLE001 - without the cache banners come straight from the database
        enabled = None
    if enabled is None:
        enabled = tuple(
            (banner, frozenset(p.pk for p in banner.placements.all()))
            for banner in HouseBanner.objects.filter(is_enabled=True).prefetch_related("placements")
        )
        try:
            cache.set(LIVE_BANNERS_CACHE_KEY, enabled, LIVE_BANNERS_CACHE_TTL)
        except Exception:  # noqa: BLE001
            pass
    now = timezone.now()
    return tuple(
        (banner, ids)
        for banner, ids in enabled
        if (banner.starts_at is None or banner.starts_at <= now) and (banner.ends_at is None or banner.ends_at > now)
    )


def _banner_fits(banner: HouseBanner, placement_ids: frozenset[int], placement: AdPlacement, user, audience: str) -> bool:
    if placement_ids and placement.pk not in placement_ids:
        return False
    if audience == AUDIENCE_AD_FREE and not banner.show_to_ad_free:
        return False
    is_authenticated = bool(getattr(user, "is_authenticated", False))
    if banner.audience == HouseBanner.Audience.GUESTS and is_authenticated:
        return False
    if banner.audience == HouseBanner.Audience.USERS and not is_authenticated:
        return False
    return True


def _pick_banner(placement: AdPlacement, user, audience: str, *, request=None, peek: bool = False) -> HouseBanner | None:
    candidates = [banner for banner, ids in _live_banners() if _banner_fits(banner, ids, placement, user, audience)]
    if not candidates:
        return None
    if peek:
        return candidates[0]
    shown = getattr(request, _SHOWN_ATTR, None) if request is not None else None
    if shown is None:
        shown = set()
        if request is not None:
            setattr(request, _SHOWN_ATTR, shown)
    fresh = [banner for banner in candidates if banner.pk not in shown] or candidates
    banner = random.choices(fresh, weights=[max(banner.weight, 1) for banner in fresh])[0]
    shown.add(banner.pk)
    return banner


def _house_slot(placement: AdPlacement, banner: HouseBanner, ad_settings: AdSettings, *, is_ad: bool) -> AdSlot:
    return AdSlot(
        key=placement.key,
        provider=AdSettings.Provider.HOUSE,
        placement=placement,
        block_id="",
        custom_html="",
        adsense_client_id="",
        show_remove_link=is_ad and ad_settings.show_remove_link,
        banner=banner,
        is_ad=is_ad,
    )


def _resolve_slot(user, key: str, *, request=None, peek: bool = False) -> AdSlot | None:
    audience = get_ad_audience(user)
    if audience is None:
        return None
    placement = _enabled_placements().get(key)
    if placement is None:
        return None
    ad_settings = get_ad_settings()
    if audience == AUDIENCE_ADS:
        provider = ad_settings.provider
        network_ready = provider in NETWORK_PROVIDERS and placement.block_id and has_ad_consent(request)
        custom_ready = provider == AdSettings.Provider.CUSTOM and placement.custom_html.strip()
        if network_ready or custom_ready:
            return AdSlot(
                key=placement.key,
                provider=provider,
                placement=placement,
                block_id=placement.block_id,
                custom_html=placement.custom_html,
                adsense_client_id=ad_settings.adsense_client_id,
                show_remove_link=ad_settings.show_remove_link,
            )
    banner = _pick_banner(placement, user, audience, request=request, peek=peek)
    if banner is None:
        return None
    return _house_slot(placement, banner, ad_settings, is_ad=audience == AUDIENCE_ADS)


def get_ad_slot(user, key: str, *, request=None) -> AdSlot | None:
    return _resolve_slot(user, key, request=request)


def count_impression(slot: AdSlot | None, user) -> None:
    if slot is None or slot.banner is None or _is_staff(user):
        return
    HouseBanner.objects.filter(pk=slot.banner.pk).update(impressions=F("impressions") + 1)


def register_click(banner: HouseBanner, user) -> None:
    if _is_staff(user):
        return
    HouseBanner.objects.filter(pk=banner.pk).update(clicks=F("clicks") + 1)


def get_feed_every_n(user, *, request=None) -> int:
    if _resolve_slot(user, "catalog_feed", request=request, peek=True) is None:
        return 0
    return int(get_ad_settings().feed_every_n or 0)
