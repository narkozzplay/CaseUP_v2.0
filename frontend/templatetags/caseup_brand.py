from __future__ import annotations

from functools import lru_cache

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

BRAND_DIR = "img/brand"
BRAND_EXTENSIONS = (".webp", ".png", ".jpg", ".jpeg", ".svg")


def _find_brand_asset(name: str) -> str:
    base = f"{BRAND_DIR}/{name}"
    candidates = (base,) if "." in name.rsplit("/", 1)[-1] else tuple(base + ext for ext in BRAND_EXTENSIONS)
    for path in candidates:
        if finders.find(path):
            return static(path)
    return ""


_find_brand_asset_cached = lru_cache(maxsize=64)(_find_brand_asset)


@register.simple_tag
def brand_asset(name: str) -> str:
    """URL of an optional design image from static/img/brand, or "" when it is not shipped yet.

    `name` may omit the extension: "hero" resolves to hero.webp / hero.png / hero.jpg.
    """
    if settings.DEBUG:
        return _find_brand_asset(name)
    return _find_brand_asset_cached(name)
