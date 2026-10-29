from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()
MONEY_Q = Decimal("0.01")


def _to_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


@register.simple_tag(takes_context=True)
def money(context, value, signed=False):
    site_settings = context.get("site_settings")
    amount = _to_decimal(value)

    if site_settings is not None:
        rate = _to_decimal(getattr(site_settings, "site_currency_rate", "1.000000"))
        if rate > 0:
            amount = (amount / rate).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
        symbol = getattr(site_settings, "site_currency_symbol", "") or "₽"
    else:
        symbol = "₽"

    if str(signed).lower() in {"1", "true", "yes"} and amount > 0:
        return f"+{amount:.2f} {symbol}"
    return f"{amount:.2f} {symbol}"


@register.simple_tag(takes_context=True)
def money_value(context, value):
    site_settings = context.get("site_settings")
    amount = _to_decimal(value)
    if site_settings is not None:
        rate = _to_decimal(getattr(site_settings, "site_currency_rate", "1.000000"))
        if rate > 0:
            amount = (amount / rate).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
    return f"{amount:.2f}"


@register.simple_tag(takes_context=True)
def brand(context):
    site_settings = context.get("site_settings")
    return getattr(site_settings, "site_name", "") or "CaseUP"


@register.filter
def get_key(mapping, key):
    """{{ mapping|get_key:key }}: a dict lookup by a variable key; None when it is missing."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None
