"""Title, description, canonical address and structured data for the public catalog pages.

The values go to the template context under seo_* names; components/seo_meta.html prints them.
"""
from __future__ import annotations

import json
from decimal import Decimal

from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import get_language

from modules.v2.system.services import get_site_name

# Filters that only narrow the list by type keep the page indexable; any other filter makes a near-copy.
_INDEXABLE_PARAMS = {"type", "page"}


def _is_ru() -> bool:
    return (get_language() or "ru")[:2] == "ru"


def _absolute(request, path: str) -> str:
    return request.build_absolute_uri(path)


def _price_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:,.2f}".replace(",", " ").replace(".00", "") + " ₽"


def _json_ld(data: dict) -> str:
    # Escapes keep a name like "</script>" from closing the tag early.
    text = json.dumps(data, ensure_ascii=False)
    return mark_safe(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def item_title(item) -> str:
    if _is_ru():
        return f"{item.market_hash_name} — цена в Steam сегодня, график | {get_site_name()}"
    return f"{item.market_hash_name} — Steam price today, chart | {get_site_name()}"


def item_meta(request, item, *, family_min_price=None, family_max_price=None) -> dict:
    name = item.market_hash_name
    price = _price_text(item.price_rub)
    if _is_ru():
        parts = [f"Цена {name} на торговой площадке Steam сейчас: {price}." if price else f"Цена {name} на торговой площадке Steam."]
        if family_min_price is not None and family_max_price is not None and family_min_price != family_max_price:
            parts.append(f"Все варианты износа: от {_price_text(family_min_price)} до {_price_text(family_max_price)}.")
        parts.append("График и история цены за день, неделю, месяц и год. Бесплатно, алерты на изменение цены.")
    else:
        parts = [f"{name} price on the Steam Community Market now: {price}." if price else f"{name} price on the Steam Community Market."]
        if family_min_price is not None and family_max_price is not None and family_min_price != family_max_price:
            parts.append(f"All exteriors: from {_price_text(family_min_price)} to {_price_text(family_max_price)}.")
        parts.append("Price chart and history for a day, week, month and year. Free, with price alerts.")

    description = " ".join(parts)
    canonical = _absolute(request, item.get_absolute_url())
    image = item.icon_cdn(512)
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "description": description,
        "url": canonical,
        "category": item.get_item_type_display(),
        "brand": {"@type": "Brand", "name": "Counter-Strike 2"},
    }
    if image:
        product["image"] = image
    if item.price_rub is not None:
        # The site only shows the Steam price and sells nothing itself. AggregateOffer keeps the price in
        # the search snippet without marking the page as a shop listing, which would ask for shipping and returns.
        price = f"{item.price_rub:.2f}"
        product["offers"] = {
            "@type": "AggregateOffer",
            "lowPrice": price,
            "highPrice": price,
            "priceCurrency": "RUB",
            "url": canonical,
        }
    return {
        "seo_title": item_title(item),
        "seo_description": description,
        "seo_canonical": canonical,
        "seo_image": image,
        "seo_og_type": "product",
        "seo_jsonld": _json_ld(product),
    }


def list_meta(request, filters, page_obj) -> dict:
    site = get_site_name()
    if _is_ru():
        title = f"Цены скинов CS2 в Steam — каталог предметов | {site}"
        description = "Актуальные цены скинов, кейсов, наклеек и брелоков CS2 на торговой площадке Steam. Графики цен, все варианты износа и алерты на изменение цены — бесплатно."
    else:
        title = f"CS2 skin prices on Steam — item catalog | {site}"
        description = "Current Steam Community Market prices for CS2 skins, cases, stickers and charms. Price charts, every exterior and price alerts, free."

    params = {key for key, value in request.GET.items() if value}
    canonical_params = []
    if filters.type and "type" in params:
        canonical_params.append(f"type={filters.type}")
    if page_obj.number > 1:
        canonical_params.append(f"page={page_obj.number}")
    canonical = reverse("steam_catalog:list") + (f"?{'&'.join(canonical_params)}" if canonical_params else "")
    return {
        "seo_title": title,
        "seo_description": description,
        "seo_canonical": _absolute(request, canonical),
        # Search results, price ranges and favorites are near-copies of the catalog: robots may follow, not index.
        "seo_robots": "" if params <= _INDEXABLE_PARAMS else "noindex, follow",
    }
