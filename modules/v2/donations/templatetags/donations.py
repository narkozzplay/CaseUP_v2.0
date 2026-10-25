from django import template

from ..services import widget_state_for

register = template.Library()


@register.inclusion_tag("donations/widget.html", takes_context=True)
def donation_widget(context, placement: str):
    """The fundraiser widget for a page slot; renders nothing when it is off or hidden there."""
    return {
        "state": widget_state_for(placement),
        "placement": placement,
        "site_settings": context.get("site_settings"),
        "LANGUAGE_CODE": context.get("LANGUAGE_CODE"),
        "request": context.get("request"),
    }
