from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

# Plan banners live in static files named after the plan code: img/plans/<code>.webp.
# Until a picture is added the card shows a CSS placeholder in the plan's colour.
PLAN_BANNER_PATH = "img/plans/{code}.webp"


@lru_cache(maxsize=64)
def _banner_path(code: str) -> str:
    path = PLAN_BANNER_PATH.format(code=code)
    return path if finders.find(path) else ""


@register.simple_tag
def plan_banner_url(code) -> str:
    path = _banner_path(str(code or ""))
    return static(path) if path else ""
