"""Profile effects next to any user name: {% pfx_look user_id as look %} then the _fx_name.html include."""
from django import template

from ..services import get_profile_look

register = template.Library()


@register.simple_tag
def pfx_look(user_or_id):
    try:
        return get_profile_look(user_or_id)
    except Exception:  # noqa: BLE001 - decoration must never break a page
        return {}
