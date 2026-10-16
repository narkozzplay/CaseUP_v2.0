from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.models import AdminAuditLog, AuditAction
from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.system.services import update_module_states

from . import deals
from .models import Deal, Listing, MarketEvent
from .notifications import KIND_DEAL
from .services import MarketError
from .tests import make_seller

FETCH = "modules.v2.farm.services.fetch_public_inventory"


def balance(user) -> Decimal:
    return UserProfile.objects.get(user=user).balance_coins


class DealTestCase(TestCase):
    def setUp(self):
        cache.clear()
        update_module_states({"market": True})
        self.seller = make_seller("deal_seller")
        self.buyer = make_seller("deal_buyer")
        self.listing = Listing.objects.create(
            seller=self.seller, asset_id="555", market_hash_name="AK-47 | Redline (Field-Tested)", price=Decimal("300.00"), float_value=Decimal("0.25140000"), item_type="skin"
        )

    def fund(self, user, amount="1000.00"):
        adjust_balance(user=user, delta=Decimal(amount), reason=LedgerReason.TOPUP, amount_snapshot=Decimal(amount))

    def bought(self) -> Deal:
        self.fund(self.buyer)
        with self.captureOnCommitCallbacks(execute=True):
            return deals.buy(self.buyer, listing_id=self.listing.pk)

    def later(self, **delta):
        return timezone.now() + timedelta(**delta)


class BuyingTests(DealTestCase):
    def test_purchase_freezes_money_reserves_the_lot_and_opens_a_chat(self):
        deal = self.bought()
        self.assertRegex(deal.code, r"^MKT-[A-Z2-9]{6}$")
        self.assertEqual(balance(self.buyer), Decimal("700.00"))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)
        deal.refresh_from_db()
        self.assertIsNotNone(deal.chat_space_id)
        self.assertTrue(Notification.objects.filter(recipient=self.seller, kind=KIND_DEAL, payload__event="new").exists())
        self.assertTrue(MarketEvent.objects.filter(deal=deal, kind=MarketEvent.Kind.DEAL_CREATED).exists())
        with self.assertRaisesMessage(MarketError, "уже купили"):
            deals.buy(make_seller("deal_late"), listing_id=self.listing.pk)

    def test_not_enough_money_sends_to_topup_and_back(self):
        client = Client()
        client.force_login(self.buyer)
        self.fund(self.buyer, "120.00")
        response = client.post(reverse("market:buy", args=[self.listing.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("payments:topup"), response["Location"])
        self.assertIn("amount=180", response["Location"])
        self.assertIn("next=%2Fmarket%2Flot%2F", response["Location"])
        self.assertFalse(Deal.objects.exists())
        page = client.get(reverse("market:lot", args=[self.listing.pk]))
        self.assertContains(page, "Купить сразу")
        self.assertContains(page, "Не хватает 180.00 ₽")

    def test_own_lot_and_missing_trade_link(self):
        self.fund(self.seller)
        with self.assertRaisesMessage(MarketError, "собственный"):
            deals.buy(self.seller, listing_id=self.listing.pk)
        no_link = make_seller("deal_nolink", trade_url=False)
        with self.assertRaisesMessage(MarketError, "трейд-ссылку"):
            deals.buy(no_link, listing_id=self.listing.pk)


class LifecycleTests(DealTestCase):
    def test_happy_path_ends_with_the_seller_paid_after_the_hold(self):
        deal = self.bought()
        with self.assertRaises(MarketError):
            deals.mark_sent(self.buyer, deal.code)
        deals.mark_sent(self.seller, deal.code)
        deal = deals.confirm_received(self.buyer, deal.code)
        self.assertEqual(deal.status, Deal.Status.HOLD)
        self.assertGreater(deal.hold_until, self.later(days=6))

        self.assertEqual(deals.process_deals(now=self.later(days=3))["completed"], 0)
        self.assertEqual(deals.process_deals(now=self.later(days=8))["completed"], 1)
        deal.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual((deal.status, self.listing.status), (Deal.Status.COMPLETED, Listing.Status.SOLD))
        self.assertEqual(balance(self.seller), Decimal("300.00"))
        kinds = list(MarketEvent.objects.filter(deal=deal).order_by("id").values_list("kind", flat=True))
        self.assertEqual(kinds, ["deal_created", "deal_sent", "deal_accepted", "deal_completed"])

    def test_cancellations_refund_in_full(self):
        deal = self.bought()
        with self.assertRaisesMessage(MarketError, "отменить сделку, если продавец не отправил"):
            deals.cancel(self.buyer, deal.code)
        deals.cancel(self.seller, deal.code)
        self.assertEqual(balance(self.buyer), Decimal("1000.00"))

        second = Listing.objects.create(seller=self.seller, asset_id="556", market_hash_name="Recoil Case", price=Decimal("50.00"))
        late = deals.buy(self.buyer, listing_id=second.pk)
        Deal.objects.filter(pk=late.pk).update(send_deadline=timezone.now() - timedelta(minutes=1))
        deals.process_deals()
        self.assertTrue(Notification.objects.filter(recipient=self.buyer, payload__event="deadline").exists())
        deals.cancel(self.buyer, late.code)
        self.assertEqual(balance(self.buyer), Decimal("1000.00"))
        late.refresh_from_db()
        with self.assertRaises(MarketError):
            deals.mark_sent(self.seller, late.code)

    def test_silent_buyer_is_covered_by_the_inventory_proof(self):
        deal = self.bought()
        deals.mark_sent(self.seller, deal.code)
        item = {"market_hash_name": "AK-47 | Redline (Field-Tested)", "float_value": 0.2514, "asset_id": "999"}
        with mock.patch(FETCH, return_value=[item]):
            self.assertEqual(deals.process_deals()["proof"], 1)
        deal.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.HOLD)
        self.assertIsNone(deal.accepted_at)
        with mock.patch(FETCH, return_value=[item]):
            self.assertEqual(deals.process_deals(now=self.later(days=8))["completed"], 1)

    def test_item_gone_by_the_end_of_the_hold_opens_a_dispute(self):
        admin = make_seller("deal_admin")
        UserProfile.objects.filter(user=admin).update(role=UserRole.ADMIN)
        deal = self.bought()
        deals.mark_sent(self.seller, deal.code)
        with mock.patch(FETCH, return_value=[{"market_hash_name": "AK-47 | Redline (Field-Tested)", "float_value": 0.2514}]):
            deals.process_deals()
        with mock.patch(FETCH, return_value=[]), self.captureOnCommitCallbacks(execute=True):
            report = deals.process_deals(now=self.later(days=8))
        self.assertEqual(report["auto_disputed"], 1)
        deal.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.DISPUTED)
        self.assertEqual(balance(self.seller), Decimal("0.00"))
        self.assertTrue(Notification.objects.filter(recipient=admin, payload__event="dispute_admin").exists())


class DisputeTests(DealTestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_seller("deal_judge")
        UserProfile.objects.filter(user=self.admin).update(role=UserRole.ADMIN)
        self.deal = self.bought()
        deals.mark_sent(self.seller, self.deal.code)

    def test_admin_refunds_the_buyer(self):
        with self.assertRaisesMessage(MarketError, "подробнее"):
            deals.open_dispute(self.buyer, self.deal.code, reason="нет")
        deals.open_dispute(self.buyer, self.deal.code, reason="Обмен не пришёл, продавец молчит в чате")
        client = Client()
        client.force_login(self.admin)
        page = client.get(reverse("market:deal", args=[self.deal.code]))
        self.assertContains(page, "Решение по спору")
        client.post(reverse("market:deal_action", args=[self.deal.code, "resolve"]), {"decision": "refund", "note": "обмен не подтверждён"})
        self.deal.refresh_from_db()
        self.assertEqual((self.deal.status, self.deal.resolution), (Deal.Status.CANCELLED, Deal.Resolution.REFUND))
        self.assertEqual(balance(self.buyer), Decimal("1000.00"))
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.MARKET_DISPUTE, actor=self.admin).exists())
        self.assertContains(client.get(reverse("control_center:market"), {"status": "cancelled"}), self.deal.code)

    def test_admin_pays_the_seller(self):
        deals.open_dispute(self.seller, self.deal.code, reason="Покупатель получил предмет, но не подтверждает")
        deals.resolve_dispute(self.admin, self.deal.code, decision="pay_seller")
        self.assertEqual(balance(self.seller), Decimal("300.00"))

    def test_strangers_see_nothing(self):
        stranger = make_seller("deal_stranger")
        client = Client()
        client.force_login(stranger)
        self.assertEqual(client.get(reverse("market:deal", args=[self.deal.code])).status_code, 404)
        self.assertEqual(client.post(reverse("market:deal_action", args=[self.deal.code, "resolve"]), {"decision": "refund"}).status_code, 404)
        party = Client()
        party.force_login(self.buyer)
        party.post(reverse("market:deal_action", args=[self.deal.code, "resolve"]), {"decision": "refund"})
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.SENT)  # a party cannot resolve
