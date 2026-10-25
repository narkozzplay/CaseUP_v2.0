"""Who sees ads and what to render. Paid plans carry the no_ads feature; the check never hides ads with CSS."""
from __future__ import annotations

from dataclasses import dataclass

from modules.v2.system.request_cache import request_memoize

from .models import AdPlacement, AdSettings

# Set by the consent bar; third-party ad networks load only after the visitor agrees to their cookies.
CONSENT_COOKIE = "caseup_ad_consent"
NETWORK_PROVIDERS = (AdSettings.Provider.YANDEX, AdSettings.Provider.GOOGLE)


@dataclass(frozen=True)
class AdSlot:
    key: str
    provider: str
    placement: AdPlacement
    block_id: str
    custom_html: str
    adsense_client_id: str
    show_remove_link: bool


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


def user_sees_ads(user) -> bool:
    ad_settings = get_ad_settings()
    if not ad_settings.is_enabled:
        return False
    if _is_staff(user):
        return ad_settings.show_to_staff
    return not _has_paid_no_ads(user)


def uses_ad_network() -> bool:
    return get_ad_settings().provider in NETWORK_PROVIDERS


def has_ad_consent(request) -> bool:
    return getattr(request, "COOKIES", {}).get(CONSENT_COOKIE) == "1"


def get_ad_slot(user, key: str, *, request=None) -> AdSlot | None:
    if not user_sees_ads(user):
        return None
    if uses_ad_network() and not has_ad_consent(request):
        return None
    placement = _enabled_placements().get(key)
    if placement is None:
        return None
    ad_settings = get_ad_settings()
    if ad_settings.provider == AdSettings.Provider.HOUSE:
        if not placement.has_house_banner:
            return None
    elif ad_settings.provider == AdSettings.Provider.CUSTOM:
        if not placement.custom_html.strip():
            return None
    elif not placement.block_id:
        return None
    return AdSlot(
        key=placement.key,
        provider=ad_settings.provider,
        placement=placement,
        block_id=placement.block_id,
        custom_html=placement.custom_html,
        adsense_client_id=ad_settings.adsense_client_id,
        show_remove_link=ad_settings.show_remove_link,
    )


def get_feed_every_n(user, *, request=None) -> int:
    if get_ad_slot(user, "catalog_feed", request=request) is None:
        return 0
    return int(get_ad_settings().feed_every_n or 0)
