from __future__ import annotations

import urllib.request

from django.conf import settings

from .alerts import (
    _build_price_drop_alert_message,
    detect_price_drop_alerts_for_category,
    detect_price_movement_alerts_for_category,
)
from .catalog import CreateCatalogResult, upsert_from_market_input
from .fetch import (
    RefreshPriceResult,
    fetch_price_rub_from_steam,
    record_price_snapshot,
    refresh_item_prices,
    try_fetch_listing_metadata,
    try_fetch_listing_render_data,
)
from .helpers import (
    build_skin_market_hash_name,
    canonical_listing_url,
    derive_ru_name_from_market_hash_name,
    extract_market_hash_name,
    guess_item_type,
    normalize_icon_url,
    normalize_rarity_key,
    parse_skin_market_hash_name,
    rarity_ru_label_from_key,
    weapon_from_base_name,
)
from .market_search import MarketSearchError, MarketSearchResult, search_market_items
from .telegram import _telegram_send_message, dispatch_pending_price_drop_alerts
from .user_alerts import dispatch_pending_user_price_alerts, evaluate_user_price_alerts_for_item_type

__all__ = [
    "CreateCatalogResult",
    "MarketSearchError",
    "MarketSearchResult",
    "RefreshPriceResult",
    "_build_price_drop_alert_message",
    "_telegram_send_message",
    "build_skin_market_hash_name",
    "canonical_listing_url",
    "derive_ru_name_from_market_hash_name",
    "detect_price_drop_alerts_for_category",
    "detect_price_movement_alerts_for_category",
    "dispatch_pending_price_drop_alerts",
    "dispatch_pending_user_price_alerts",
    "evaluate_user_price_alerts_for_item_type",
    "extract_market_hash_name",
    "fetch_price_rub_from_steam",
    "guess_item_type",
    "normalize_icon_url",
    "normalize_rarity_key",
    "parse_skin_market_hash_name",
    "rarity_ru_label_from_key",
    "search_market_items",
    "record_price_snapshot",
    "refresh_item_prices",
    "try_fetch_listing_metadata",
    "try_fetch_listing_render_data",
    "upsert_from_market_input",
    "weapon_from_base_name",
]
