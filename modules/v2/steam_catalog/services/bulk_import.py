"""One-button import of the whole CS2 reference into the Steam Catalog.

Adding items one by one asks Steam for the listing page and the price of every wear variant — about 13
requests per skin, far too many for thousands of items. Here catalog rows are built from the reference
data alone (names, wears, StatTrak, rarity, picture) without a single request to Steam; prices arrive
later through the price queue.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone

from modules.v2.steam_catalog.models import CS2ReferenceItem, SteamItem

from .constants import EXTERIOR_LABELS, EXTERIOR_ORDER
from .helpers import rarity_ru_label_from_key

logger = logging.getLogger(__name__)

STATUS_KEY = "steam_catalog:bulk_import_status"
STATUS_TTL = 7 * 24 * 60 * 60
KIND_TO_TYPE = {
    CS2ReferenceItem.Kind.SKIN: SteamItem.ItemType.SKIN,
    CS2ReferenceItem.Kind.CASE: SteamItem.ItemType.CASE,
    CS2ReferenceItem.Kind.STICKER: SteamItem.ItemType.STICKER,
}


@dataclass
class BulkImportResult:
    references: int = 0
    created: int = 0
    already_there: int = 0


def skin_hash_name(base_name: str, exterior: str, *, stattrak: bool) -> str:
    """Steam market hash name of a skin variant; knives and gloves keep the star in front of StatTrak™."""
    name = base_name
    if stattrak:
        name = f"★ StatTrak™ {base_name[2:]}" if base_name.startswith("★ ") else f"StatTrak™ {base_name}"
    return f"{name} ({exterior})" if exterior else name


def variants_for_reference(reference: CS2ReferenceItem) -> list[dict]:
    """Catalog rows (SteamItem field values) for one reference item."""
    common = {
        "item_type": KIND_TO_TYPE[reference.kind],
        "icon_url": reference.image_url or "",
        "rarity": rarity_ru_label_from_key(reference.rarity_key) or reference.rarity_name or "",
        "rarity_key": reference.rarity_key or "",
        "allow_buy": True,
        "allow_sell": True,
        "is_active": True,
        "stock": 0,
    }
    if reference.kind != CS2ReferenceItem.Kind.SKIN:
        hash_name = reference.market_hash_name or reference.name
        return [{**common, "market_hash_name": hash_name, "family_name": hash_name, "name": hash_name}]

    base_name = reference.name
    wears = [wear for wear in EXTERIOR_LABELS if wear in set(reference.wears or [])] or [""]
    rows = []
    for stattrak in (False, True) if reference.has_stattrak else (False,):
        for wear in wears:
            hash_name = skin_hash_name(base_name, wear, stattrak=stattrak)
            rows.append({
                **common,
                "market_hash_name": hash_name,
                "family_name": base_name,
                "name": hash_name,
                "exterior": wear,
                "wear_order": EXTERIOR_ORDER.get(wear, 0),
                "weapon": reference.weapon or "",
                "is_stattrak": stattrak,
            })
    return rows


def import_reference_to_catalog(*, kinds=None, batch_size: int = 1000) -> BulkImportResult:
    kinds = list(kinds or KIND_TO_TYPE)
    result = BulkImportResult()
    pending: list[SteamItem] = []

    def flush():
        if not pending:
            return
        names = [item.market_hash_name for item in pending]
        existing = set(SteamItem.objects.filter(market_hash_name__in=names).values_list("market_hash_name", flat=True))
        fresh = {item.market_hash_name: item for item in pending if item.market_hash_name not in existing}
        SteamItem.objects.bulk_create(fresh.values(), ignore_conflicts=True, batch_size=batch_size)
        result.created += len(fresh)
        result.already_there += len(pending) - len(fresh)
        pending.clear()

    for reference in CS2ReferenceItem.objects.filter(kind__in=kinds).order_by("id").iterator(chunk_size=batch_size):
        result.references += 1
        pending.extend(SteamItem(**row) for row in variants_for_reference(reference))
        if len(pending) >= batch_size:
            flush()
    flush()
    return result


def refresh_reference(kinds) -> dict[str, str]:
    """Re-download the reference datasets (stickers included). Failures leave the current reference as is."""
    from .reference import fetch_reference_payload, import_reference_rows

    report = {}
    for kind in kinds:
        try:
            outcome = import_reference_rows(kind=kind, rows=fetch_reference_payload(kind=kind, lang="en"))
            report[kind] = f"+{outcome.created}, обновлено {outcome.updated}"
        except Exception as exc:  # noqa: BLE001 - offline server: import from what is already there
            logger.warning("Reference refresh for %s failed: %s", kind, exc)
            report[kind] = "не удалось обновить, взят текущий справочник"
    return report


def run_full_import(*, kinds=None, refresh: bool = True) -> dict:
    kinds = list(kinds or KIND_TO_TYPE)
    write_status({"state": "running", "started_at": timezone.now().isoformat()})
    try:
        reference_report = refresh_reference(kinds) if refresh else {}
        result = import_reference_to_catalog(kinds=kinds)
    except Exception as exc:
        logger.exception("Bulk catalog import failed")
        status = {"state": "failed", "error": str(exc)[:300], "finished_at": timezone.now().isoformat()}
        write_status(status)
        return status
    status = {
        "state": "done",
        "finished_at": timezone.now().isoformat(),
        "references": result.references,
        "created": result.created,
        "already_there": result.already_there,
        "reference_report": reference_report,
    }
    write_status(status)
    return status


def write_status(status: dict) -> None:
    cache.set(STATUS_KEY, status, STATUS_TTL)


def read_status() -> dict | None:
    return cache.get(STATUS_KEY)
