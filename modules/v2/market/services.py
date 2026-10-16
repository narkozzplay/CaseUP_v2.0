"""Market, stage 1: sellers, their Steam inventory, lots and the showcase.

Every action is written to MarketEvent (and the site-wide activity log), so any lot's history can be
traced later: who listed it, every price change, who removed it and why.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from .models import Listing, MarketEvent, MarketSeller

logger = logging.getLogger(__name__)

STEAM_ID64_BASE = 76561197960265728
TRADE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,16}$")
INVENTORY_CACHE_SECONDS = 5 * 60
MAX_PRICE = Decimal("10000000.00")


class MarketError(Exception):
    pass


def _setting(name: str, default):
    return getattr(settings, name, default)


# --------------------------------------------------------------------------- journal


_SYSTEM = object()


def log_event(kind: str, *, user=None, actor=_SYSTEM, listing=None, deal=None, message: str = "", payload: dict | None = None, ip: str | None = None) -> MarketEvent:
    """Journal one market action. ``actor`` defaults to ``user``; pass ``actor=None`` for the site itself."""
    from modules.v2.control_center.models import ActivityCategory
    from modules.v2.control_center.services.activity import log_activity

    actor = user if actor is _SYSTEM else actor
    if deal is not None and listing is None:
        listing = deal.listing
    event = MarketEvent.objects.create(
        kind=kind, user=user, actor=actor, listing=listing, deal=deal, message=message[:255], payload=payload or {}, ip_address=ip or None
    )
    log_activity(
        category=ActivityCategory.TRADING,
        event=f"market.{kind}",
        summary=message[:255],
        user=user,
        actor=actor,
        amount=(payload or {}).get("price"),
        obj=deal or listing,
        payload=payload or {},
    )
    return event


# --------------------------------------------------------------------------- sellers


@dataclass(frozen=True)
class Requirement:
    key: str
    label: str
    ok: bool
    hint: str = ""
    url: str = ""


def real_topups_total(user) -> Decimal:
    from modules.v2.payments.models import Payment

    return Payment.objects.filter(user=user, status=Payment.Status.SUCCEEDED, is_test=False).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def seller_requirements(user) -> list[Requirement]:
    from modules.v2.control_center.models import UserBan
    from modules.v2.profiles.models import UserProfile, VerificationLevel

    profile = UserProfile.objects.filter(user=user).first()
    seller = MarketSeller.objects.filter(user=user).first()
    min_days = int(_setting("MARKET_SELLER_MIN_ACCOUNT_DAYS", 30))
    min_topup = Decimal(str(_setting("MARKET_SELLER_MIN_TOPUP", 500)))
    age_days = (timezone.now() - user.date_joined).days
    topups = real_topups_total(user)
    return [
        Requirement("verified", "Аккаунт верифицирован", bool(profile and profile.verification_level != VerificationLevel.NONE), "Подайте заявку на верификацию в профиле.", reverse("profiles:profile_settings")),
        Requirement("age", f"На сайте не меньше {min_days} дней", age_days >= min_days, f"Сейчас: {age_days} дн. Осталось {max(0, min_days - age_days)} дн."),
        Requirement("topup", f"Пополнения на сумму от {min_topup:.0f} ₽", topups >= min_topup, f"Сейчас: {topups:.2f} ₽.", reverse("payments:topup")),
        Requirement("steam", "Привязан Steam", bool(profile and (profile.steam_id or "").strip()), "Подключите Steam в профиле.", reverse("profiles:profile")),
        Requirement("trade_url", "Сохранена трейд-ссылка Steam", bool(seller and seller.trade_url), "Вставьте трейд-ссылку ниже."),
        Requirement("not_banned", "Аккаунт не заблокирован", not UserBan.objects.active().filter(user=user).exists()),
    ]


def can_sell(user) -> bool:
    return all(requirement.ok for requirement in seller_requirements(user))


def _steam_id64(user) -> str:
    from modules.v2.profiles.models import UserProfile

    profile = UserProfile.objects.filter(user=user).only("steam_id").first()
    return (profile.steam_id or "").strip() if profile else ""


def normalize_trade_url(raw: str, *, steam_id64: str) -> str:
    """Validate a Steam trade link and check that it belongs to the user's own Steam account."""
    value = (raw or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in ("steamcommunity.com", "www.steamcommunity.com") or parsed.path.rstrip("/") != "/tradeoffer/new":
        raise MarketError("Это не трейд-ссылка Steam. Скопируйте её в Steam: Инвентарь → Предложения обмена → «Кто может отправлять мне предложения обмена?».")
    query = parse_qs(parsed.query)
    partner = (query.get("partner") or [""])[0]
    token = (query.get("token") or [""])[0]
    if not partner.isdigit() or not TRADE_TOKEN_RE.match(token):
        raise MarketError("В трейд-ссылке нет номера партнёра или токена — скопируйте ссылку целиком.")
    if not steam_id64.isdigit():
        raise MarketError("Сначала привяжите Steam к аккаунту.")
    if int(partner) != int(steam_id64) - STEAM_ID64_BASE:
        raise MarketError("Трейд-ссылка принадлежит другому Steam-аккаунту, а не привязанному к CaseUP.")
    return f"https://steamcommunity.com/tradeoffer/new/?partner={partner}&token={token}"


def save_trade_url(user, raw: str, *, ip: str | None = None) -> MarketSeller:
    trade_url = normalize_trade_url(raw, steam_id64=_steam_id64(user))
    seller, _ = MarketSeller.objects.get_or_create(user=user)
    if seller.trade_url != trade_url:
        seller.trade_url, seller.trade_url_updated_at = trade_url, timezone.now()
        seller.save(update_fields=["trade_url", "trade_url_updated_at"])
        log_event(MarketEvent.Kind.TRADE_LINK, user=user, message="Трейд-ссылка сохранена", ip=ip)
    return seller


# --------------------------------------------------------------------------- inventory


def _inventory_cache_key(user) -> str:
    return f"market:inventory:{user.pk}"


def fetch_inventory(user, *, force: bool = False) -> list[dict]:
    """Tradable CS2 items from the seller's public Steam inventory (cached for a few minutes)."""
    from modules.v2.farm.services import fetch_public_inventory

    key = _inventory_cache_key(user)
    if not force:
        cached = cache.get(key)
        if cached is not None:
            return cached
    steam_id64 = _steam_id64(user)
    if not steam_id64:
        raise MarketError("Сначала привяжите Steam к аккаунту.")
    seller, _ = MarketSeller.objects.get_or_create(user=user)
    try:
        raw_items = fetch_public_inventory(steam_id64)
    except Exception as exc:  # noqa: BLE001 - shown to the seller as is
        seller.inventory_error = str(exc)[:255]
        seller.save(update_fields=["inventory_error"])
        raise MarketError(f"Не удалось прочитать инвентарь Steam: {exc}. Инвентарь должен быть открытым.") from exc
    items = [item for item in raw_items if item.get("asset_id") and item.get("market_hash_name") and item.get("tradable") and item.get("marketable")]
    seller.inventory_checked_at, seller.inventory_error = timezone.now(), ""
    seller.save(update_fields=["inventory_checked_at", "inventory_error"])
    cache.set(key, items, INVENTORY_CACHE_SECONDS)
    return items


def inventory_for_sale(user, *, force: bool = False) -> list[dict]:
    """The inventory with the Steam reference price and whether the item is already on the market."""
    from modules.v2.steam_catalog.models import SteamItem

    items = fetch_inventory(user, force=force)
    listed = set(Listing.objects.filter(seller=user, status__in=Listing.OPEN_STATUSES).values_list("asset_id", flat=True))
    prices = dict(SteamItem.objects.filter(market_hash_name__in={item["market_hash_name"] for item in items}).values_list("market_hash_name", "price_rub"))
    rows = []
    for item in items:
        icon = item.get("icon_url") or ""
        rows.append({
            **item,
            "icon": icon if icon.startswith("http") else (f"https://community.cloudflare.steamstatic.com/economy/image/{icon}/256x256" if icon else ""),
            "steam_price": prices.get(item["market_hash_name"]),
            "is_listed": item["asset_id"] in listed,
        })
    rows.sort(key=lambda row: (row["is_listed"], -(row["steam_price"] or Decimal("0"))))
    return rows


# --------------------------------------------------------------------------- lots


def parse_price(raw) -> Decimal:
    try:
        price = Decimal(str(raw or "").replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise MarketError("Цена должна быть числом, например 149.90.") from exc
    if price <= 0:
        raise MarketError("Цена должна быть больше нуля.")
    if price > MAX_PRICE:
        raise MarketError("Слишком большая цена.")
    return price


def create_listing(user, *, asset_id: str, price, ip: str | None = None) -> Listing:
    from modules.v2.steam_catalog.models import SteamItem
    from modules.v2.steam_catalog.services.helpers import guess_item_type, parse_skin_market_hash_name

    if not can_sell(user):
        raise MarketError("Продавать могут только аккаунты, выполнившие все условия продавца.")
    price = parse_price(price)
    item = next((row for row in fetch_inventory(user) if row["asset_id"] == str(asset_id)), None)
    if item is None:  # the item may have arrived after the inventory was cached
        item = next((row for row in fetch_inventory(user, force=True) if row["asset_id"] == str(asset_id)), None)
    if item is None:
        raise MarketError("Этого предмета нет в вашем инвентаре Steam или его нельзя передать. Обновите инвентарь.")

    hash_name = item["market_hash_name"]
    is_stattrak, _base, exterior = parse_skin_market_hash_name(hash_name.replace("★ StatTrak™ ", "StatTrak™ ★ "))
    with transaction.atomic():
        if Listing.objects.select_for_update().filter(asset_id=item["asset_id"], status__in=Listing.OPEN_STATUSES).exists():
            raise MarketError("Этот предмет уже выставлен на рынок.")
        float_value = item.get("float_value")
        listing = Listing.objects.create(
            seller=user,
            asset_id=item["asset_id"],
            class_id=item.get("class_id", ""),
            instance_id=item.get("instance_id", ""),
            market_hash_name=hash_name,
            icon_url=item.get("icon_url", ""),
            rarity_color=item.get("rarity_color", ""),
            float_value=Decimal(str(float_value)).quantize(Decimal("0.00000001")) if float_value is not None else None,
            item_type=guess_item_type(hash_name),
            exterior=exterior,
            is_stattrak=is_stattrak,
            steam_item=SteamItem.objects.filter(market_hash_name=hash_name).first(),
            price=price,
            checked_at=timezone.now(),
        )
    log_event(MarketEvent.Kind.LISTED, user=user, listing=listing, message=f"{listing.code}: {hash_name} за {price:.2f} ₽", payload={"price": str(price), "asset_id": listing.asset_id}, ip=ip)
    from modules.v2.webpush.services import prompt_after_commit

    prompt_after_commit(user, "market_listed")
    return listing


def _own_active_listing(user, listing_id) -> Listing:
    listing = Listing.objects.select_for_update().filter(pk=listing_id, seller=user).first()
    if listing is None:
        raise MarketError("Лот не найден.")
    if listing.status != Listing.Status.ACTIVE:
        raise MarketError("Лот уже не на витрине — изменить его нельзя.")
    return listing


@transaction.atomic
def change_price(user, *, listing_id, price, ip: str | None = None) -> Listing:
    listing = _own_active_listing(user, listing_id)
    new_price = parse_price(price)
    old_price = listing.price
    if new_price == old_price:
        return listing
    listing.price = new_price
    listing.save(update_fields=["price", "updated_at"])
    log_event(MarketEvent.Kind.PRICE, user=user, listing=listing, message=f"{listing.code}: {old_price:.2f} → {new_price:.2f} ₽", payload={"price": str(new_price), "old_price": str(old_price)}, ip=ip)
    return listing


@transaction.atomic
def cancel_listing(user, *, listing_id, ip: str | None = None) -> Listing:
    listing = _own_active_listing(user, listing_id)
    listing.status, listing.closed_at = Listing.Status.CANCELLED, timezone.now()
    listing.save(update_fields=["status", "closed_at", "updated_at"])
    log_event(MarketEvent.Kind.CANCELLED, user=user, listing=listing, message=f"{listing.code} снят с витрины", ip=ip)
    return listing


# --------------------------------------------------------------------------- presence check


def verify_listings(*, pause_seconds: float | None = None) -> dict:
    """Take lots off the showcase when the item left the seller's inventory (sold elsewhere, traded away).

    An inventory that cannot be read right now removes nothing: only an item really missing from a
    successfully read inventory does.
    """
    from modules.v2.notifications.services import notify

    from .notifications import KIND_LISTING_MISSING

    pause = float(_setting("MARKET_INVENTORY_CHECK_PAUSE", 3.0)) if pause_seconds is None else pause_seconds
    report = {"sellers": 0, "removed": 0, "errors": 0}
    seller_ids = list(Listing.objects.filter(status=Listing.Status.ACTIVE).values_list("seller_id", flat=True).distinct())
    from django.contrib.auth import get_user_model

    for index, seller in enumerate(get_user_model().objects.filter(pk__in=seller_ids)):
        if index and pause:
            time.sleep(pause)
        report["sellers"] += 1
        try:
            present = {item["asset_id"] for item in fetch_inventory(seller, force=True)}
        except MarketError:
            report["errors"] += 1
            continue
        now = timezone.now()
        for listing in Listing.objects.filter(seller=seller, status=Listing.Status.ACTIVE):
            if listing.asset_id in present:
                Listing.objects.filter(pk=listing.pk).update(checked_at=now)
                continue
            with transaction.atomic():
                updated = Listing.objects.filter(pk=listing.pk, status=Listing.Status.ACTIVE).update(status=Listing.Status.MISSING, closed_at=now)
            if updated:
                report["removed"] += 1
                log_event(MarketEvent.Kind.MISSING, user=seller, actor=None, listing=listing, message=f"{listing.code} снят: предмета больше нет в инвентаре Steam")
                notify(seller, KIND_LISTING_MISSING, object_id=listing.pk, payload={"code": listing.code, "name": listing.market_hash_name})
    return report


# --------------------------------------------------------------------------- showcase

SORTS = {
    "new": ("-created_at", "-id"),
    "cheap": ("price", "-id"),
    "expensive": ("-price", "-id"),
}
SORT_LABELS = (("new", "Сначала новые"), ("cheap", "Сначала дешёвые"), ("expensive", "Сначала дорогие"))
TYPE_LABELS = (("", "Все"), ("skin", "Скины"), ("case", "Кейсы"), ("sticker", "Стикеры"), ("other", "Другое"))


def showcase(params) -> tuple:
    qs = Listing.objects.filter(status=Listing.Status.ACTIVE).select_related("seller", "seller__profile_v2", "steam_item")
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(market_hash_name__icontains=q)
    item_type = (params.get("type") or "").strip()
    if item_type == "other":
        qs = qs.exclude(item_type__in=["skin", "case", "sticker"])
    elif item_type:
        qs = qs.filter(item_type=item_type)
    exterior = (params.get("exterior") or "").strip()
    if exterior:
        qs = qs.filter(exterior=exterior)
    if params.get("stattrak") == "1":
        qs = qs.filter(is_stattrak=True)
    for field, lookup in (("min", "price__gte"), ("max", "price__lte")):
        raw = (params.get(field) or "").strip()
        if raw:
            try:
                qs = qs.filter(**{lookup: parse_price(raw)})
            except MarketError:
                pass
    sort = params.get("sort") if params.get("sort") in SORTS else "new"
    filters = {"q": q, "type": item_type, "exterior": exterior, "stattrak": params.get("stattrak") == "1", "min": params.get("min", ""), "max": params.get("max", ""), "sort": sort}
    return qs.order_by(*SORTS[sort]), filters


def listing_history(listing: Listing):
    return listing.events.select_related("actor").order_by("created_at", "id")


def seller_stats(user) -> dict:
    listings = Listing.objects.filter(seller=user)
    return {
        "active": listings.filter(status=Listing.Status.ACTIVE).count(),
        "sold": listings.filter(status=Listing.Status.SOLD).count(),
        "since": timezone.localdate(user.date_joined),
        "recent_events": MarketEvent.objects.filter(Q(user=user) | Q(listing__seller=user)).order_by("-created_at")[:20],
    }
