from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from django.conf import settings
from django.templatetags.static import static

from modules.v2.profiles.presentation import (
    get_default_no_image_url,
    get_user_avatar_url,
    get_user_display_name,
    get_user_role_prefix,
)


STATIC_PATH_PREFIXES = (
    "img/",
    "icons/",
    "css/",
    "js/",
)


def _to_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _looks_like_absolute_url(raw: str) -> bool:
    return raw.startswith(("http://", "https://", "//"))


def _resolve_image_url(raw_value: str) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return ""
    if _looks_like_absolute_url(raw):
        return raw
    if raw.startswith("/"):
        return raw
    if raw.startswith("media/") or raw.startswith("static/"):
        return f"/{raw}"
    if raw.startswith(STATIC_PATH_PREFIXES):
        return static(raw)

    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/").strip()
    if not media_url.startswith("/"):
        media_url = f"/{media_url}"
    return f"{media_url.rstrip('/')}/{raw.lstrip('/')}"


def get_product_preview_url(listing) -> str:
    preview_url = _resolve_image_url(str(getattr(listing, "cover_image", "") or ""))
    if preview_url:
        return preview_url
    return get_default_no_image_url()


def normalize_rating_to_half(value) -> Decimal:
    raw_value = _to_decimal(value)
    bounded = min(max(raw_value, Decimal("0")), Decimal("5"))
    half_steps = int((bounded * 2).to_integral_value(rounding=ROUND_FLOOR))
    return Decimal(half_steps) / Decimal("2")


def build_star_states(value, *, total_stars: int = 5) -> tuple[str, ...]:
    normalized = normalize_rating_to_half(value)
    states: list[str] = []
    remaining = normalized
    for _index in range(max(total_stars, 0)):
        if remaining >= 1:
            states.append("full")
            remaining -= Decimal("1")
        elif remaining >= Decimal("0.5"):
            states.append("half")
            remaining -= Decimal("0.5")
        else:
            states.append("empty")
    return tuple(states)


def build_star_rating_data(value, *, precision: int = 1) -> dict[str, object]:
    raw_value = _to_decimal(value)
    if raw_value < 0:
        raw_value = Decimal("0")
    if raw_value > 5:
        raw_value = Decimal("5")
    normalized = normalize_rating_to_half(raw_value)
    decimals = max(int(precision), 0)
    value_label = f"{raw_value:.{decimals}f}"
    return {
        "raw_value": raw_value,
        "normalized_value": normalized,
        "value_label": value_label,
        "states": build_star_states(raw_value),
    }


def format_reviews_count_label(total_reviews: int) -> str:
    count = max(int(total_reviews or 0), 0)
    mod100 = count % 100
    mod10 = count % 10
    if 11 <= mod100 <= 14:
        suffix = "отзывов"
    elif mod10 == 1:
        suffix = "отзыв"
    elif mod10 in {2, 3, 4}:
        suffix = "отзыва"
    else:
        suffix = "отзывов"
    return f"{count} {suffix}"


def get_review_access_badges(access_source: str) -> tuple[str, ...]:
    normalized_source = str(access_source or "").strip().lower()
    if normalized_source == "purchase":
        return ("Куплено",)
    if normalized_source == "bundle_purchase":
        return ("Куплено в наборе",)
    if normalized_source == "subscription":
        return ("В подписке",)
    if normalized_source == "admin":
        return ("Админ доступ",)
    return tuple()


def build_review_presenter(review) -> dict[str, object]:
    return {
        "review": review,
        "display_name": get_user_display_name(getattr(review, "user", None)),
        "role_prefix": get_user_role_prefix(getattr(review, "user", None)),
        "avatar_url": get_user_avatar_url(getattr(review, "user", None)),
        "rating": int(getattr(review, "rating", 0) or 0),
        "rating_data": build_star_rating_data(getattr(review, "rating", 0), precision=1),
        "access_badges": get_review_access_badges(getattr(review, "access_source_snapshot", "")),
    }

