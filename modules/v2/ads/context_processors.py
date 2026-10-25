from __future__ import annotations

from .services import get_ad_settings, user_sees_ads


def ads_context(request):
    user = getattr(request, "user", None)
    try:
        visible = user_sees_ads(user)
    except Exception:
        visible = False
    if not visible:
        return {"ads_visible": False}
    ad_settings = get_ad_settings()
    return {
        "ads_visible": True,
        "ads_provider": ad_settings.provider,
        "ads_adsense_client_id": ad_settings.adsense_client_id,
    }
