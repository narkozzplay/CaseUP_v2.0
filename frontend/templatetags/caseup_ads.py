from django import template

from modules.v2.ads.services import count_impression, get_ad_slot

register = template.Library()


@register.inclusion_tag("ads/slot.html", takes_context=True)
def ad_slot(context, key: str):
    request = context.get("request")
    user = getattr(request, "user", None)
    slot = get_ad_slot(user, key, request=request)
    count_impression(slot, user)
    return {"slot": slot}


@register.inclusion_tag("ads/info_banner.html", takes_context=True)
def info_banner(context, slot: str):
    from modules.v2.ads.info_banners import get_info_banners

    request = context.get("request")
    language = context.get("LANGUAGE_CODE", "ru")
    banners = get_info_banners(slot, getattr(request, "user", None))
    for banner in banners:
        # Site links are stored without the language prefix; English pages keep visitors on /en/.
        banner.href = banner.url
        if language == "en" and banner.url.startswith("/") and not banner.url.startswith(("/en/", "//")):
            banner.href = f"/en{banner.url}"
    return {"banners": banners, "slot": slot, "LANGUAGE_CODE": language}
