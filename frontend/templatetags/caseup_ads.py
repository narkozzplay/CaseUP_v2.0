from django import template

from modules.v2.ads.services import get_ad_slot

register = template.Library()


@register.inclusion_tag("ads/slot.html", takes_context=True)
def ad_slot(context, key: str):
    request = context.get("request")
    user = getattr(request, "user", None)
    return {"slot": get_ad_slot(user, key, request=request)}
