from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

from django.core.cache import cache

from modules.v2.steam_catalog.models import SteamItem

from .helpers import guess_item_type, normalize_rarity_key, parse_skin_market_hash_name, rarity_ru_label_from_key

SEARCH_URL = "https://steamcommunity.com/market/search/render/"
SEARCH_CACHE_SECONDS = 300
SEARCH_MIN_QUERY_LENGTH = 2
ICON_CDN = "https://community.cloudflare.steamstatic.com/economy/image/{icon}/128x128"


class MarketSearchError(Exception):
    pass


@dataclass(frozen=True)
class MarketSearchResult:
    hash_name: str
    name: str
    icon: str
    type_label: str
    item_type: str
    rarity_key: str
    rarity_label: str
    price_text: str
    listings: int
    catalog_item_id: int | None

    def as_dict(self) -> dict:
        return asdict(self)


def _fetch_search_payload(*, query: str, count: int, appid: int, timeout: float) -> dict:
    params = urlencode({
        "query": query,
        "appid": appid,
        "norender": 1,
        "count": count,
        "start": 0,
        "search_descriptions": 0,
        "sort_column": "popular",
        "sort_dir": "desc",
        "l": "english",
    })
    req = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={
            "User-Agent": "Mozilla/5.0 (CaseUP) AppleWebKit/537.36 Chrome/122 Safari/537.36",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")


def _catalog_ids_for(hash_names: list[str]) -> dict[str, int]:
    """Map every result hash name to an existing catalog item (exact match or same skin family)."""
    family_by_hash = {}
    for hash_name in hash_names:
        _, base_name, exterior = parse_skin_market_hash_name(hash_name)
        family_by_hash[hash_name] = base_name if exterior and base_name else hash_name

    rows = (
        SteamItem.objects
        .filter(market_hash_name__in=hash_names)
        .values_list("market_hash_name", "id")
    )
    found = {name: item_id for name, item_id in rows}

    missing_families = {family for name, family in family_by_hash.items() if name not in found}
    if missing_families:
        family_rows = (
            SteamItem.objects
            .filter(family_name__in=missing_families)
            .order_by("is_stattrak", "wear_order", "id")
            .values_list("family_name", "id")
        )
        family_ids: dict[str, int] = {}
        for family, item_id in family_rows:
            family_ids.setdefault(family, item_id)
        for name, family in family_by_hash.items():
            if name not in found and family in family_ids:
                found[name] = family_ids[family]
    return found


def search_market_items(*, query: str, count: int = 12, appid: int = 730, timeout: float = 8.0) -> list[MarketSearchResult]:
    query = (query or "").strip()
    if len(query) < SEARCH_MIN_QUERY_LENGTH:
        return []

    cache_key = f"steam_market_search:{appid}:{count}:{query.lower()}"
    raw_results = cache.get(cache_key)
    if raw_results is None:
        try:
            payload = _fetch_search_payload(query=query, count=count, appid=appid, timeout=timeout)
        except Exception as exc:
            raise MarketSearchError("Steam Market не ответил. Попробуй ещё раз чуть позже.") from exc
        if not payload.get("success"):
            raise MarketSearchError("Steam Market вернул ошибку. Попробуй ещё раз чуть позже.")
        raw_results = payload.get("results") or []
        cache.set(cache_key, raw_results, SEARCH_CACHE_SECONDS)

    hash_names = [str(row.get("hash_name") or "").strip() for row in raw_results]
    catalog_ids = _catalog_ids_for([name for name in hash_names if name])

    results: list[MarketSearchResult] = []
    for row, hash_name in zip(raw_results, hash_names):
        if not hash_name:
            continue
        description = row.get("asset_description") or {}
        type_label = str(description.get("type") or "")
        rarity_key = normalize_rarity_key(type_label)
        icon = str(description.get("icon_url") or "")
        results.append(MarketSearchResult(
            hash_name=hash_name,
            name=str(row.get("name") or hash_name),
            icon=ICON_CDN.format(icon=icon) if icon else "",
            type_label=type_label,
            item_type=str(guess_item_type(hash_name)),
            rarity_key=rarity_key,
            rarity_label=rarity_ru_label_from_key(rarity_key),
            price_text=str(row.get("sell_price_text") or ""),
            listings=int(row.get("sell_listings") or 0),
            catalog_item_id=catalog_ids.get(hash_name),
        ))
    return results
