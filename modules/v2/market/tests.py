from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.models import ActivityLog
from modules.v2.notifications.models import Notification
from modules.v2.payments.models import Payment
from modules.v2.profiles.models import UserProfile, VerificationLevel
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.system.services import update_module_states

from . import services
from .models import Listing, MarketEvent, MarketSeller

User = get_user_model()


def steam_id_for(user) -> str:
    return str(76561198000000000 + user.pk)


def trade_url_for(user) -> str:
    return f"https://steamcommunity.com/tradeoffer/new/?partner={int(steam_id_for(user)) - services.STEAM_ID64_BASE}&token=AbCd_123"
FETCH = "modules.v2.farm.services.fetch_public_inventory"


def inventory(*asset_ids, tradable=True):
    return [
        {
            "asset_id": asset_id,
            "class_id": "c" + asset_id,
            "instance_id": "0",
            "market_hash_name": f"AK-47 | Redline (Field-Tested) #{asset_id}" if asset_id != "1" else "AK-47 | Redline (Field-Tested)",
            "icon_url": "iconhash",
            "rarity_color": "d32ce6",
            "float_value": 0.2514,
            "tradable": tradable,
            "marketable": True,
        }
        for asset_id in asset_ids
    ]


def make_seller(username="mkt_seller", *, verified=True, days=40, topup="600", trade_url=True) -> User:
    user = User.objects.create_user(username=username, password="x")
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=days))
    UserProfile.objects.filter(user=user).update(steam_id=steam_id_for(user), verification_level=VerificationLevel.VERIFIED if verified else VerificationLevel.NONE)
    if topup:
        Payment.objects.create(user=user, provider="yookassa", amount=Decimal(topup), status=Payment.Status.SUCCEEDED, paid_at=timezone.now())
    if trade_url:
        MarketSeller.objects.create(user=user, trade_url=trade_url_for(user))
    return User.objects.get(pk=user.pk)


class SellerTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_requirements(self):
        self.assertTrue(services.can_sell(make_seller()))
        failing = {item.key for item in services.seller_requirements(make_seller("mkt_new", verified=False, days=3, topup="200", trade_url=False)) if not item.ok}
        self.assertEqual(failing, {"verified", "age", "topup", "trade_url"})
        # Several small top-ups count together; test-mode payments do not.
        user = make_seller("mkt_sum", topup="300")
        Payment.objects.create(user=user, provider="yookassa", amount=Decimal("250"), status=Payment.Status.SUCCEEDED)
        Payment.objects.create(user=user, provider="yookassa", amount=Decimal("1000"), status=Payment.Status.SUCCEEDED, is_test=True)
        self.assertTrue(services.can_sell(user))

    def test_trade_link_must_belong_to_the_linked_steam(self):
        user = make_seller("mkt_link", trade_url=False)
        good = trade_url_for(user)
        seller = services.save_trade_url(user, good.replace("https", "http") + "&extra=1")
        self.assertEqual(seller.trade_url, good)
        self.assertTrue(MarketEvent.objects.filter(kind=MarketEvent.Kind.TRADE_LINK, user=user).exists())
        for bad in ("https://evil.example/tradeoffer/new/?partner=1&token=abcdefgh", good.replace("partner=", "partner=1"), "https://steamcommunity.com/tradeoffer/new/?partner=40"):
            with self.assertRaises(services.MarketError):
                services.save_trade_url(user, bad)


class ListingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.seller = make_seller()
        SteamItem.objects.create(market_hash_name="AK-47 | Redline (Field-Tested)", name="AK-47 | Redline (Field-Tested)", item_type=SteamItem.ItemType.SKIN, price_rub=Decimal("990.00"))

    def test_list_change_price_cancel_with_a_full_journal(self):
        with mock.patch(FETCH, return_value=inventory("1", "2")):
            listing = services.create_listing(self.seller, asset_id="1", price="1 050,50")
            with self.assertRaisesMessage(services.MarketError, "уже выставлен"):
                services.create_listing(self.seller, asset_id="1", price="1000")
        self.assertEqual((listing.price, listing.exterior, listing.item_type), (Decimal("1050.50"), "Field-Tested", "skin"))
        self.assertEqual(listing.steam_price, Decimal("990.00"))
        services.change_price(self.seller, listing_id=listing.pk, price="999")
        services.cancel_listing(self.seller, listing_id=listing.pk)
        with self.assertRaises(services.MarketError):
            services.change_price(self.seller, listing_id=listing.pk, price="5")
        kinds = list(MarketEvent.objects.filter(listing=listing).order_by("id").values_list("kind", flat=True))
        self.assertEqual(kinds, ["listed", "price", "cancelled"])
        self.assertEqual(ActivityLog.objects.filter(event__startswith="market.").count(), 3)

    def test_untradable_or_foreign_items_cannot_be_listed(self):
        with mock.patch(FETCH, return_value=inventory("7", tradable=False)):
            with self.assertRaisesMessage(services.MarketError, "нет в вашем инвентаре"):
                services.create_listing(self.seller, asset_id="7", price="10")
        other = make_seller("mkt_other")
        with mock.patch(FETCH, return_value=inventory("1")):
            listing = services.create_listing(self.seller, asset_id="1", price="10")
            with self.assertRaises(services.MarketError):
                services.change_price(other, listing_id=listing.pk, price="1")
        newcomer = make_seller("mkt_newcomer", days=1)
        with self.assertRaisesMessage(services.MarketError, "условия продавца"):
            services.create_listing(newcomer, asset_id="9", price="10")

    def test_missing_item_takes_the_lot_off_the_showcase(self):
        with mock.patch(FETCH, return_value=inventory("1", "2")):
            gone = services.create_listing(self.seller, asset_id="1", price="10")
            kept = services.create_listing(self.seller, asset_id="2", price="20")
        with mock.patch(FETCH, return_value=inventory("2")):
            report = services.verify_listings(pause_seconds=0)
        self.assertEqual(report["removed"], 1)
        gone.refresh_from_db()
        kept.refresh_from_db()
        self.assertEqual((gone.status, kept.status), (Listing.Status.MISSING, Listing.Status.ACTIVE))
        self.assertTrue(Notification.objects.filter(recipient=self.seller, object_id=gone.pk).exists())
        # An inventory that cannot be read right now removes nothing.
        with mock.patch(FETCH, side_effect=RuntimeError("Steam 429")):
            self.assertEqual(services.verify_listings(pause_seconds=0)["errors"], 1)
        kept.refresh_from_db()
        self.assertEqual(kept.status, Listing.Status.ACTIVE)


class PageTests(TestCase):
    def setUp(self):
        cache.clear()
        update_module_states({"market": True})
        self.seller = make_seller()
        self.client = Client()
        self.client.force_login(self.seller)

    def test_sell_flow_and_showcase(self):
        with mock.patch(FETCH, return_value=inventory("1")):
            page = self.client.get(reverse("market:sell"))
            self.assertContains(page, "всё выполнено")
            self.assertContains(page, "AK-47 | Redline (Field-Tested)")
            response = self.client.post(reverse("market:list_item"), {"asset_id": "1", "price": "123.45"})
        listing = Listing.objects.get()
        self.assertRedirects(response, reverse("market:lot", args=[listing.pk]), fetch_redirect_response=False)

        buyer = make_seller("mkt_buyer")
        client = Client()
        client.force_login(buyer)
        showcase = client.get(reverse("market:index"), {"q": "redline", "sort": "cheap"})
        self.assertContains(showcase, "123.45 ₽")
        lot = client.get(reverse("market:lot", args=[listing.pk]))
        self.assertContains(lot, listing.code)
        self.assertNotContains(lot, "История лота")  # the journal is for the seller and staff
        self.assertContains(self.client.get(reverse("market:lot", args=[listing.pk])), "История лота")

    def test_market_is_off_until_enabled(self):
        update_module_states({"market": False})
        self.assertNotEqual(self.client.get(reverse("market:index")).status_code, 200)
