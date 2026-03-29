from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import transaction

from modules.v2.steam_catalog.models import SteamItem

from .constants import EXTERIOR_LABELS, EXTERIOR_ORDER

from .helpers import (
    build_skin_market_hash_name,
    canonical_listing_url,
    extract_market_hash_name,
    guess_item_type,
    normalize_rarity_key,
    parse_skin_market_hash_name,
    rarity_ru_label_from_key,
    weapon_from_base_name,
)


@dataclass(frozen=True)
class CreateCatalogResult:
    anchor_item: SteamItem
    created_count: int
    variants_created: int
    market_hash_name: str

def _services_pkg():
    import modules.v2.steam_catalog.services as services_pkg
    return services_pkg

def _base_defaults(
    *,
    market_hash_name: str,
    family_name: str,
    item_type: str,
    name: str,
    icon_url: str,
    rarity: str,
    exterior: str,
    weapon: str,
    price_rub: Decimal | None,
    is_stattrak: bool = False,
) -> dict[str, Any]:
    rarity_key = normalize_rarity_key(rarity)
    rarity_display = rarity_ru_label_from_key(rarity_key) or rarity

    return {
        "name": name or market_hash_name,
        "name_ru": None,
        "family_name": family_name or market_hash_name,
        "item_type": item_type,
        "icon_url": icon_url or "",
        "rarity": rarity_display or "",
        "rarity_key": rarity_key or "",
        "exterior": exterior or "",
        "weapon": weapon or "",
        "price_rub": price_rub,
        "is_stattrak": is_stattrak,
        "wear_order": EXTERIOR_ORDER.get(exterior or "", 0),
        "allow_buy": True,
        "allow_sell": True,
        "is_active": True,
        "needs_review": False,
        "created_from_scan": False,
    }


def _apply_missing_fields(item: SteamItem, defaults: dict[str, Any], *, set_stock: int | None = None) -> None:
    dirty_fields: list[str] = []

    for field, value in defaults.items():
        current = getattr(item, field)

        if field == "price_rub":
            if value is not None and current != value:
                setattr(item, field, value)
                dirty_fields.append(field)
            continue

        if field in {"family_name", "is_stattrak", "wear_order"}:
            if current != value:
                setattr(item, field, value)
                dirty_fields.append(field)
            continue

        if value and not current:
            setattr(item, field, value)
            dirty_fields.append(field)

    if set_stock is not None and item.stock != set_stock:
        item.stock = set_stock
        dirty_fields.append("stock")

    if dirty_fields:
        dirty_fields.append("updated_at")
        item.save(update_fields=sorted(set(dirty_fields)))


@transaction.atomic
def upsert_from_market_input(*, value: str, stock: int = 0, appid: int = 730) -> CreateCatalogResult:
    market_hash_name = extract_market_hash_name(value)
    if not market_hash_name:
        raise ValueError("Empty market_hash_name")

    listing_url = canonical_listing_url(appid=appid, market_hash_name=market_hash_name)

    try:
        meta = _services_pkg().try_fetch_listing_metadata(url=listing_url)
    except Exception:
        meta = {}

    try:
        render_data = _services_pkg().try_fetch_listing_render_data(url=listing_url)
    except Exception:
        render_data = {}

    try:
        price_rub = _services_pkg().fetch_price_rub_from_steam(
            market_hash_name=market_hash_name,
            appid=appid,
        )
    except Exception:
        price_rub = None

    item_type = guess_item_type(market_hash_name)

    if item_type != SteamItem.ItemType.SKIN:
        defaults = _base_defaults(
            market_hash_name=market_hash_name,
            family_name=market_hash_name,
            item_type=item_type,
            name=(meta.get("name") or market_hash_name).strip(),
            icon_url=(meta.get("icon_url") or render_data.get("icon_url") or "").strip(),
            rarity=(render_data.get("rarity") or "").strip(),
            exterior="",
            weapon="",
            price_rub=price_rub,
            is_stattrak=False,
        )
        item, created = SteamItem.objects.get_or_create(
            market_hash_name=market_hash_name,
            defaults={**defaults, "stock": int(stock or 0)},
        )
        if not created:
            _apply_missing_fields(item, defaults, set_stock=int(stock or 0))

        return CreateCatalogResult(
            anchor_item=item,
            created_count=1 if created else 0,
            variants_created=0,
            market_hash_name=market_hash_name,
        )

    is_stattrak, base_name, parsed_exterior = parse_skin_market_hash_name(market_hash_name)
    weapon = weapon_from_base_name(base_name)
    rarity = (render_data.get("rarity") or "").strip()
    icon_url = (meta.get("icon_url") or render_data.get("icon_url") or "").strip()

    anchor_hash = market_hash_name
    if not parsed_exterior:
        parsed_exterior = EXTERIOR_LABELS[0]
        anchor_hash = build_skin_market_hash_name(
            is_stattrak=is_stattrak,
            base_name=base_name,
            exterior=parsed_exterior,
        )

    created_count = 0
    anchor_item: SteamItem | None = None

    for stattrak_flag in (False, True):
        for exterior in EXTERIOR_LABELS:
            variant_hash = build_skin_market_hash_name(
                is_stattrak=stattrak_flag,
                base_name=base_name,
                exterior=exterior,
            )

            try:
                variant_price = _services_pkg().fetch_price_rub_from_steam(
                    market_hash_name=variant_hash,
                    appid=appid,
                )
            except Exception:
                variant_price = price_rub

            variant_defaults = _base_defaults(
                market_hash_name=variant_hash,
                family_name=base_name,
                item_type=SteamItem.ItemType.SKIN,
                name=variant_hash,
                icon_url=icon_url,
                rarity=rarity,
                exterior=exterior,
                weapon=weapon,
                price_rub=variant_price,
                is_stattrak=stattrak_flag,
            )
            variant_defaults["name_ru"] = None

            variant_stock = int(stock or 0) if variant_hash == anchor_hash else 0

            item, created = SteamItem.objects.get_or_create(
                market_hash_name=variant_hash,
                defaults={**variant_defaults, "stock": variant_stock},
            )
            if created:
                created_count += 1
            else:
                _apply_missing_fields(
                    item,
                    variant_defaults,
                    set_stock=variant_stock if variant_hash == anchor_hash else None,
                )

            if variant_hash == anchor_hash:
                anchor_item = item

    if anchor_item is None:
        fallback_defaults = _base_defaults(
            market_hash_name=market_hash_name,
            family_name=base_name,
            item_type=SteamItem.ItemType.SKIN,
            name=(meta.get("name") or market_hash_name).strip(),
            icon_url=icon_url,
            rarity=rarity,
            exterior=parsed_exterior,
            weapon=weapon,
            price_rub=price_rub,
            is_stattrak=is_stattrak,
        )

        anchor_item, created = SteamItem.objects.get_or_create(
            market_hash_name=market_hash_name,
            defaults={**fallback_defaults, "stock": int(stock or 0)},
        )
        if created:
            created_count += 1
        else:
            _apply_missing_fields(anchor_item, fallback_defaults, set_stock=int(stock or 0))

    return CreateCatalogResult(
        anchor_item=anchor_item,
        created_count=created_count,
        variants_created=10,
        market_hash_name=market_hash_name,
    )


__all__ = ["CreateCatalogResult", "upsert_from_market_input"]
