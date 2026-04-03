from decimal import Decimal
from django import template

register = template.Library()

def _q(value):
    if value in (None, ""):
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))

@register.simple_tag(takes_context=True)
def money(context, value, signed=False):
    site_settings = context.get("site_settings")
    if site_settings is None:
        amount = _q(value)
        return f"{amount:.2f} ₽"

    rate = Decimal(str(site_settings.site_currency_rate or "1.000000"))
    if rate <= 0:
        rate = Decimal("1.000000")

    amount = (_q(value) / rate).quantize(Decimal("0.01"))
    symbol = site_settings.site_currency_symbol or "₽"

    if signed and amount > 0:
        return f"+{amount:.2f} {symbol}"
    return f"{amount:.2f} {symbol}"

@register.simple_tag(takes_context=True)
def brand(context):
    site_settings = context.get("site_settings")
    return getattr(site_settings, "site_name", "") or "CaseUP"