from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.farm.models import FarmAccount, FarmAccountInventoryItem
from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem

from .models import SaleOrder, TradeBotAccount, TradeOffer, TradeUserPenalty
from .services import (
    TradeHubError,
    cancel_sale_order,
    create_sale_order,
    finalize_sale_order,
    mark_offer_accepted,
    mark_offer_sent,
)

User = get_user_model()


class TradeHubTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _make_farmer(self, username="farmer_tradehub"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.FARMER
        profile.save(update_fields=["role"])
        return user

    def _make_admin(self, username="admin_tradehub"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        return user

    def _make_item(self, market_hash_name="AK-47 | Redline (Field-Tested)", price=Decimal("1000.00")):
        return SteamItem.objects.create(
            market_hash_name=market_hash_name,
            family_name="AK-47 | Redline",
            name=market_hash_name,
            item_type=SteamItem.ItemType.SKIN,
            price_rub=price,
            allow_sell=True,
            is_active=True,
        )

    def _make_inventory_item(self, *, user, asset_id="1001", market_hash_name="AK-47 | Redline (Field-Tested)", payout=Decimal("650.00")):
        account = FarmAccount.objects.create(
            owner=user,
            title=f"Farm {asset_id}",
            steam_id64="76561198000000000",
            trade_url="https://steamcommunity.com/tradeoffer/new/?partner=39734272&token=AbCdEf12",
            status=FarmAccount.Status.ACTIVE,
        )
        item = self._make_item(market_hash_name=market_hash_name, price=Decimal("1000.00"))
        inventory_item = FarmAccountInventoryItem.objects.create(
            account=account,
            asset_id=asset_id,
            class_id="2001",
            instance_id="0",
            amount=1,
            market_hash_name=market_hash_name,
            tradable=True,
            marketable=True,
            matched_item=item,
            catalog_price_rub_snapshot=item.price_rub,
            payout_price_rub_snapshot=payout,
            is_sellable=True,
            is_missing=False,
        )
        return account, inventory_item, item

    def test_create_sale_order_locks_item_and_assigns_bot(self):
        user = self._make_farmer("farmer_create_sale")
        account, inventory_item, _ = self._make_inventory_item(user=user)
        bot = TradeBotAccount.objects.create(
            title="Bot #1",
            steam_id64="76561198000000001",
            status=TradeBotAccount.Status.ACTIVE,
            is_enabled=True,
            is_default=True,
        )

        order = create_sale_order(
            seller=user,
            account=account,
            inventory_item_ids=[inventory_item.id],
            created_by=user,
        )

        inventory_item.refresh_from_db()
        self.assertEqual(order.bot_account, bot)
        self.assertEqual(order.status, SaleOrder.Status.OFFER_PENDING)
        self.assertEqual(inventory_item.sale_state, FarmAccountInventoryItem.SaleState.RESERVED)
        self.assertTrue(TradeOffer.objects.filter(order=order, status=TradeOffer.Status.NEW).exists())

    def test_finalize_sale_order_pays_user_and_increases_stock(self):
        user = self._make_farmer("farmer_finalize_sale")
        account, inventory_item, steam_item = self._make_inventory_item(user=user, asset_id="1002")
        TradeBotAccount.objects.create(
            title="Bot #2",
            steam_id64="76561198000000002",
            status=TradeBotAccount.Status.ACTIVE,
            is_enabled=True,
            is_default=True,
        )
        order = create_sale_order(seller=user, account=account, inventory_item_ids=[inventory_item.id], created_by=user)
        mark_offer_accepted(order=order, created_by=user)
        finalize_sale_order(order=order, created_by=user)

        profile = UserProfile.objects.get(user=user)
        inventory_item.refresh_from_db()
        steam_item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(profile.balance_coins, Decimal("650.00"))
        self.assertEqual(steam_item.stock, 1)
        self.assertEqual(order.status, SaleOrder.Status.COMPLETED)
        self.assertEqual(inventory_item.sale_state, FarmAccountInventoryItem.SaleState.SOLD)

    def test_second_user_cancel_bans_user(self):
        user = self._make_farmer("farmer_cancel_sale")
        TradeBotAccount.objects.create(
            title="Bot #3",
            steam_id64="76561198000000003",
            status=TradeBotAccount.Status.ACTIVE,
            is_enabled=True,
            is_default=True,
        )
        account1, item1, _ = self._make_inventory_item(user=user, asset_id="1003", market_hash_name="AWP | Atheris (Field-Tested)")
        order1 = create_sale_order(seller=user, account=account1, inventory_item_ids=[item1.id], created_by=user)
        cancel_sale_order(order=order1, reason="Первый отказ", cancelled_by_user=True, created_by=user)

        account2, item2, _ = self._make_inventory_item(user=user, asset_id="1004", market_hash_name="USP-S | Cortex (Field-Tested)")
        order2 = create_sale_order(seller=user, account=account2, inventory_item_ids=[item2.id], created_by=user)
        cancel_sale_order(order=order2, reason="Второй отказ", cancelled_by_user=True, created_by=user)

        self.assertEqual(TradeUserPenalty.objects.filter(user=user, type=TradeUserPenalty.Type.WARNING).count(), 1)
        self.assertTrue(TradeUserPenalty.objects.filter(user=user, type=TradeUserPenalty.Type.BAN, is_active=True).exists())

    def test_cancel_sale_order_rejects_second_cancel_and_does_not_duplicate_penalty(self):
        user = self._make_farmer("farmer_cancel_once")
        TradeBotAccount.objects.create(
            title="Bot #cancel once",
            steam_id64="76561198000000203",
            status=TradeBotAccount.Status.ACTIVE,
            is_enabled=True,
            is_default=True,
        )
        account, item, _ = self._make_inventory_item(user=user, asset_id="1203", market_hash_name="MP9 | Hydra (Field-Tested)")
        order = create_sale_order(seller=user, account=account, inventory_item_ids=[item.id], created_by=user)

        cancel_sale_order(order=order, reason="Первый отказ", cancelled_by_user=True, created_by=user)
        with self.assertRaises(TradeHubError):
            cancel_sale_order(order=order, reason="Повторный отказ", cancelled_by_user=True, created_by=user)

        penalties = TradeUserPenalty.objects.filter(user=user, order=order)
        self.assertEqual(penalties.count(), 1)
        self.assertEqual(penalties.first().type, TradeUserPenalty.Type.WARNING)

    def test_mark_offer_sent_rejects_terminal_completed_order(self):
        user = self._make_farmer("farmer_mark_sent_completed")
        TradeBotAccount.objects.create(
            title="Bot #terminal",
            steam_id64="76561198000000204",
            status=TradeBotAccount.Status.ACTIVE,
            is_enabled=True,
            is_default=True,
        )
        account, item, _ = self._make_inventory_item(user=user, asset_id="1204", market_hash_name="Galil AR | Chatterbox (Field-Tested)")
        order = create_sale_order(seller=user, account=account, inventory_item_ids=[item.id], created_by=user)
        mark_offer_accepted(order=order, created_by=user)
        finalize_sale_order(order=order, created_by=user)

        with self.assertRaises(TradeHubError):
            mark_offer_sent(order=order, external_offer_id="late-offer", created_by=user)

        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.COMPLETED)

    def test_farmer_can_create_order_from_farm_page(self):
        user = self._make_farmer("farmer_tradehub_view")
        account, inventory_item, _ = self._make_inventory_item(user=user, asset_id="1005")
        TradeBotAccount.objects.create(
            title="Bot #4",
            steam_id64="76561198000000004",
            status=TradeBotAccount.Status.ACTIVE,
            is_enabled=True,
            is_default=True,
        )

        self.client.login(username="farmer_tradehub_view", password="StrongPass123")
        response = self.client.post(
            reverse("tradehub:create_order"),
            {
                "account_id": account.id,
                "inventory_item_ids": [inventory_item.id],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SaleOrder.objects.filter(seller=user).exists())

    def test_admin_can_open_bots_list(self):
        self._make_admin("admin_tradehub_view")
        self.client.login(username="admin_tradehub_view", password="StrongPass123")
        response = self.client.get(reverse("tradehub:bots_list"))
        self.assertEqual(response.status_code, 200)
