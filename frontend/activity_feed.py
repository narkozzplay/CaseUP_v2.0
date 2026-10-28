"""Live feed of real site activity for the user dashboard: market buys and listings, rare case drops.

Every row is a real event from the database, nothing is generated.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from django.db import DatabaseError
from django.urls import reverse

from modules.v2.profiles.models import UserRole
from modules.v2.profiles.presentation import get_profile_avatar_url, get_profile_display_name

FEED_LIMIT = 12
RARE_DROP_RARITIES = ("classified", "covert", "gold")
PUBLIC_PROFILE_ROLES = (UserRole.USER, UserRole.FARMER)


@dataclass(frozen=True)
class FeedRow:
    kind: str  # "buy" | "list" | "drop"
    user_name: str
    user_avatar: str
    user_id: int
    item_name: str
    item_icon: str
    rarity: str
    price: Decimal | None
    created_at: datetime
    url: str = ""
    user_role: str = ""
    profile_url: str = ""


def _who(user) -> tuple[str, str]:
    profile = getattr(user, "profile_v2", None)
    return get_profile_display_name(profile, fallback_username=user.username), get_profile_avatar_url(profile)


def _role(user) -> str:
    return str(getattr(getattr(user, "profile_v2", None), "role", "") or "")


def _market_rows(limit: int) -> list[FeedRow]:
    from modules.v2.market.models import Deal, Listing

    rows = []
    deals = (
        Deal.objects.exclude(status=Deal.Status.CANCELLED)
        .select_related("listing", "buyer", "buyer__profile_v2")
        .order_by("-created_at")[:limit]
    )
    for deal in deals:
        name, avatar = _who(deal.buyer)
        rows.append(FeedRow("buy", name, avatar, deal.buyer_id, deal.listing.market_hash_name, deal.listing.icon,
                            deal.listing.rarity_color, deal.price, deal.created_at, user_role=_role(deal.buyer)))
    listings = (
        Listing.objects.filter(status=Listing.Status.ACTIVE)
        .select_related("seller", "seller__profile_v2")
        .order_by("-created_at")[:limit]
    )
    for lot in listings:
        name, avatar = _who(lot.seller)
        rows.append(FeedRow("list", name, avatar, lot.seller_id, lot.market_hash_name, lot.icon, lot.rarity_color,
                            lot.price, lot.created_at, url=reverse("market:lot", args=[lot.pk]), user_role=_role(lot.seller)))
    return rows


def _drop_rows(limit: int) -> list[FeedRow]:
    from modules.v2.case_simulator.models import SimOpening

    rows = []
    openings = (
        SimOpening.objects.filter(source=SimOpening.Source.CASE, item__rarity__in=RARE_DROP_RARITIES)
        .select_related("item", "case", "user", "user__profile_v2")
        .order_by("-created_at")[:limit]
    )
    for opening in openings:
        name, avatar = _who(opening.user)
        rows.append(FeedRow("drop", name, avatar, opening.user_id, opening.display_name, opening.item.image_url,
                            opening.item.rarity, None, opening.created_at, user_role=_role(opening.user)))
    return rows


def build_activity_feed(feature_flags: dict | None = None, limit: int = FEED_LIMIT, *, viewer_is_team: bool = False) -> list[FeedRow]:
    """Team profiles (moderators and up) link only for viewers who are team themselves."""
    flags = feature_flags or {}
    rows: list[FeedRow] = []
    try:
        if flags.get("market", True):
            rows += _market_rows(limit)
        if flags.get("case_simulator", True):
            rows += _drop_rows(limit)
    except DatabaseError:
        return []
    rows.sort(key=lambda row: row.created_at, reverse=True)
    return [
        replace(row, profile_url=reverse("profiles:profile_detail", args=[row.user_id]))
        if viewer_is_team or row.user_role in PUBLIC_PROFILE_ROLES
        else row
        for row in rows[:limit]
    ]
