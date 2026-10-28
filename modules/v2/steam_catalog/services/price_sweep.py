"""Market sweep: the whole catalog's lowest prices, 100 items per Steam request.

priceoverview gives one price per request, and Steam lets an address make only a few dozen of those an hour, so
the per-item queue (price_queue.py) can never get through tens of thousands of items. The market search
(``search/render?norender=1``) lists 100 items per page with their lowest listing, so a full pass over the
market takes a few hundred requests. Each run reads a few pages in name order and carries on from where the
previous run stopped; after the last page it starts over.

The search answers in US dollars only. The rate comes from Steam itself: the lowest listing of one liquid item
asked in dollars and in roubles (two priceoverview requests twice a day), or ``STEAM_USD_RUB_RATE`` when set.

Hot and warm items are left to the per-item queue: it keeps their median price, which is what alerts, the
channel digest and portfolios use. The sweep refreshes the rest, so the queue's cold tier has almost nothing
left to do. History from the sweep is marked ``SWEEP_SOURCE`` and written at most every few hours per item
unless the price jumped, because the lowest listing moves all the time.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote, urlencode

from django.core.cache import cache
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory

from .fetch import _parse_price
from .price_queue import (
    DIRECT,
    SWEEP_SOURCE,
    _acquire_route,
    _hot_filter,
    _setting,
    _warm_filter,
    note_rate_limited,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://steamcommunity.com/market/search/render/"
PRICEOVERVIEW_URL = "https://steamcommunity.com/market/priceoverview/"
PAGE_SIZE = 100
STATE_KEY = "steam_price_sweep:state"
RATE_KEY = "steam_price_sweep:usd_rub"
RATE_LAST_KEY = "steam_price_sweep:usd_rub:last"
RATE_HOURS = 12
# A sweep history row is written when the last one is older than this or the price moved at least HISTORY_JUMP.
HISTORY_HOURS = 6
HISTORY_JUMP = Decimal("0.05")
USD, RUB = 1, 5
HEADERS = {
    "User-Agent": "Mozilla/5.0 (CaseUP) AppleWebKit/537.36 Chrome/122 Safari/537.36",
    "Accept": "application/json",
}


@dataclass
class SweepResult:
    pages: int = 0
    seen: int = 0
    matched: int = 0
    updated: int = 0
    history_written: int = 0
    skipped: int = 0  # rows priced in another currency
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _get_json(url: str, timeout: float = 15.0) -> dict:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="ignore") or "{}")
    return payload if isinstance(payload, dict) else {}


def _amount(text) -> Decimal | None:
    # «1 785,23 pуб.»: the thousands separator would cut the number short.
    return _parse_price(re.sub(r"\s", "", str(text or ""))) if text else None


# --------------------------------------------------------------------------- USD → RUB


def _lowest_price(market_hash_name: str, currency: int, deadline) -> Decimal | None:
    if _acquire_route(deadline, only=DIRECT) is None:
        return None
    query = f"appid=730&currency={currency}&market_hash_name={quote(market_hash_name)}"
    return _amount(_get_json(f"{PRICEOVERVIEW_URL}?{query}").get("lowest_price"))


def _measure_rate(deadline, result: SweepResult) -> Decimal | None:
    name = _setting("STEAM_PRICE_SWEEP_RATE_ITEM", "AK-47 | Redline (Field-Tested)")
    try:
        usd = _lowest_price(name, USD, deadline)
        rub = _lowest_price(name, RUB, deadline) if usd else None
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            result.notes.append(note_rate_limited(DIRECT))
        else:
            result.errors.append(f"Курс доллара: Steam ответил HTTP {exc.code}")
        return None
    except Exception as exc:  # noqa: BLE001 - the last known rate is used instead
        result.errors.append(f"Курс доллара: {exc}")
        return None
    if not usd or not rub:
        return None
    return (rub / usd).quantize(Decimal("0.0001"))


def usd_rub_rate(*, deadline=None, result: SweepResult | None = None) -> Decimal | None:
    """Roubles per dollar as Steam converts them; measured twice a day, the last known value when Steam is busy."""
    fixed = _setting("STEAM_USD_RUB_RATE", 0)
    if fixed:
        return Decimal(str(fixed))
    cached = cache.get(RATE_KEY)
    if cached:
        return Decimal(cached)
    rate = _measure_rate(deadline, result or SweepResult())
    if rate:
        cache.set(RATE_KEY, str(rate), timeout=RATE_HOURS * 3600)
        cache.set(RATE_LAST_KEY, str(rate), timeout=None)
        return rate
    last = cache.get(RATE_LAST_KEY)
    return Decimal(last) if last else None


# --------------------------------------------------------------------------- pages


def state() -> dict:
    """Where the sweep is: next page offset, market size, finished passes, last page time."""
    saved = cache.get(STATE_KEY) or {}
    return {
        "start": int(saved.get("start") or 0),
        "total": int(saved.get("total") or 0),
        "passes": int(saved.get("passes") or 0),
        "last_page_at": saved.get("last_page_at"),
        "last_page_time": parse_datetime(saved["last_page_at"]) if saved.get("last_page_at") else None,
        "rate": cache.get(RATE_LAST_KEY),
        "message": saved.get("message") or "",
    }


def _save_state(current: dict) -> None:
    cache.set(STATE_KEY, {key: current[key] for key in ("start", "total", "passes", "last_page_at", "message")}, timeout=None)


def fetch_page(start: int) -> dict:
    params = urlencode({
        "appid": 730,
        "currency": USD,
        "norender": 1,
        "count": PAGE_SIZE,
        "start": start,
        "search_descriptions": 0,
        "sort_column": "name",
        "sort_dir": "asc",
        "l": "english",
    })
    return _get_json(f"{SEARCH_URL}?{params}")


def _history_due(last_price, last_at, price: Decimal, now) -> bool:
    if last_price is None or last_at is None:
        return True
    if last_price == price:
        return False
    if now - last_at >= timedelta(hours=HISTORY_HOURS):
        return True
    return last_price > 0 and abs(price - last_price) / last_price >= HISTORY_JUMP


def apply_page(rows: list[dict], rate: Decimal, result: SweepResult) -> None:
    prices: dict[str, Decimal] = {}
    for row in rows:
        name = str(row.get("hash_name") or "").strip()
        try:
            cents = int(row.get("sell_price") or 0)
        except (TypeError, ValueError):
            cents = 0
        # No listings right now: nothing to price by; the per-item queue still asks for the median.
        if not (name and cents > 0 and int(row.get("sell_listings") or 0) > 0):
            continue
        # sell_price is in the answer's currency: anything but dollars would be converted at the wrong rate.
        if not str(row.get("sell_price_text") or "").startswith("$"):
            result.skipped += 1
            continue
        prices[name] = (Decimal(cents) / 100 * rate).quantize(Decimal("0.01"))
    result.seen += len(rows)
    if not prices:
        return

    now = timezone.now()
    latest = SteamItemPriceHistory.objects.filter(item_id=OuterRef("pk")).order_by("-created_at")
    items = list(
        SteamItem.objects.filter(is_active=True, market_hash_name__in=prices)
        .exclude(_hot_filter())
        .exclude(_warm_filter(now))
        .annotate(last_price=Subquery(latest.values("price_rub")[:1]), last_at=Subquery(latest.values("created_at")[:1]))
    )
    history = []
    for item in items:
        price = prices[item.market_hash_name]
        if item.price_rub != price:
            result.updated += 1
        if _history_due(item.last_price, item.last_at, price, now):
            history.append(SteamItemPriceHistory(item=item, price_rub=price, source=SWEEP_SOURCE))
        item.price_rub, item.price_checked_at, item.price_missing = price, now, False
    SteamItem.objects.bulk_update(items, ["price_rub", "price_checked_at", "price_missing"])
    SteamItemPriceHistory.objects.bulk_create(history)
    result.matched += len(items)
    result.history_written += len(history)


def sweep(*, pages: int | None = None, deadline=None) -> SweepResult:
    """Reads the next few pages of the market and stores the prices; stops early on a Steam pause or the deadline."""
    result = SweepResult()
    if not _setting("STEAM_PRICE_SWEEP_ENABLED", True):
        return result
    current = state()
    rate = usd_rub_rate(deadline=deadline, result=result)
    if rate is None:
        result.notes.append("Курс доллара пока неизвестен: массовый проход ждёт, пока Steam ответит.")
        current["message"] = (result.errors + result.notes)[0]
        _save_state(current)
        return result

    for _ in range(pages or int(_setting("STEAM_PRICE_SWEEP_PAGES", 3))):
        if _acquire_route(deadline, only=DIRECT) is None:
            break
        try:
            payload = fetch_page(current["start"])
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                result.notes.append(note_rate_limited(DIRECT))
            else:
                result.errors.append(f"Поиск по рынку: HTTP {exc.code}")
            break
        except Exception as exc:  # noqa: BLE001 - the next run tries the same page again
            result.errors.append(f"Поиск по рынку: {exc}")
            break
        if not payload.get("success"):
            result.errors.append("Поиск по рынку: Steam вернул ошибку")
            break
        rows = payload.get("results") or []
        try:
            total = int(payload.get("total_count") or 0)
        except (TypeError, ValueError):
            total = 0
        if not rows and current["start"] < total:
            # Steam sometimes answers a busy client with an empty page: try the same page next time.
            result.notes.append("Steam вернул пустую страницу поиска, повторим в следующий раз.")
            break
        apply_page(rows, rate, result)
        result.pages += 1
        # An anonymous client may get fewer rows than it asked for: move on by what Steam actually returned.
        current["start"] += len(rows)
        current["total"] = total
        current["last_page_at"] = timezone.now().isoformat()
        if not rows or current["start"] >= total:
            current["start"] = 0
            current["passes"] += 1
        _save_state(current)
    # The run log of the sweep is this one line on the auto-update page.
    current["message"] = (result.errors + result.notes + [""])[0]
    _save_state(current)
    return result
