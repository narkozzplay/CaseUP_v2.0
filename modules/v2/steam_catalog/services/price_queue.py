"""Smart price queue: keeps a catalog of tens of thousands of items fresh without getting banned by Steam.

Steam's priceoverview answers about one request every few seconds per IP address, so a whole-catalog pass
is impossible on every run. Each auto-update run instead takes a small batch of the stalest items:

1. «hot» items first — those somebody actually uses (stock, portfolios, farm drops, trade sales, price
   alerts) — refreshed every ``STEAM_PRICE_HOT_MINUTES``;
2. then the rest of the catalog in turn, each at most once per ``STEAM_PRICE_COLD_HOURS``.

Requests go out through «routes»: the server itself and any relays listed in ``STEAM_PRICE_RELAY_URLS``
(a small PHP script on other hosting, i.e. another IP). Every route has its own request slot (a cache
key shared by all workers), so each IP sends at most one request per ``STEAM_PRICE_REQUEST_INTERVAL``
seconds, and its own pause after an HTTP 429 from Steam (``STEAM_PRICE_BACKOFF_MINUTES``).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem, SteamItemPriceHistory, UserPriceAlertRule

DIRECT = "direct"
RELAY_ERROR_PAUSE_MINUTES = 30


@dataclass
class QueueBatchResult:
    checked: int = 0
    updated: int = 0
    history_written: int = 0
    changed_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rate_limited: bool = False


def _setting(name: str, default):
    return getattr(settings, name, default)


# --------------------------------------------------------------------------- routes


def routes() -> list[str]:
    relays = [url.strip() for url in _setting("STEAM_PRICE_RELAY_URLS", []) or [] if url and url.strip()]
    return [DIRECT, *relays]


def _slot_key(route: str) -> str:
    return f"steam_price_queue:slot:{route}"


def _backoff_key(route: str) -> str:
    return f"steam_price_queue:backoff:{route}"


def route_paused_until(route: str):
    until = cache.get(_backoff_key(route))
    return until if until and until > timezone.now() else None


def _pause_route(route: str, minutes: int) -> None:
    cache.set(_backoff_key(route), timezone.now() + timedelta(minutes=minutes), timeout=minutes * 60)


def backoff_until():
    """When every route is paused: the moment the first one comes back; otherwise None."""
    pauses = [route_paused_until(route) for route in routes()]
    if any(pause is None for pause in pauses):
        return None
    return min(pauses)


def _acquire_route() -> str | None:
    """Wait for a free request slot on any route that is not paused; None when all are paused."""
    interval = float(_setting("STEAM_PRICE_REQUEST_INTERVAL", 3.0))
    while True:
        available = [route for route in routes() if route_paused_until(route) is None]
        if not available:
            return None
        if interval <= 0:
            return available[0]
        for route in available:
            # cache.add is atomic in Redis: one process gets the slot per interval and IP.
            if cache.add(_slot_key(route), 1, timeout=max(1, int(round(interval)))):
                return route
        time.sleep(0.3)


def _fetch_via_relay(relay_url: str, market_hash_name: str, timeout: float = 15.0):
    from .fetch import _parse_price

    request = urllib.request.Request(
        f"{relay_url}?{urlencode({'market_hash_name': market_hash_name})}",
        headers={"X-Relay-Token": _setting("STEAM_PRICE_RELAY_TOKEN", ""), "User-Agent": "CaseUP/price-queue"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="ignore") or "{}")
    return _parse_price(payload.get("median_price")) or _parse_price(payload.get("lowest_price"))


def fetch_price(route: str, market_hash_name: str):
    from . import fetch_price_rub_from_steam

    if route == DIRECT:
        return fetch_price_rub_from_steam(market_hash_name=market_hash_name)
    return _fetch_via_relay(route, market_hash_name)


def route_label(route: str) -> str:
    return "сервер" if route == DIRECT else "хостинг"


# --------------------------------------------------------------------------- queue


def _hot_filter() -> Q:
    from modules.v2.farm.models import FarmDropItem
    from modules.v2.steam_trading.models import SteamPurchase
    from modules.v2.tradehub.models import SaleOrderItem

    hot = Q(stock__gt=0)
    hot |= Q(Exists(SteamPurchase.objects.filter(item_id=OuterRef("pk"))))
    hot |= Q(Exists(FarmDropItem.objects.filter(steam_item_id=OuterRef("pk"))))
    hot |= Q(Exists(SaleOrderItem.objects.filter(steam_item_id=OuterRef("pk"))))
    hot |= Q(Exists(UserPriceAlertRule.objects.filter(item_id=OuterRef("pk"))))
    return hot


def pick_batch(*, category: str, limit: int, now=None) -> list[SteamItem]:
    """The stalest hot items of the category first, then the stalest of the rest."""
    now = now or timezone.now()
    hot_cutoff = now - timedelta(minutes=int(_setting("STEAM_PRICE_HOT_MINUTES", 120)))
    cold_cutoff = now - timedelta(hours=int(_setting("STEAM_PRICE_COLD_HOURS", 24)))
    base = SteamItem.objects.filter(item_type=category, is_active=True)
    oldest_first = (F("price_checked_at").asc(nulls_first=True), "id")
    hot_filter = _hot_filter()

    picked = list(base.filter(hot_filter).filter(Q(price_checked_at__isnull=True) | Q(price_checked_at__lt=hot_cutoff)).order_by(*oldest_first)[:limit])
    if len(picked) < limit:
        picked += list(
            base.exclude(pk__in=[item.pk for item in picked])
            .exclude(hot_filter)
            .filter(Q(price_checked_at__isnull=True) | Q(price_checked_at__lt=cold_cutoff))
            .order_by(*oldest_first)[: limit - len(picked)]
        )
    return picked


def batch_limit(interval_minutes: int) -> int:
    """How many items fit into one run: ~80% of the run interval at the combined pace of all routes, capped."""
    pace = max(float(_setting("STEAM_PRICE_REQUEST_INTERVAL", 3.0)), 0.5) / len(routes())
    fits = int(interval_minutes * 60 * 0.8 / pace)
    return max(1, min(fits, int(_setting("STEAM_PRICE_BATCH_MAX", 60)) * len(routes())))


def refresh_batch(*, category: str, limit: int, source: str = "queue") -> QueueBatchResult:
    result = QueueBatchResult()
    until = backoff_until()
    if until:
        result.rate_limited = True
        result.errors.append(f"Пауза: Steam ограничил запросы, продолжим после {timezone.localtime(until):%H:%M}.")
        return result

    for item in pick_batch(category=category, limit=limit):
        route = _acquire_route()
        if route is None:
            result.rate_limited = True
            break
        try:
            price = fetch_price(route, item.market_hash_name)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                minutes = int(_setting("STEAM_PRICE_BACKOFF_MINUTES", 15))
                _pause_route(route, minutes)
                result.errors.append(f"Steam ограничил запросы ({route_label(route)}, 429), пауза {minutes} мин.")
                continue  # the item stays unchecked and goes out through another route or the next run
            if route != DIRECT:
                _pause_route(route, RELAY_ERROR_PAUSE_MINUTES)
                result.errors.append(f"Хостинг-ретранслятор ответил HTTP {exc.code}, пауза {RELAY_ERROR_PAUSE_MINUTES} мин. Проверьте скрипт и ключ.")
                continue
            price = None
            result.errors.append(f"{item.market_hash_name}: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001 - network hiccups must not stop the queue
            if route != DIRECT:
                _pause_route(route, RELAY_ERROR_PAUSE_MINUTES)
                result.errors.append(f"Хостинг-ретранслятор недоступен ({exc}), пауза {RELAY_ERROR_PAUSE_MINUTES} мин.")
                continue
            price = None
            result.errors.append(f"{item.market_hash_name}: {exc}")

        fields = {"price_checked_at": timezone.now()}
        if price is not None:
            result.checked += 1
            if item.price_rub != price:
                fields["price_rub"] = price
                result.updated += 1
            last = SteamItemPriceHistory.objects.filter(item=item).order_by("-created_at").values_list("price_rub", flat=True).first()
            if last != price:
                SteamItemPriceHistory.objects.create(item=item, price_rub=price, source=source)
                result.history_written += 1
                result.changed_ids.append(item.pk)
        # Items without a market price are marked checked too, so the queue moves on.
        SteamItem.objects.filter(pk=item.pk).update(**fields)
    return result


def coverage(*, now=None) -> dict:
    """For the auto-update page: how much of the catalog has a fresh price and how the routes are doing."""
    now = now or timezone.now()
    day_ago = now - timedelta(hours=int(_setting("STEAM_PRICE_COLD_HOURS", 24)))
    active = SteamItem.objects.filter(is_active=True)
    total = active.count()
    fresh = active.filter(price_checked_at__gte=day_ago).count()
    return {
        "total": total,
        "fresh": fresh,
        "percent": int(fresh * 100 / total) if total else 0,
        "never": active.filter(price_checked_at__isnull=True).count(),
        "hot": active.filter(_hot_filter()).count(),
        "routes": [{"label": route_label(route), "paused_until": route_paused_until(route)} for route in routes()],
    }
