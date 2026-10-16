"""Steam catalog list: filters, sorting and favorites.

The catalog holds tens of thousands of items, one row per wear/StatTrak variant. The list shows one card per
family (all wears of a skin), so the grouping is done by the database: variant-level filters first, then
GROUP BY family with min/max price, and only the cards of the current page are loaded as objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db.models import Case, Count, F, IntegerField, Max, Min, Q, Sum, Value, When
from django.db.models.functions import Coalesce, NullIf

from modules.v2.steam_catalog.models import SteamItem, SteamItemFavorite

from .constants import EXTERIOR_LABELS, RARITY_RU_LABELS, WEAR_RU

FAMILY_KEY = Coalesce(NullIf(F("family_name"), Value("")), F("market_hash_name"))

SORT_LABELS = {
    "updated": "Недавно обновлённые",
    "price_asc": "Сначала дешёвые",
    "price_desc": "Сначала дорогие",
    "name": "По названию",
}

TYPE_LABELS = {
    "skin": "Скины",
    "case": "Кейсы",
    "sticker": "Наклейки",
    "graffiti": "Граффити",
    "charm": "Брелоки",
    "other": "Прочее",
}

RARITY_ORDER = ["consumer", "industrial", "milspec", "restricted", "classified", "covert", "extraordinary", "contraband"]

WEAPON_GROUPS = {
    "pistol": ("Пистолеты", ["Glock-18", "USP-S", "P2000", "P250", "Five-SeveN", "Tec-9", "CZ75-Auto", "Desert Eagle", "Dual Berettas", "R8 Revolver"]),
    "smg": ("Пистолеты-пулемёты", ["MAC-10", "MP9", "MP7", "MP5-SD", "UMP-45", "P90", "PP-Bizon"]),
    "rifle": ("Винтовки", ["AK-47", "M4A4", "M4A1-S", "Galil AR", "FAMAS", "SG 553", "AUG"]),
    "sniper": ("Снайперские", ["AWP", "SSG 08", "SCAR-20", "G3SG1"]),
    "heavy": ("Тяжёлое", ["Nova", "XM1014", "Sawed-Off", "MAG-7", "M249", "Negev"]),
    "knife": ("Ножи", []),
    "gloves": ("Перчатки", []),
}
_KNOWN_GUNS = {name for _label, names in WEAPON_GROUPS.values() for name in names}
_GLOVES_Q = Q(weapon__icontains="Gloves") | Q(weapon__icontains="Hand Wraps")


def _weapon_group_q(group: str) -> Q | None:
    if group == "knife":
        return Q(item_type=SteamItem.ItemType.SKIN, market_hash_name__contains="★") & ~_GLOVES_Q
    if group == "gloves":
        return Q(item_type=SteamItem.ItemType.SKIN) & _GLOVES_Q
    if group in WEAPON_GROUPS:
        return Q(weapon__in=WEAPON_GROUPS[group][1])
    return None


def _decimal(value) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number >= 0 else None


@dataclass
class CatalogFilters:
    q: str = ""
    type: str = ""
    group: str = ""
    weapon: str = ""
    rarity: str = ""
    exterior: str = ""
    stattrak: str = ""  # "1" only StatTrak, "0" without
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    priced: bool = False
    in_stock: bool = False
    review: str = ""
    favorites: bool = False
    sort: str = "updated"
    chips: list[dict] = field(default_factory=list)

    @property
    def is_filtered(self) -> bool:
        return bool(self.chips)


def parse_filters(params, *, can_edit: bool = False) -> CatalogFilters:
    get = lambda name: (params.get(name) or "").strip()  # noqa: E731
    filters = CatalogFilters(
        q=get("q")[:120],
        type=get("type") if get("type") in TYPE_LABELS else "",
        group=get("group") if get("group") in WEAPON_GROUPS else "",
        weapon=get("weapon")[:64],
        rarity=get("rarity") if get("rarity") in RARITY_RU_LABELS else "",
        exterior=get("exterior") if get("exterior") in EXTERIOR_LABELS else "",
        stattrak=get("stattrak") if get("stattrak") in ("0", "1") else "",
        price_min=_decimal(get("price_min")) if get("price_min") else None,
        price_max=_decimal(get("price_max")) if get("price_max") else None,
        priced=get("priced") == "1",
        in_stock=get("stock") == "1" and can_edit,
        review=get("review") if can_edit and get("review") in ("0", "1") else "",
        favorites=get("fav") == "1",
        sort=get("sort") if get("sort") in SORT_LABELS else "updated",
    )
    filters.chips = _chips(filters)
    return filters


def _chips(f: CatalogFilters) -> list[dict]:
    """Active filters as removable chips: label + the query parameter(s) that drop it."""
    chips = []
    if f.q:
        chips.append({"label": f"«{f.q}»", "drop": ["q"]})
    if f.type:
        chips.append({"label": TYPE_LABELS[f.type], "drop": ["type"]})
    if f.group:
        chips.append({"label": WEAPON_GROUPS[f.group][0], "drop": ["group"]})
    if f.weapon:
        chips.append({"label": f.weapon, "drop": ["weapon"]})
    if f.rarity:
        chips.append({"label": RARITY_RU_LABELS[f.rarity], "drop": ["rarity"]})
    if f.exterior:
        chips.append({"label": WEAR_RU.get(f.exterior, f.exterior), "drop": ["exterior"]})
    if f.stattrak:
        chips.append({"label": "StatTrak™" if f.stattrak == "1" else "Без StatTrak", "drop": ["stattrak"]})
    if f.price_min is not None or f.price_max is not None:
        low = f"от {f.price_min:g} ₽" if f.price_min is not None else ""
        high = f"до {f.price_max:g} ₽" if f.price_max is not None else ""
        chips.append({"label": " ".join(part for part in (low, high) if part), "drop": ["price_min", "price_max"]})
    if f.priced:
        chips.append({"label": "Только с ценой", "drop": ["priced"]})
    if f.in_stock:
        chips.append({"label": "В наличии", "drop": ["stock"]})
    if f.review:
        chips.append({"label": "Требует review" if f.review == "1" else "Без review", "drop": ["review"]})
    if f.favorites:
        chips.append({"label": "★ Избранное", "drop": ["fav"]})
    return chips


def favorite_keys(user) -> list[str]:
    if not user.is_authenticated:
        return []
    return list(SteamItemFavorite.objects.filter(user=user).values_list("family_key", flat=True))


def filter_variants(f: CatalogFilters, *, fav_keys: list[str]):
    """Variant-level filters: the cards then show the price range of the matching variants only."""
    qs = SteamItem.objects.annotate(fkey=FAMILY_KEY)
    for word in f.q.split():
        qs = qs.filter(
            Q(market_hash_name__icontains=word) | Q(name__icontains=word) | Q(name_ru__icontains=word) | Q(weapon__icontains=word) | Q(family_name__icontains=word)
        )
    if f.type:
        qs = qs.filter(item_type=f.type)
    if f.group:
        qs = qs.filter(_weapon_group_q(f.group))
    if f.weapon:
        qs = qs.filter(weapon=f.weapon)
    if f.rarity:
        qs = qs.filter(rarity_key=f.rarity)
    if f.exterior:
        qs = qs.filter(exterior=f.exterior)
    if f.stattrak:
        qs = qs.filter(is_stattrak=f.stattrak == "1")
    if f.price_min is not None:
        qs = qs.filter(price_rub__gte=f.price_min)
    if f.price_max is not None:
        qs = qs.filter(price_rub__lte=f.price_max)
    if f.priced:
        qs = qs.filter(price_rub__isnull=False)
    if f.in_stock:
        qs = qs.filter(stock__gt=0)
    if f.review:
        qs = qs.filter(needs_review=f.review == "1")
    if f.favorites:
        qs = qs.filter(fkey__in=fav_keys)
    return qs


def family_rows(f: CatalogFilters, *, fav_keys: list[str]):
    """One row per family, favorites first, then the chosen order."""
    rows = (
        filter_variants(f, fav_keys=fav_keys)
        .annotate(fav=Case(When(fkey__in=fav_keys, then=Value(1)), default=Value(0), output_field=IntegerField()))
        .values("fkey", "fav")
        .annotate(
            min_price=Min("price_rub"),
            max_price=Max("price_rub"),
            variants=Count("id"),
            total_stock=Sum("stock"),
            last_updated=Max("updated_at"),
        )
    )
    order = {
        "updated": [F("last_updated").desc()],
        "price_asc": [F("min_price").asc(nulls_last=True), "fkey"],
        "price_desc": [F("max_price").desc(nulls_last=True), "fkey"],
        "name": ["fkey"],
    }[f.sort]
    return rows.order_by("-fav", *order)


def build_cards(rows, f: CatalogFilters, *, fav_keys: list[str], auto_update_categories=()) -> list[dict]:
    """Cards for one page: the representative variant is the first matching one (no StatTrak, best wear)."""
    rows = list(rows)
    keys = [row["fkey"] for row in rows]
    representatives: dict[str, SteamItem] = {}
    for item in filter_variants(f, fav_keys=fav_keys).filter(fkey__in=keys).order_by("fkey", "is_stattrak", "wear_order", "-updated_at"):
        representatives.setdefault(item.fkey, item)
    auto_update_categories = set(auto_update_categories)
    fav_set = set(fav_keys)
    cards = []
    for row in rows:
        item = representatives.get(row["fkey"])
        if item is None:
            continue
        cards.append(
            {
                "family_name": row["fkey"],
                "item": item,
                "variants_count": row["variants"],
                "total_stock": row["total_stock"] or 0,
                "min_price": row["min_price"],
                "max_price": row["max_price"],
                "auto_update_enabled": item.item_type in auto_update_categories,
                "is_favorite": row["fkey"] in fav_set,
            }
        )
    return cards


def filter_options(f: CatalogFilters) -> dict:
    """Choices for the selects; weapons are limited to the chosen weapon group when there is one."""
    weapons_qs = SteamItem.objects.filter(item_type=SteamItem.ItemType.SKIN).exclude(weapon="")
    if f.group:
        weapons_qs = weapons_qs.filter(_weapon_group_q(f.group))
    present = set(weapons_qs.values_list("weapon", flat=True).distinct())
    weapon_groups = []
    for key, (label, names) in WEAPON_GROUPS.items():
        if f.group and key != f.group:
            continue
        if key in ("knife", "gloves"):
            members = sorted(name for name in present if name not in _KNOWN_GUNS and (("Gloves" in name or "Hand Wraps" in name) == (key == "gloves")))
        else:
            members = [name for name in names if name in present]
        if members:
            weapon_groups.append({"label": label, "weapons": members})
    rarities_present = set(SteamItem.objects.exclude(rarity_key="").values_list("rarity_key", flat=True).distinct())
    return {
        "types": list(TYPE_LABELS.items()),
        "groups": [(key, label) for key, (label, _names) in WEAPON_GROUPS.items()],
        "weapon_groups": weapon_groups,
        "rarities": [(key, RARITY_RU_LABELS[key]) for key in RARITY_ORDER if key in rarities_present],
        "exteriors": [(value, WEAR_RU.get(value, value)) for value in EXTERIOR_LABELS],
        "sorts": list(SORT_LABELS.items()),
    }


def family_key_of(item: SteamItem) -> str:
    return item.family_name or item.market_hash_name


def is_favorite(user, item: SteamItem) -> bool:
    return user.is_authenticated and SteamItemFavorite.objects.filter(user=user, family_key=family_key_of(item)).exists()


def toggle_favorite(user, item: SteamItem) -> bool:
    """Stars or un-stars the item's family; returns the new state."""
    key = family_key_of(item)
    deleted, _ = SteamItemFavorite.objects.filter(user=user, family_key=key).delete()
    if deleted:
        return False
    SteamItemFavorite.objects.get_or_create(user=user, family_key=key, defaults={"item": item})
    return True
