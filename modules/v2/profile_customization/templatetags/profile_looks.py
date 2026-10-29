"""Profile effects next to any user name: {% pfx_look user_id as look %} then the _fx_name.html include."""
from django import template

from ..services import get_profile_look, prime_looks

register = template.Library()


@register.simple_tag
def pfx_look(user_or_id):
    try:
        return get_profile_look(user_or_id)
    except Exception:  # noqa: BLE001 - decoration must never break a page
        return {}


@register.simple_tag
def pfx_prime(rows, attr):
    """Batch-load the looks for a list before rendering it: {% pfx_prime social_messages "author_user_id" %}."""
    ids = []
    for row in rows or ():
        value = row.get(attr) if isinstance(row, dict) else getattr(row, attr, None)
        ids.append(getattr(value, "pk", value))
    try:
        prime_looks(ids)
    except Exception:  # noqa: BLE001 - decoration must never break a page
        pass
    return ""
