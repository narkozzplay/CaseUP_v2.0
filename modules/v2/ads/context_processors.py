from __future__ import annotations

from .services import get_ad_settings, has_ad_consent, user_sees_ads, uses_ad_network


def ads_context(request):
    user = getattr(request, "user", None)
    try:
        visible = user_sees_ads(user)
    except Exception:
        visible = False
    if not visible:
        return {"ads_visible": False}
    ad_settings = get_ad_settings()
    network = uses_ad_network()
    consent = has_ad_consent(request)
    return {
        "ads_visible": True,
        "ads_provider": ad_settings.provider,
        "ads_adsense_client_id": ad_settings.adsense_client_id,
        "ads_network_allowed": network and consent,
        "ads_ask_consent": network and not consent,
    }
