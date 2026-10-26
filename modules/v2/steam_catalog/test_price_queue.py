from __future__ import annotations

import io
import json
import time
import urllib.error
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile, UserRole

from .models import CS2ReferenceItem, SteamItem, SteamItemFavorite, SteamItemPriceHistory
from .services import bulk_import, price_queue

PRICE_FETCH = "modules.v2.steam_catalog.services.fetch_price_rub_from_steam"


def make_item(name: str, *, kind=SteamItem.ItemType.SKIN, checked_hours_ago=None, stock=0) -> SteamItem:
    item = SteamItem.objects.create(market_hash_name=name, family_name=name, name=name, item_type=kind, stock=stock)
    if checked_hours_ago is not None:
        SteamItem.objects.filter(pk=item.pk).update(price_checked_at=timezone.now() - timedelta(hours=checked_hours_ago))
    return item


class QueueTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_hot_items_first_then_the_stalest_rest(self):
        hot = make_item("Hot | Item", stock=2, checked_hours_ago=3)
        never = make_item("Never | Checked")
        old = make_item("Old | Item", checked_hours_ago=30)
        SteamItem.objects.filter(pk=old.pk).update(price_rub=Decimal("2.00"))
        make_item("Fresh | Item", checked_hours_ago=1)
        make_item("Hot | Fresh", stock=1, checked_hours_ago=1)
        batch = price_queue.pick_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertEqual([item.pk for item in batch], [hot.pk, never.pk, old.pk])

    @mock.patch(PRICE_FETCH)
    def test_429_pauses_the_route_and_leaves_the_item_unchecked(self, fetch_mock):
        first, second = make_item("A | One"), make_item("B | Two")
        fetch_mock.side_effect = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO(b""))
        result = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertTrue(result.rate_limited)
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertFalse(SteamItem.objects.filter(pk__in=[first.pk, second.pk], price_checked_at__isnull=False).exists())
        self.assertIsNotNone(price_queue.backoff_until())

        again = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertIn("Пауза", again.errors[0])
        self.assertEqual(fetch_mock.call_count, 1)

    @mock.patch(PRICE_FETCH, return_value=None)
    def test_items_without_a_price_still_move_on(self, _fetch):
        item = make_item("No | Listings")
        result = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=5)
        self.assertEqual(result.checked, 0)
        item.refresh_from_db()
        self.assertIsNotNone(item.price_checked_at)

    @override_settings(STEAM_PRICE_RELAY_URLS=["https://relay.example/steam_price.php"], STEAM_PRICE_RELAY_TOKEN="t" * 32, STEAM_PRICE_REQUEST_INTERVAL=1)
    def test_relay_is_a_second_route(self):
        make_item("Relay | One")
        make_item("Relay | Two")

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=0):
            self.assertEqual(request.get_header("X-relay-token"), "t" * 32)
            return Response(json.dumps({"success": True, "median_price": "12,50 pуб."}).encode())

        with mock.patch(PRICE_FETCH, return_value=Decimal("10.00")) as direct, mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as relay:
            result = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=5)
        self.assertEqual((direct.call_count, relay.call_count, result.checked), (1, 1, 2))
        self.assertEqual(sorted(SteamItem.objects.values_list("price_rub", flat=True)), [Decimal("10.00"), Decimal("12.50")])
        self.assertEqual(SteamItemPriceHistory.objects.count(), 2)
        self.assertEqual(price_queue.batch_limit(5), 120)

    @override_settings(STEAM_PRICE_RELAY_URLS=["https://relay.example/steam_price.php"], STEAM_PRICE_REQUEST_INTERVAL=1)
    def test_broken_relay_is_paused_and_the_server_carries_on(self):
        first, second = make_item("Broken | One"), make_item("Broken | Two")
        forbidden = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))
        with mock.patch(PRICE_FETCH, return_value=Decimal("5.00")), mock.patch("urllib.request.urlopen", side_effect=forbidden):
            result = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=5)
        self.assertIn("ключ", " ".join(result.errors))
        self.assertIsNotNone(price_queue.route_paused_until("https://relay.example/steam_price.php"))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.price_rub, Decimal("5.00"))
        self.assertIsNone(second.price_checked_at)  # left for the next run instead of being marked checked


class TierTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_hot_then_warm_then_cold(self):
        user = get_user_model().objects.create_user(username="fav_user", password="x")
        cold = make_item("Cold | Item", checked_hours_ago=30)
        SteamItem.objects.filter(pk=cold.pk).update(price_rub=Decimal("1.00"))
        viewed = make_item("Viewed | Item", checked_hours_ago=7)
        SteamItem.objects.filter(pk=viewed.pk).update(price_rub=Decimal("1.00"), last_viewed_at=timezone.now() - timedelta(days=1))
        moved = make_item("Moved | Item", checked_hours_ago=8)
        SteamItem.objects.filter(pk=moved.pk).update(price_rub=Decimal("1.00"))
        SteamItemPriceHistory.objects.create(item=moved, price_rub=Decimal("1.00"))
        starred = SteamItem.objects.create(market_hash_name="Star | Item (Field-Tested)", family_name="Star | Item", item_type=SteamItem.ItemType.SKIN)
        SteamItem.objects.filter(pk=starred.pk).update(price_checked_at=timezone.now() - timedelta(hours=3))
        other_wear = make_item("Star | Item (Minimal Wear)", checked_hours_ago=3)
        SteamItem.objects.filter(pk=other_wear.pk).update(family_name="Star | Item")
        SteamItemFavorite.objects.create(user=user, family_key="Star | Item", item=starred)
        make_item("Warm | Fresh", checked_hours_ago=2)  # viewed, but checked recently: not due
        SteamItem.objects.filter(market_hash_name="Warm | Fresh").update(last_viewed_at=timezone.now())
        make_item("Cold | Fresh", checked_hours_ago=10)

        batch = price_queue.pick_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertEqual([item.pk for item in batch], [starred.pk, other_wear.pk, moved.pk, viewed.pk, cold.pk])

    def test_items_without_a_price_wait_a_week(self):
        make_item("Empty | Recent", checked_hours_ago=48)
        make_item("Empty | Hot", checked_hours_ago=3, stock=1)
        empty_old = make_item("Empty | Old", checked_hours_ago=24 * 8)
        failed = make_item("Failed | Lookup", checked_hours_ago=30)  # the last request failed: daily cadence
        SteamItem.objects.exclude(pk=failed.pk).update(price_missing=True)
        batch = price_queue.pick_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertEqual([item.pk for item in batch], [empty_old.pk, failed.pk])

    @mock.patch(PRICE_FETCH)
    def test_only_a_real_answer_marks_the_price_missing(self, fetch_mock):
        item = make_item("Flaky | Item")
        fetch_mock.side_effect = OSError("timeout")
        price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=1)
        item.refresh_from_db()
        self.assertFalse(item.price_missing)
        SteamItem.objects.filter(pk=item.pk).update(price_checked_at=None)
        fetch_mock.side_effect, fetch_mock.return_value = None, None
        price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=1)
        item.refresh_from_db()
        self.assertTrue(item.price_missing)
        SteamItem.objects.filter(pk=item.pk).update(price_checked_at=None)
        fetch_mock.side_effect = OSError("timeout")  # a failure after an empty answer: back to the daily cadence
        price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=1)
        item.refresh_from_db()
        self.assertFalse(item.price_missing)
        SteamItem.objects.filter(pk=item.pk).update(price_checked_at=None)
        fetch_mock.side_effect = None
        fetch_mock.return_value = Decimal("4.00")
        price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=1)
        item.refresh_from_db()
        self.assertFalse(item.price_missing)

    @mock.patch(PRICE_FETCH, return_value=Decimal("7.00"))
    def test_manual_refresh_clears_the_empty_marker(self, _fetch):
        from .services import refresh_item_prices

        item = make_item("Manual | Item", checked_hours_ago=24 * 3)
        SteamItem.objects.filter(pk=item.pk).update(price_missing=True)
        item.refresh_from_db()
        refresh_item_prices(item)
        item.refresh_from_db()
        self.assertEqual((item.price_rub, item.price_missing), (Decimal("7.00"), False))
        self.assertGreater(item.price_checked_at, timezone.now() - timedelta(minutes=1))

    def test_views_of_people_make_the_item_warm_and_robots_do_not(self):
        item = make_item("Seen | Item")
        robot = mock.Mock(META={"HTTP_USER_AGENT": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"})
        price_queue.note_item_view(robot, item)
        item.refresh_from_db()
        self.assertIsNone(item.last_viewed_at)
        person = mock.Mock(META={"HTTP_USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"})
        price_queue.note_item_view(person, item)
        item.refresh_from_db()
        self.assertIsNotNone(item.last_viewed_at)
        first = item.last_viewed_at
        price_queue.note_item_view(person, item)  # throttled: one write an hour
        item.refresh_from_db()
        self.assertEqual(item.last_viewed_at, first)


class BudgetTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(STEAM_PRICE_HOURLY_BUDGET=2, STEAM_PRICE_REQUEST_INTERVAL=0)
    @mock.patch(PRICE_FETCH, return_value=Decimal("3.00"))
    def test_hourly_budget_stops_the_batch(self, fetch_mock):
        for index in range(4):
            make_item(f"Budget | {index}")
        result = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertTrue(result.out_of_budget)
        self.assertFalse(result.rate_limited)
        self.assertEqual(price_queue.spent_this_hour(price_queue.DIRECT), 2)

        again = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertTrue(again.out_of_budget)
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(price_queue.coverage()["routes"][0]["spent"], 2)

    @override_settings(STEAM_PRICE_HOURLY_BUDGET=1, STEAM_PRICE_REQUEST_INTERVAL=0)
    def test_the_counter_not_the_pre_check_decides(self):
        # Another worker saw budget left at the same moment: only one of the two may send the request.
        with mock.patch.object(price_queue, "_has_budget", return_value=True), mock.patch("time.sleep"):
            self.assertEqual(price_queue._acquire_route(), price_queue.DIRECT)
            self.assertIsNone(price_queue._acquire_route(deadline=time.monotonic() + price_queue.DEADLINE_RESERVE_SECONDS + 0.5))

    @override_settings(STEAM_PRICE_HOURLY_BUDGET=0, STEAM_PRICE_REQUEST_INTERVAL=0)
    @mock.patch(PRICE_FETCH, return_value=Decimal("3.00"))
    def test_zero_budget_means_no_hourly_limit(self, fetch_mock):
        for index in range(3):
            make_item(f"Free | {index}")
        result = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=10)
        self.assertEqual((fetch_mock.call_count, result.out_of_budget), (3, False))


class BulkImportTests(TestCase):
    def setUp(self):
        cache.clear()
        CS2ReferenceItem.objects.create(
            external_id="skin-1", kind=CS2ReferenceItem.Kind.SKIN, name="AK-47 | Redline", market_hash_name="AK-47 | Redline",
            weapon="AK-47", rarity_key="classified", rarity_name="Classified", wears=["Field-Tested", "Minimal Wear"], has_stattrak=True, image_url="https://img/ak.png",
        )
        CS2ReferenceItem.objects.create(
            external_id="knife-1", kind=CS2ReferenceItem.Kind.SKIN, name="★ Karambit | Fade", market_hash_name="★ Karambit | Fade",
            weapon="Karambit", wears=["Factory New"], has_stattrak=True,
        )
        CS2ReferenceItem.objects.create(external_id="case-1", kind=CS2ReferenceItem.Kind.CASE, name="Recoil Case", market_hash_name="Recoil Case")
        CS2ReferenceItem.objects.create(external_id="st-1", kind=CS2ReferenceItem.Kind.STICKER, name="Sticker | Gold", market_hash_name="Sticker | Gold")

    def test_every_variant_lands_in_the_catalog_without_steam(self):
        with mock.patch("urllib.request.urlopen") as network:
            result = bulk_import.import_reference_to_catalog()
        network.assert_not_called()
        names = set(SteamItem.objects.values_list("market_hash_name", flat=True))
        self.assertEqual(
            names,
            {
                "AK-47 | Redline (Minimal Wear)", "AK-47 | Redline (Field-Tested)",
                "StatTrak™ AK-47 | Redline (Minimal Wear)", "StatTrak™ AK-47 | Redline (Field-Tested)",
                "★ Karambit | Fade (Factory New)", "★ StatTrak™ Karambit | Fade (Factory New)",
                "Recoil Case", "Sticker | Gold",
            },
        )
        self.assertEqual((result.created, result.already_there), (8, 0))
        redline = SteamItem.objects.get(market_hash_name="AK-47 | Redline (Field-Tested)")
        self.assertEqual((redline.family_name, redline.weapon, redline.wear_order, redline.icon_url), ("AK-47 | Redline", "AK-47", 3, "https://img/ak.png"))
        self.assertEqual(SteamItem.objects.get(market_hash_name="Sticker | Gold").item_type, SteamItem.ItemType.STICKER)

        again = bulk_import.import_reference_to_catalog()
        self.assertEqual((again.created, again.already_there), (0, 8))

    def test_button_runs_the_import_for_staff_only(self):
        admin = get_user_model().objects.create_user(username="catalog_admin", password="x")
        UserProfile.objects.filter(user=admin).update(role=UserRole.ADMIN)
        client = Client()
        client.force_login(admin)
        with mock.patch("modules.v2.steam_catalog.services.bulk_import.refresh_reference", return_value={}):
            response = client.post(reverse("steam_catalog:reference_import_all"))
        self.assertRedirects(response, reverse("steam_catalog:reference"), fetch_redirect_response=False)
        self.assertEqual(SteamItem.objects.count(), 8)
        self.assertEqual(bulk_import.read_status()["state"], "done")
        self.assertContains(client.get(reverse("steam_catalog:reference")), "добавлено 8")

        user = get_user_model().objects.create_user(username="catalog_user", password="x")
        client.force_login(user)
        self.assertEqual(client.post(reverse("steam_catalog:reference_import_all")).status_code, 403)

    @mock.patch(PRICE_FETCH, return_value=Decimal("1.00"))
    def test_batch_stops_at_the_deadline_instead_of_hitting_the_task_time_limit(self, fetch_mock):
        item = make_item("Late | Item")
        result = price_queue.refresh_batch(category=SteamItem.ItemType.SKIN, limit=5, deadline=time.monotonic() + 1)
        self.assertTrue(result.out_of_time)
        self.assertFalse(result.rate_limited)
        self.assertEqual(result.errors, [])
        fetch_mock.assert_not_called()
        item.refresh_from_db()
        self.assertIsNone(item.price_checked_at)  # stays the stalest, so the next run takes it first

    @override_settings(STEAM_PRICE_REQUEST_INTERVAL=3, STEAM_PRICE_BATCH_MAX=60)
    def test_batch_limit_fits_the_time_left(self):
        self.assertEqual(price_queue.batch_limit(60), 60)
        # 120 s left: 100 s after the reserve, 80 % of it at one request per 3 s.
        self.assertEqual(price_queue.batch_limit(60, budget_seconds=120), 26)
        self.assertEqual(price_queue.batch_limit(60, budget_seconds=5), 1)
