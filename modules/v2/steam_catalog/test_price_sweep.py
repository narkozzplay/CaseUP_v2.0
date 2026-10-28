from __future__ import annotations

import io
import urllib.error
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import SteamItem, SteamItemPriceHistory
from .services import price_queue, price_sweep

FETCH_PAGE = "modules.v2.steam_catalog.services.price_sweep.fetch_page"


def make_item(name: str, **fields) -> SteamItem:
    return SteamItem.objects.create(market_hash_name=name, family_name=name, name=name, item_type=SteamItem.ItemType.SKIN, **fields)


def row(name: str, cents: int, listings: int = 5) -> dict:
    return {"hash_name": name, "sell_price": cents, "sell_listings": listings, "sell_price_text": f"${cents / 100:.2f}"}


def page(rows, total=250):
    return {"success": True, "total_count": total, "results": rows}


@override_settings(STEAM_PRICE_REQUEST_INTERVAL=0, STEAM_USD_RUB_RATE=80, STEAM_PRICE_RELAY_URLS=[])
class SweepTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_cold_items_get_the_lowest_price_in_roubles_and_the_cursor_moves_on(self):
        cold = make_item("Cold | Item")
        stocked = make_item("Hot | Item", stock=1, price_rub=Decimal("50.00"))
        with mock.patch(FETCH_PAGE, return_value=page([row("Cold | Item", 125), row("Hot | Item", 999), row("Not | In Catalog", 10)])) as fetch:
            result = price_sweep.sweep(pages=1)
        fetch.assert_called_once_with(0)
        cold.refresh_from_db()
        self.assertEqual(cold.price_rub, Decimal("100.00"))  # $1.25 × 80
        self.assertIsNotNone(cold.price_checked_at)
        self.assertFalse(cold.price_missing)
        self.assertEqual(SteamItemPriceHistory.objects.get(item=cold).source, price_queue.SWEEP_SOURCE)
        # The hot item keeps its median from the per-item queue.
        stocked.refresh_from_db()
        self.assertEqual(stocked.price_rub, Decimal("50.00"))
        self.assertEqual((result.pages, result.seen, result.matched), (1, 3, 1))
        self.assertEqual(price_sweep.state()["start"], 100)

    def test_after_the_last_page_the_sweep_starts_over(self):
        with mock.patch(FETCH_PAGE, side_effect=[page([row("A", 1)] * 3, total=150), page([row("B", 1)], total=150)]) as fetch:
            price_sweep.sweep(pages=2)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [0, 100])
        current = price_sweep.state()
        self.assertEqual((current["start"], current["total"], current["passes"]), (0, 150, 1))
        self.assertIsNotNone(current["last_page_time"])

    def test_items_without_listings_are_left_to_the_queue(self):
        item = make_item("Empty | Item")
        with mock.patch(FETCH_PAGE, return_value=page([row("Empty | Item", 0, listings=0)])):
            price_sweep.sweep(pages=1)
        item.refresh_from_db()
        self.assertIsNone(item.price_checked_at)

    def test_history_is_written_every_few_hours_or_on_a_jump_only(self):
        item = make_item("Moving | Item")
        SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("100.00"), source=price_queue.SWEEP_SOURCE)
        # +0.8 % within the hour: skipped; +12 %: written; +0.9 % seven hours later: written.
        for cents, rows, hours_later in ((126, 1, 0), (140, 2, 0), (141, 3, 7)):
            if hours_later:
                SteamItemPriceHistory.objects.filter(item=item).update(created_at=timezone.now() - timedelta(hours=hours_later))
            with mock.patch(FETCH_PAGE, return_value=page([row("Moving | Item", cents)])):
                price_sweep.sweep(pages=1)
            self.assertEqual(SteamItemPriceHistory.objects.filter(item=item).count(), rows)
        item.refresh_from_db()
        self.assertEqual(item.price_rub, Decimal("112.80"))

    def test_sweep_moves_do_not_make_an_item_warm(self):
        item = make_item("Cold | Mover")
        SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("1.00"), source=price_queue.SWEEP_SOURCE)
        self.assertFalse(SteamItem.objects.filter(pk=item.pk).filter(price_queue._warm_filter(timezone.now())).exists())
        SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("2.00"), source="queue")
        self.assertTrue(SteamItem.objects.filter(pk=item.pk).filter(price_queue._warm_filter(timezone.now())).exists())

    def test_429_pauses_the_server_and_keeps_the_page(self):
        error = urllib.error.HTTPError("https://steam", 429, "Too Many Requests", {}, io.BytesIO(b""))
        with mock.patch(FETCH_PAGE, side_effect=error):
            result = price_sweep.sweep(pages=3)
        self.assertEqual(result.pages, 0)
        self.assertIn("429", result.notes[0])
        self.assertIsNotNone(price_queue.route_paused_until(price_queue.DIRECT))
        self.assertEqual(price_sweep.state()["start"], 0)
        self.assertIn("429", price_sweep.state()["message"])
        # A paused server sends nothing more.
        with mock.patch(FETCH_PAGE) as fetch:
            price_sweep.sweep(pages=1)
        fetch.assert_not_called()

    def test_an_empty_page_in_the_middle_is_retried(self):
        with mock.patch(FETCH_PAGE, return_value=page([], total=500)):
            result = price_sweep.sweep(pages=2)
        self.assertEqual(result.pages, 0)
        self.assertEqual(price_sweep.state()["start"], 0)

    def test_requests_count_toward_the_server_budget(self):
        with mock.patch(FETCH_PAGE, return_value=page([row("X", 1)])):
            price_sweep.sweep(pages=2)
        self.assertEqual(price_queue.spent_this_hour(price_queue.DIRECT), 2)


@override_settings(STEAM_PRICE_REQUEST_INTERVAL=0, STEAM_USD_RUB_RATE=0, STEAM_PRICE_RELAY_URLS=[])
class RateTests(TestCase):
    def setUp(self):
        cache.clear()

    def _answers(self, *payloads):
        return mock.patch("modules.v2.steam_catalog.services.price_sweep._get_json", side_effect=list(payloads))

    def test_rate_is_steams_own_and_cached(self):
        with self._answers({"lowest_price": "$20.00"}, {"lowest_price": "1 640,50 pуб."}) as get:
            self.assertEqual(price_sweep.usd_rub_rate(), Decimal("82.0250"))
            self.assertEqual(price_sweep.usd_rub_rate(), Decimal("82.0250"))
        self.assertEqual(get.call_count, 2)

    def test_last_known_rate_is_used_when_steam_is_busy(self):
        with self._answers({"lowest_price": "$20.00"}, {"lowest_price": "1600,00 pуб."}):
            price_sweep.usd_rub_rate()
        cache.delete(price_sweep.RATE_KEY)
        error = urllib.error.HTTPError("https://steam", 429, "Too Many Requests", {}, io.BytesIO(b""))
        with self._answers(error):
            self.assertEqual(price_sweep.usd_rub_rate(), Decimal("80.0000"))

    def test_without_any_rate_the_sweep_waits(self):
        with self._answers({}), mock.patch(FETCH_PAGE) as fetch:
            result = price_sweep.sweep(pages=1)
        fetch.assert_not_called()
        self.assertIn("Курс доллара", result.notes[-1])
