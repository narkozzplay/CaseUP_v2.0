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

from .models import CS2ReferenceItem, SteamItem, SteamItemPriceHistory
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
