from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from django.db import transaction

from modules.v2.steam_catalog.models import CS2ReferenceItem, SteamItem

from .helpers import normalize_rarity_key, parse_skin_market_hash_name, rarity_ru_label_from_key

REFERENCE_SOURCE_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/{lang}/{name}.json"
REFERENCE_FILES = {
    CS2ReferenceItem.Kind.SKIN: "skins",
    CS2ReferenceItem.Kind.CASE: "crates",
    CS2ReferenceItem.Kind.STICKER: "stickers",
}
DEFAULT_KINDS = (CS2ReferenceItem.Kind.SKIN, CS2ReferenceItem.Kind.CASE)
_BATCH_SIZE = 500


@dataclass(frozen=True)
class ReferenceImportResult:
    kind: str
    created: int
    updated: int
    skipped: int


def fetch_reference_payload(*, kind: str, lang: str = "en", timeout: float = 60.0) -> list[dict[str, Any]]:
    url = REFERENCE_SOURCE_URL.format(lang=lang, name=REFERENCE_FILES[kind])
    req = urllib.request.Request(url, headers={"User-Agent": "CaseUP/cs2_reference"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected payload for {kind}: expected a list")
    return payload


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        return None


def _names(rows) -> list[str]:
    return [str(row.get("name")).strip() for row in (rows or []) if isinstance(row, dict) and row.get("name")]


def _rarity(row: dict) -> tuple[str, str, str]:
    rarity = row.get("rarity") or {}
    name = str(rarity.get("name") or "")
    return name, normalize_rarity_key(name), str(rarity.get("color") or "")


def _skin_fields(row: dict) -> dict[str, Any] | None:
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    rarity_name, rarity_key, rarity_color = _rarity(row)
    collections = _names(row.get("collections"))
    return {
        "kind": CS2ReferenceItem.Kind.SKIN,
        "name": name,
        "market_hash_name": name,
        "weapon": str((row.get("weapon") or {}).get("name") or ""),
        "category": str((row.get("category") or {}).get("name") or ""),
        "pattern": str((row.get("pattern") or {}).get("name") or ""),
        "rarity_name": rarity_name,
        "rarity_key": rarity_key,
        "rarity_color": rarity_color,
        "collection_name": collections[0] if collections else "",
        "collections": collections,
        "crates": _names(row.get("crates")),
        "wears": _names(row.get("wears")),
        "min_float": _decimal(row.get("min_float")),
        "max_float": _decimal(row.get("max_float")),
        "has_stattrak": bool(row.get("stattrak")),
        "has_souvenir": bool(row.get("souvenir")),
        "image_url": str(row.get("image") or ""),
        "release_date": "",
    }


def _case_fields(row: dict) -> dict[str, Any] | None:
    market_hash_name = str(row.get("market_hash_name") or "").strip()
    if not market_hash_name:
        return None
    rarity_name, rarity_key, rarity_color = _rarity(row)
    return {
        "kind": CS2ReferenceItem.Kind.CASE,
        "name": str(row.get("name") or market_hash_name).strip(),
        "market_hash_name": market_hash_name,
        "weapon": "",
        "category": str(row.get("type") or ""),
        "pattern": "",
        "rarity_name": rarity_name,
        "rarity_key": rarity_key,
        "rarity_color": rarity_color,
        "collection_name": "",
        "collections": [],
        "crates": [],
        "wears": [],
        "min_float": None,
        "max_float": None,
        "has_stattrak": False,
        "has_souvenir": False,
        "image_url": str(row.get("image") or ""),
        "release_date": str(row.get("first_sale_date") or ""),
    }


def _sticker_fields(row: dict) -> dict[str, Any] | None:
    market_hash_name = str(row.get("market_hash_name") or "").strip()
    if not market_hash_name:
        return None
    rarity_name, rarity_key, rarity_color = _rarity(row)
    collections = _names(row.get("collections"))
    tournament = (row.get("tournament") or {}).get("name") or ""
    return {
        "kind": CS2ReferenceItem.Kind.STICKER,
        "name": str(row.get("name") or market_hash_name).strip(),
        "market_hash_name": market_hash_name,
        "weapon": "",
        "category": str(row.get("type") or ""),
        "pattern": str(row.get("effect") or ""),
        "rarity_name": rarity_name,
        "rarity_key": rarity_key,
        "rarity_color": rarity_color,
        "collection_name": collections[0] if collections else str(tournament),
        "collections": collections,
        "crates": _names(row.get("crates")),
        "wears": [],
        "min_float": None,
        "max_float": None,
        "has_stattrak": False,
        "has_souvenir": False,
        "image_url": str(row.get("image") or ""),
        "release_date": "",
    }


_PARSERS = {
    CS2ReferenceItem.Kind.SKIN: _skin_fields,
    CS2ReferenceItem.Kind.CASE: _case_fields,
    CS2ReferenceItem.Kind.STICKER: _sticker_fields,
}


@transaction.atomic
def import_reference_rows(*, kind: str, rows: Iterable[dict[str, Any]]) -> ReferenceImportResult:
    parser = _PARSERS[kind]
    parsed: dict[str, dict[str, Any]] = {}
    skipped = 0
    for row in rows:
        external_id = str((row or {}).get("id") or "").strip()
        fields = parser(row) if external_id else None
        if not fields:
            skipped += 1
            continue
        parsed[external_id] = fields

    existing = {item.external_id: item for item in CS2ReferenceItem.objects.filter(external_id__in=parsed.keys())}
    to_create: list[CS2ReferenceItem] = []
    to_update: list[CS2ReferenceItem] = []
    update_fields: list[str] = []

    for external_id, fields in parsed.items():
        update_fields = list(fields.keys())
        item = existing.get(external_id)
        if item is None:
            to_create.append(CS2ReferenceItem(external_id=external_id, **fields))
            continue
        for field, value in fields.items():
            setattr(item, field, value)
        to_update.append(item)

    CS2ReferenceItem.objects.bulk_create(to_create, batch_size=_BATCH_SIZE)
    if to_update:
        CS2ReferenceItem.objects.bulk_update(to_update, update_fields, batch_size=_BATCH_SIZE)

    return ReferenceImportResult(kind=kind, created=len(to_create), updated=len(to_update), skipped=skipped)


def catalog_ids_for_reference(items: Iterable[CS2ReferenceItem]) -> dict[int, int]:
    """Map reference item id -> an existing SteamItem id (same skin family or same market hash name)."""
    items = list(items)
    names = {item.market_hash_name for item in items if item.market_hash_name}
    if not names:
        return {}

    family_ids: dict[str, int] = {}
    for family, item_id in (
        SteamItem.objects
        .filter(family_name__in=names)
        .order_by("is_stattrak", "wear_order", "id")
        .values_list("family_name", "id")
    ):
        family_ids.setdefault(family, item_id)
    for hash_name, item_id in SteamItem.objects.filter(market_hash_name__in=names).values_list("market_hash_name", "id"):
        family_ids.setdefault(hash_name, item_id)

    return {item.id: family_ids[item.market_hash_name] for item in items if item.market_hash_name in family_ids}


def fill_catalog_from_reference(*, reference: CS2ReferenceItem) -> int:
    """Fill blank icon/rarity on catalog items of the same family with reference data."""
    if reference.kind == CS2ReferenceItem.Kind.SKIN:
        queryset = SteamItem.objects.filter(family_name=reference.name)
    else:
        queryset = SteamItem.objects.filter(market_hash_name=reference.market_hash_name)

    touched = 0
    for item in queryset:
        dirty = []
        if not item.icon_url and reference.image_url:
            item.icon_url = reference.image_url
            dirty.append("icon_url")
        if not item.rarity and reference.rarity_name:
            item.rarity = rarity_ru_label_from_key(reference.rarity_key) or reference.rarity_name
            dirty.append("rarity")
        if not item.rarity_key and reference.rarity_key:
            item.rarity_key = reference.rarity_key
            dirty.append("rarity_key")
        if not item.weapon and reference.weapon and item.item_type == SteamItem.ItemType.SKIN:
            _, base_name, _ = parse_skin_market_hash_name(item.market_hash_name)
            if base_name == reference.name:
                item.weapon = reference.weapon
                dirty.append("weapon")
        if dirty:
            item.save(update_fields=[*dirty, "updated_at"])
            touched += 1
    return touched
