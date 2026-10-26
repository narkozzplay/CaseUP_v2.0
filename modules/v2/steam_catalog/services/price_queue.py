"""Smart price queue: keeps a catalog of tens of thousands of items fresh without getting banned by Steam.

Steam's priceoverview answers about one request every few seconds per IP address, so a whole-catalog pass
is impossible on every run. Each auto-update run instead takes a small batch of the stalest items:

1. «hot» items first — those somebody actually uses (stock, portfolios, farm drops, trade sales, price
   alerts, favorites) — refreshed every ``STEAM_PRICE_HOT_MINUTES``;
2. then «warm» ones — pages people opened lately or prices that moved lately — every ``STEAM_PRICE_WARM_HOURS``;
3. then the rest of the catalog in turn, each at most once per ``STEAM_PRICE_COLD_HOURS``. Items Steam has no
   price for are asked again only after ``STEAM_PRICE_EMPTY_DAYS``.

Requests go out through «routes»: the server itself and any relays listed in ``STEAM_PRICE_RELAY_URLS``
(a small PHP script on other hosting, i.e. another IP). Every route has its own request slot (a cache
key shared by all workers), so each IP sends at most one request per ``STEAM_PRICE_REQUEST_INTERVAL``
seconds, at most ``STEAM_PRICE_HOURLY_BUDGET`` requests per clock hour, and its own pause after an HTTP 429
from Steam (``STEAM_PRICE_BACKOFF_MINUTES``).

A run is bounded by a ``deadline`` (``time.monotonic()``): the Celery task has a soft time limit, and a batch
that does not fit is simply finished by the next run instead of being killed mid-request.
"""
from __future__ import annotations

import json
import logging
import re
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

from modules.v2.steam_catalog.models import SteamItem, SteamItemFavorite, SteamItemPriceHistory, UserPriceAlertRule

logger = logging.getLogger(__name__)

DIRECT = "direct"
RELAY_ERROR_PAUSE_MINUTES = 30
# Kept free before the deadline: one Steam request (timeout 15 s) plus saving the result.
DEADLINE_RESERVE_SECONDS = 20
VIEW_THROTTLE_SECONDS = 3600
ROBOT_AGENT = re.compile(r"bot|crawl|spider|slurp|preview|curl|wget|python-requests|httpclient|headless", re.IGNORECASE)


@dataclass
class QueueBatchResult:
    checked: int = 0
    updated: int = 0
    history_written: int = 0
    changed_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rate_limited: bool = False
    out_of_budget: bool = False
    out_of_time: bool = False


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


def _spent_key(route: str, now=None) -> str:
    return f"steam_price_queue:spent:{route}:{(now or timezone.now()):%Y%m%d%H}"


def hourly_budget() -> int:
    return max(int(_setting("STEAM_PRICE_HOURLY_BUDGET", 900) or 0), 0)


def spent_this_hour(route: str) -> int:
    return int(cache.get(_spent_key(route)) or 0)


def _has_budget(route: str) -> bool:
    budget = hourly_budget()
    return not budget or spent_this_hour(route) < budget


def _reserve(route: str) -> bool:
    """Atomically takes one request of the route's hourly budget; False when the budget is already used up."""
    key = _spent_key(route)
    cache.add(key, 0, timeout=2 * 60 * 60)
    try:
        spent = cache.incr(key)
    except ValueError:  # the key expired between add and incr
        cache.set(key, 1, timeout=2 * 60 * 60)
        spent = 1
    budget = hourly_budget()
    return not budget or spent <= budget


def budget_exhausted() -> bool:
    """Every route that is not paused has used up its requests for this hour."""
    active = [route for route in routes() if route_paused_until(route) is None]
    return bool(active) and not any(_has_budget(route) for route in active)


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


def _out_of_time(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline - DEADLINE_RESERVE_SECONDS


def _acquire_route(deadline: float | None = None) -> str | None:
    """Wait for a free request slot on any route that is not paused and has requests left this hour; None when
    there is none or the deadline is too close for one more request. The returned route's request is counted."""
    interval = float(_setting("STEAM_PRICE_REQUEST_INTERVAL", 3.0))
    while not _out_of_time(deadline):
        available = [route for route in routes() if route_paused_until(route) is None and _has_budget(route)]
        if not available:
            return None
        for route in available:
            # cache.add is atomic in Redis: one process gets the slot per interval and IP.
            if interval <= 0 or cache.add(_slot_key(route), 1, timeout=max(1, int(round(interval)))):
                # Concurrent workers may both have seen budget left: the atomic counter decides.
                if _reserve(route):
                    return route
        time.sleep(0.3)
    return None


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
    # A favorite stars the whole family card, so every wear of it is hot.
    hot |= Q(Exists(SteamItemFavorite.objects.filter(family_key=OuterRef("family_name"))))
    hot |= Q(Exists(SteamItemFavorite.objects.filter(item_id=OuterRef("pk"))))
    return hot


def _warm_filter(now) -> Q:
    viewed_since = now - timedelta(days=int(_setting("STEAM_PRICE_WARM_VIEW_DAYS", 7)))
    moved_since = now - timedelta(hours=int(_setting("STEAM_PRICE_WARM_MOVED_HOURS", 48)))
    # Prices that moved lately keep moving: these are the candidates for the channel digest and price alerts.
    moved = SteamItemPriceHistory.objects.filter(item_id=OuterRef("pk"), created_at__gte=moved_since)
    return Q(last_viewed_at__gte=viewed_since) | Q(Exists(moved))


def _stale(cutoff, empty_cutoff) -> Q:
    """Never checked, or checked before ``cutoff``; items Steam had no price for only before ``empty_cutoff``."""
    listed = Q(price_missing=False, price_checked_at__lt=cutoff)
    missing = Q(price_missing=True, price_checked_at__lt=empty_cutoff)
    return Q(price_checked_at__isnull=True) | listed | missing


def pick_batch(*, category: str, limit: int, now=None) -> list[SteamItem]:
    """The stalest hot items of the category first, then the stalest warm ones, then the stalest of the rest."""
    now = now or timezone.now()
    hot_cutoff = now - timedelta(minutes=int(_setting("STEAM_PRICE_HOT_MINUTES", 120)))
    warm_cutoff = now - timedelta(hours=int(_setting("STEAM_PRICE_WARM_HOURS", 6)))
    cold_cutoff = now - timedelta(hours=int(_setting("STEAM_PRICE_COLD_HOURS", 24)))
    empty_cutoff = now - timedelta(days=int(_setting("STEAM_PRICE_EMPTY_DAYS", 7)))
    base = SteamItem.objects.filter(item_type=category, is_active=True)
    oldest_first = (F("price_checked_at").asc(nulls_first=True), "id")
    hot_filter = _hot_filter()
    warm_filter = _warm_filter(now)

    tiers = (
        base.filter(hot_filter).filter(_stale(hot_cutoff, empty_cutoff)),
        base.exclude(hot_filter).filter(warm_filter).filter(_stale(warm_cutoff, empty_cutoff)),
        base.exclude(hot_filter).exclude(warm_filter).filter(_stale(cold_cutoff, empty_cutoff)),
    )
    picked: list[SteamItem] = []
    for tier in tiers:
        if len(picked) >= limit:
            break
        picked += list(tier.exclude(pk__in=[item.pk for item in picked]).order_by(*oldest_first)[: limit - len(picked)])
    return picked


def note_item_view(request, item: SteamItem) -> None:
    """Marks the item page as viewed by a person (at most once an hour per item), which makes the item «warm»."""
    agent = request.META.get("HTTP_USER_AGENT", "")
    if not agent or ROBOT_AGENT.search(agent):
        return
    try:
        first_this_hour = cache.add(f"steam_price_queue:viewed:{item.pk}", 1, timeout=VIEW_THROTTLE_SECONDS)
    except Exception:  # noqa: BLE001 - tracking is optional, the page must still open without the cache
        logger.warning("Item view tracking skipped: cache unavailable", exc_info=True)
        return
    if first_this_hour:
        SteamItem.objects.filter(pk=item.pk).update(last_viewed_at=timezone.now())


def batch_limit(interval_minutes: int, budget_seconds: float | None = None) -> int:
    """How many items fit into one run: ~80% of the run interval (or of ``budget_seconds``, the time the task
    has left, when that is shorter) at the combined pace of all routes, capped."""
    pace = max(float(_setting("STEAM_PRICE_REQUEST_INTERVAL", 3.0)), 0.5) / len(routes())
    seconds = interval_minutes * 60
    if budget_seconds is not None:
        seconds = min(seconds, max(budget_seconds - DEADLINE_RESERVE_SECONDS, 0))
    fits = int(seconds * 0.8 / pace)
    return max(1, min(fits, int(_setting("STEAM_PRICE_BATCH_MAX", 60)) * len(routes())))


def refresh_batch(*, category: str, limit: int, source: str = "queue", deadline: float | None = None) -> QueueBatchResult:
    result = QueueBatchResult()
    until = backoff_until()
    if until:
        result.rate_limited = True
        result.errors.append(f"Пауза: Steam ограничил запросы, продолжим после {timezone.localtime(until):%H:%M}.")
        return result
    if budget_exhausted():
        result.out_of_budget = True
        return result

    for item in pick_batch(category=category, limit=limit):
        route = _acquire_route(deadline)
        if route is None:
            # The rest of the batch stays stale and is picked first by the next run.
            if _out_of_time(deadline):
                result.out_of_time = True
            elif budget_exhausted():
                result.out_of_budget = True
            else:
                result.rate_limited = True
            break
        try:
            price = fetch_price(route, item.market_hash_name)
            answered = True
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
            price, answered = None, False
            result.errors.append(f"{item.market_hash_name}: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001 - network hiccups must not stop the queue
            if route != DIRECT:
                _pause_route(route, RELAY_ERROR_PAUSE_MINUTES)
                result.errors.append(f"Хостинг-ретранслятор недоступен ({exc}), пауза {RELAY_ERROR_PAUSE_MINUTES} мин.")
                continue
            price, answered = None, False
            result.errors.append(f"{item.market_hash_name}: {exc}")

        fields = {"price_checked_at": timezone.now()}
        # Only a real answer without a price puts the item on the weekly cadence; a failed request keeps it on the
        # normal one, even when Steam had no price for it last time.
        fields["price_missing"] = answered and price is None
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
    """For the auto-update page: how much of the catalog has a fresh price, per tier, and how the routes are doing."""
    now = now or timezone.now()
    day_ago = now - timedelta(hours=int(_setting("STEAM_PRICE_COLD_HOURS", 24)))
    active = SteamItem.objects.filter(is_active=True)
    total = active.count()
    fresh = active.filter(price_checked_at__gte=day_ago).count()
    hot_filter, warm_filter = _hot_filter(), _warm_filter(now)

    def tier(queryset, hours: float) -> dict:
        count = queryset.count()
        fresh_count = queryset.filter(price_checked_at__gte=now - timedelta(hours=hours)).count()
        return {"total": count, "fresh": fresh_count, "percent": int(fresh_count * 100 / count) if count else 100}

    hot_minutes = int(_setting("STEAM_PRICE_HOT_MINUTES", 120))
    warm_hours = int(_setting("STEAM_PRICE_WARM_HOURS", 6))
    budget = hourly_budget()
    return {
        "total": total,
        "fresh": fresh,
        "percent": int(fresh * 100 / total) if total else 0,
        "never": active.filter(price_checked_at__isnull=True).count(),
        "hot": active.filter(hot_filter).count(),
        "hot_tier": tier(active.filter(hot_filter), hot_minutes / 60),
        "warm_tier": tier(active.exclude(hot_filter).filter(warm_filter), warm_hours),
        "hot_minutes": hot_minutes,
        "warm_hours": warm_hours,
        "cold_hours": int(_setting("STEAM_PRICE_COLD_HOURS", 24)),
        "budget": budget,
        "routes": [
            {"label": route_label(route), "paused_until": route_paused_until(route), "spent": spent_this_hour(route)}
            for route in routes()
        ],
    }
