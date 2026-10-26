from __future__ import annotations

import html
import json
import re
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory

from .constants import _PRICE_RE
from .helpers import normalize_icon_url


@dataclass(frozen=True)
class RefreshPriceResult:
    updated_count: int
    history_written: int
    touched_count: int
    last_synced_at: Any


def _services_pkg():
    import modules.v2.steam_catalog.services as services_pkg
    return services_pkg


def _extract_meta_by_regex(html_text: str, attr_name: str, attr_value: str) -> str:
    match = re.search(
        rf'<meta[^>]+{attr_name}=["\']{re.escape(attr_value)}["\'][^>]+content=["\']([^"\']+)["\']',
        html_text,
        flags=re.IGNORECASE,
    )
    return (match.group(1).strip() if match else "")


def try_fetch_listing_metadata(*, url: str, timeout: float = 10.0) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (CaseUP) AppleWebKit/537.36 Chrome/122 Safari/537.36",
        "Accept-Language": "ru,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html_text = resp.read().decode("utf-8", errors="ignore")

    name = (
        _extract_meta_by_regex(html_text, "property", "og:title")
        or _extract_meta_by_regex(html_text, "name", "twitter:title")
    ).strip()

    icon_url = (
        _extract_meta_by_regex(html_text, "property", "og:image")
        or _extract_meta_by_regex(html_text, "name", "twitter:image")
        or _extract_meta_by_regex(html_text, "name", "image")
    ).strip()

    if "::" in name:
        name = name.split("::", 1)[1].strip()

    name = html.unescape(name)
    name = re.sub(r"^(Лоты|Лот)\s+", "", name, flags=re.IGNORECASE).strip()

    return {
        "name": name,
        "icon_url": normalize_icon_url(icon_url),
    }


def try_fetch_listing_render_data(*, url: str, timeout: float = 10.0) -> dict[str, str]:
    render_url = url.rstrip("/") + "/render/?query=&start=0&count=1&currency=5&language=english"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en,ru;q=0.9",
        "Referer": url,
    }
    req = urllib.request.Request(render_url, headers=headers)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")

    rarity = ""
    exterior = ""
    icon_url = ""

    assets = payload.get("assets") or {}
    for app_assets in assets.values():
        if not isinstance(app_assets, dict):
            continue
        for context_assets in app_assets.values():
            if not isinstance(context_assets, dict):
                continue
            for asset in context_assets.values():
                if not isinstance(asset, dict):
                    continue
                icon_url = asset.get("icon_url_large") or asset.get("icon_url") or icon_url
                if icon_url:
                    break
            if icon_url:
                break
        if icon_url:
            break

    def iter_tag_dicts(node: Any):
        if isinstance(node, dict):
            tags = node.get("tags")
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, dict):
                        yield tag
            for value in node.values():
                yield from iter_tag_dicts(value)
        elif isinstance(node, list):
            for value in node:
                yield from iter_tag_dicts(value)

    def _norm(value: str) -> str:
        return (value or "").strip().lower()

    def _category_tokens(tag: dict) -> list[str]:
        return [
            _norm(tag.get("category")),
            _norm(tag.get("category_name")),
            _norm(tag.get("localized_category_name")),
            _norm(tag.get("localized_tag_name")),
            _norm(tag.get("internal_name")),
        ]

    def _is_rarity(tokens: list[str]) -> bool:
        joined = " ".join(tokens)
        return "rarity" in joined or "quality" in joined or "редк" in joined or "качест" in joined

    def _is_exterior(tokens: list[str]) -> bool:
        joined = " ".join(tokens)
        return (
            "exterior" in joined
            or "wear" in joined
            or "condition" in joined
            or "износ" in joined
            or "состоя" in joined
        )

    for tag in iter_tag_dicts(payload):
        name = (tag.get("name") or tag.get("localized_tag_name") or "").strip()
        if not name:
            continue

        tokens = _category_tokens(tag)
        if not rarity and _is_rarity(tokens):
            rarity = name
        if not exterior and _is_exterior(tokens):
            exterior = name

        if rarity and exterior:
            break

    return {
        "rarity": rarity,
        "exterior": exterior,
        "icon_url": normalize_icon_url(icon_url),
    }


def _parse_price(text: str | None) -> Decimal | None:
    if not text:
        return None

    match = _PRICE_RE.search(text)
    if not match:
        return None

    s = match.group(0).replace(" ", "")
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")

    try:
        return Decimal(s)
    except Exception:
        return None


def fetch_price_rub_from_steam(*, market_hash_name: str, appid: int = 730, timeout: float = 10.0) -> Decimal | None:
    url = (
        "https://steamcommunity.com/market/priceoverview/"
        f"?appid={appid}&currency=5&market_hash_name={quote(market_hash_name)}"
    )
    headers = {
        "User-Agent": "CaseUP/steam_catalog",
        "Accept-Language": "ru,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")

    return _parse_price(payload.get("median_price")) or _parse_price(payload.get("lowest_price"))


def record_price_snapshot(*, item: SteamItem, price_rub: Decimal | None, source: str = "manual") -> SteamItemPriceHistory | None:
    if price_rub is None:
        return None

    last_entry = (
        SteamItemPriceHistory.objects
        .filter(item=item)
        .order_by("-created_at")
        .only("price_rub")
        .first()
    )

    if last_entry and last_entry.price_rub == price_rub:
        return None

    return SteamItemPriceHistory.objects.create(
        item=item,
        price_rub=price_rub,
        source=(source or "manual").strip() or "manual",
    )


def refresh_item_prices(
    item: SteamItem,
    *,
    appid: int = 730,
    include_family: bool = True,
    source: str = "manual",
) -> RefreshPriceResult:
    if item.item_type == SteamItem.ItemType.SKIN and include_family:
        family_key = item.family_name or item.market_hash_name
        targets = list(
            SteamItem.objects
            .filter(family_name=family_key)
            .order_by("is_stattrak", "wear_order", "market_hash_name")
        )
    else:
        targets = [item]

    fetched_prices: list[tuple[int, Decimal]] = []

    from .price_queue import count_manual_request

    for target in targets:
        count_manual_request()
        try:
            price_rub = _services_pkg().fetch_price_rub_from_steam(
                market_hash_name=target.market_hash_name,
                appid=appid,
            )
        except Exception:
            price_rub = None

        if price_rub is None:
            continue

        fetched_prices.append((target.id, price_rub))

    updated_count = 0
    history_written = 0
    last_synced_at = timezone.now()

    with transaction.atomic():
        for item_id, price_rub in fetched_prices:
            locked_item = SteamItem.objects.select_for_update().get(pk=item_id)

            if locked_item.price_rub != price_rub:
                locked_item.price_rub = price_rub
                updated_count += 1
            # A fresh price counts as a price-queue check too: the item is listed again and not due for a while.
            locked_item.price_missing = False
            locked_item.price_checked_at = last_synced_at
            locked_item.save(update_fields=["price_rub", "price_missing", "price_checked_at", "updated_at"])

            history_entry = record_price_snapshot(item=locked_item, price_rub=price_rub, source=source)
            if history_entry is not None:
                history_written += 1

    return RefreshPriceResult(
        updated_count=updated_count,
        history_written=history_written,
        touched_count=len(fetched_prices),
        last_synced_at=last_synced_at,
    )


__all__ = [
    "RefreshPriceResult",
    "fetch_price_rub_from_steam",
    "record_price_snapshot",
    "refresh_item_prices",
    "try_fetch_listing_metadata",
    "try_fetch_listing_render_data",
]
