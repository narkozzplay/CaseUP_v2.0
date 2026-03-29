from __future__ import annotations

from urllib.parse import parse_qs, quote, unquote, urlparse

from modules.v2.steam_catalog.models import SteamItem

from .constants import EXTERIOR_LABELS, EXTERIOR_ORDER, RARITY_RU_LABELS, WEAR_RU, _EXTERIOR_RE, _STATTRAK_PREFIXES


def extract_market_hash_name(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""

    if "steamcommunity.com" not in raw and "market_hash_name=" not in raw and "/market/listings/" not in raw:
        return raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return raw

    query = parse_qs(parsed.query or "")
    if "market_hash_name" in query and query["market_hash_name"]:
        return unquote(query["market_hash_name"][0])

    path = (parsed.path or "").strip("/")
    parts = path.split("/")
    try:
        idx = parts.index("listings")
        if len(parts) >= idx + 3:
            return unquote(parts[idx + 2])
    except ValueError:
        pass

    if parts:
        return unquote(parts[-1])

    return raw


def canonical_listing_url(*, appid: int, market_hash_name: str) -> str:
    return f"https://steamcommunity.com/market/listings/{appid}/{quote(market_hash_name)}"


def normalize_icon_url(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""

    if v.startswith("http://") or v.startswith("https://"):
        return v

    return v


def guess_item_type(market_hash_name: str) -> str:
    s = (market_hash_name or "").strip()
    if not s:
        return SteamItem.ItemType.OTHER

    if s.startswith("Sticker | "):
        return SteamItem.ItemType.STICKER
    if s.startswith("Graffiti | "):
        return SteamItem.ItemType.GRAFFITI
    if s.startswith("Charm | "):
        return SteamItem.ItemType.CHARM
    if s.endswith(" Case") and " | " not in s:
        return SteamItem.ItemType.CASE
    if " | " in s:
        return SteamItem.ItemType.SKIN

    return SteamItem.ItemType.OTHER


def normalize_rarity_key(label: str) -> str:
    s = (label or "").strip().lower()
    if not s:
        return ""

    if "ширпотреб" in s or "consumer" in s:
        return "consumer"
    if "промышлен" in s or "industrial" in s:
        return "industrial"
    if "армей" in s or "mil-spec" in s or "milspec" in s or "mil spec" in s:
        return "milspec"
    if "запрещ" in s or "restricted" in s:
        return "restricted"
    if "засекр" in s or "classified" in s:
        return "classified"
    if "тайн" in s or "covert" in s or "secret" in s:
        return "covert"
    if "контраб" in s or "contraband" in s:
        return "contraband"
    if "крайне" in s or "extraordinary" in s or "gold" in s:
        return "extraordinary"

    return ""


def rarity_ru_label_from_key(key: str) -> str:
    return RARITY_RU_LABELS.get((key or "").strip().lower(), "")


def parse_skin_market_hash_name(market_hash_name: str) -> tuple[bool, str, str]:
    s = (market_hash_name or "").strip()
    is_stattrak = False

    for prefix in _STATTRAK_PREFIXES:
        if s.startswith(prefix):
            is_stattrak = True
            s = s[len(prefix):]
            break

    exterior = ""
    match = _EXTERIOR_RE.search(s)
    if match:
        exterior = match.group(1).strip()
        s = s[:match.start()].strip()

    return is_stattrak, s, exterior


def build_skin_market_hash_name(*, is_stattrak: bool, base_name: str, exterior: str) -> str:
    prefix = "StatTrak™ " if is_stattrak else ""
    return f"{prefix}{base_name} ({exterior})"


def weapon_from_base_name(base_name: str) -> str:
    s = (base_name or "").strip()
    if " | " in s:
        return s.split(" | ", 1)[0].strip()
    return ""


def derive_ru_name_from_market_hash_name(market_hash_name: str) -> str:
    s = (market_hash_name or "").strip()
    if not s:
        return ""

    kind_prefix = {
        "Sticker | ": "Наклейка | ",
        "Graffiti | ": "Граффити | ",
        "Charm | ": "Брелок | ",
        "Patch | ": "Нашивка | ",
    }
    for en, ru in kind_prefix.items():
        if s.startswith(en):
            s = ru + s[len(en):]
            break

    if s.endswith(" Case") and " | " not in s:
        s = s[:-5] + " Кейс"

    match = _EXTERIOR_RE.search(s)
    if match:
        wear_en = match.group(1).strip()
        wear_ru = WEAR_RU.get(wear_en)
        if wear_ru:
            s = _EXTERIOR_RE.sub(f"({wear_ru})", s)

    s = s.replace("Souvenir", "Сувенирный")
    return s.strip()


__all__ = [
    "EXTERIOR_LABELS",
    "EXTERIOR_ORDER",
    "build_skin_market_hash_name",
    "canonical_listing_url",
    "derive_ru_name_from_market_hash_name",
    "extract_market_hash_name",
    "guess_item_type",
    "normalize_icon_url",
    "normalize_rarity_key",
    "parse_skin_market_hash_name",
    "rarity_ru_label_from_key",
    "weapon_from_base_name",
]
