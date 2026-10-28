"""Major items (sticker capsules, souvenir packages, stickers) shown on the Major page.

They come from the Steam Catalog by a keyword in the name ("Singapore 2026"), plus items an admin
links by hand. A daily task refreshes the CS2 reference around the Major so new items appear by
themselves, and alerts the admins when something new shows up or when nothing has appeared yet.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from modules.v2.steam_catalog.models import SteamItem

from .models import Major

logger = logging.getLogger(__name__)

# Items are looked for from a month before the Major until a month after its final.
WATCH_BEFORE = timedelta(days=30)
WATCH_AFTER = timedelta(days=30)
STICKERS_ON_PAGE = 24


def items_queryset(major: Major):
    keyword = (major.items_keyword or "").strip()
    query = Q(pk__in=major.extra_items.values("pk"))
    if keyword:
        query |= Q(market_hash_name__icontains=keyword)
    return SteamItem.objects.filter(query, is_active=True).distinct()


def page_items(major: Major) -> dict:
    items = list(items_queryset(major).order_by("-price_rub", "market_hash_name"))
    packages = [item for item in items if item.item_type == SteamItem.ItemType.CASE]
    stickers = [item for item in items if item.item_type == SteamItem.ItemType.STICKER]
    other = [item for item in items if item.item_type not in (SteamItem.ItemType.CASE, SteamItem.ItemType.STICKER)]
    keyword = (major.items_keyword or "").strip()
    return {
        "packages": packages,
        "stickers": stickers[:STICKERS_ON_PAGE],
        "stickers_total": len(stickers),
        "other": other,
        "total": len(items),
        "catalog_url": f"{reverse('steam_catalog:list')}?q={keyword}" if keyword else "",
    }


def _in_watch_window(major: Major, now) -> bool:
    end = timezone.make_aware(timezone.datetime.combine(major.ends_on, timezone.datetime.min.time())) + WATCH_AFTER
    return major.starts_at - WATCH_BEFORE <= now <= end


def _refresh_catalog() -> None:
    from modules.v2.steam_catalog.models import CS2ReferenceItem
    from modules.v2.steam_catalog.services.bulk_import import import_reference_to_catalog, refresh_reference

    kinds = [CS2ReferenceItem.Kind.CASE, CS2ReferenceItem.Kind.STICKER]
    refresh_reference(kinds)
    import_reference_to_catalog(kinds=kinds)


def check_major_items(*, now=None, refresh: bool = True) -> dict:
    """Daily: pull new items into the catalog, then tell the admins what changed for each watched Major."""
    from modules.v2.system.alerts import send_admin_alert

    now = now or timezone.now()
    majors = [major for major in Major.objects.filter(is_published=True) if _in_watch_window(major, now)]
    report = {}
    if not majors:
        return report
    if refresh:
        try:
            _refresh_catalog()
        except Exception:  # noqa: BLE001 - the check below still runs on what the catalog has
            logger.exception("Major items: catalog refresh failed")
    for major in majors:
        count = items_queryset(major).count()
        admin_url = reverse("admin:majors_major_change", args=[major.pk])
        if count > major.items_known_count:
            send_admin_alert(
                f"major-items-new:{major.pk}:{count}",
                f"{major.name}: в каталоге новые предметы мейджора",
                f"Найдено {count - major.items_known_count} новых, всего {count}. Они уже на странице мейджора.",
                url=major.get_absolute_url(),
                ok=True,
            )
            Major.objects.filter(pk=major.pk).update(items_known_count=count)
        elif count == 0 and now >= major.starts_at:
            send_admin_alert(
                f"major-items-missing:{major.pk}",
                f"{major.name}: предметы мейджора не появились",
                "Капсулы, наклейки и сувенирные наборы не нашлись в каталоге автоматически. "
                "Добавьте их в каталог Steam или привяжите вручную в карточке мейджора.",
                url=admin_url,
                repeat_after=24 * 60 * 60,
            )
        report[major.slug] = count
    return report
