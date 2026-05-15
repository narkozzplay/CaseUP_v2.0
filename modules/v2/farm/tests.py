from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.steam_catalog.models import SteamItem

from .models import (
    ActiveDropPoolEntry,
    FarmAccount,
    FarmAccountInventoryItem,
    FarmAccountInventorySync,
    WeeklyDropLog,
)
from .services import (
    STEAM_INVENTORY_PAGE_SIZE,
    _steam_inventory_fetch_page,
    get_drop_week_by_index,
    parse_trade_url,
    sync_account_inventory,
)

User = get_user_model()


class FarmTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _make_farmer(self, username="farmer1"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.FARMER
        profile.save(update_fields=["role"])
        return user

    def _make_admin(self, username="admin1"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        return user

    def _make_drop_item(self, market_hash_name="Revolution Case", item_type=SteamItem.ItemType.CASE):
        return SteamItem.objects.create(
            market_hash_name=market_hash_name,
            family_name=market_hash_name,
            name=market_hash_name,
            item_type=item_type,
            price_rub=Decimal("100.00"),
            is_active=True,
        )

    def test_drop_week_label_contains_month_and_date_range(self):
        week = get_drop_week_by_index(1)
        self.assertIn("декабрь 2025 / январь 2026", week.label)
        self.assertIn("31.12.2025 — 06.01.2026", week.label)

    def test_farm_forbidden_for_plain_user(self):
        user = User.objects.create_user(username="user1", password="StrongPass123")
        self.client.login(username="user1", password="StrongPass123")

        response = self.client.get(reverse("farm:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_farm_available_for_farmer(self):
        self._make_farmer("farmer_dashboard")
        self.client.login(username="farmer_dashboard", password="StrongPass123")

        response = self.client.get(reverse("farm:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_farmer_can_create_account(self):
        user = self._make_farmer("farmer_account")

        self.client.login(username="farmer_account", password="StrongPass123")
        response = self.client.post(
            reverse("farm:accounts_add"),
            {
                "title": "Farm #1",
                "steam_id64": "76561198000000000",
                "trade_url": "",
                "login_hint": "steam login",
                "status": "review",
                "notes": "first account",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(FarmAccount.objects.filter(owner=user, title="Farm #1").exists())

    def test_trade_url_parser_builds_steam_id64(self):
        parsed = parse_trade_url("https://steamcommunity.com/tradeoffer/new/?partner=39734272&token=AbCdEf12")
        self.assertEqual(parsed.partner_id, "39734272")
        self.assertEqual(parsed.token, "AbCdEf12")
        self.assertEqual(parsed.steam_id64, "76561198000000000")

    def test_farmer_can_create_account_with_trade_url_only(self):
        user = self._make_farmer("farmer_trade_url")

        self.client.login(username="farmer_trade_url", password="StrongPass123")
        response = self.client.post(
            reverse("farm:accounts_add"),
            {
                "title": "Farm trade url",
                "steam_id64": "",
                "trade_url": "https://steamcommunity.com/tradeoffer/new/?partner=39734272&token=AbCdEf12",
                "login_hint": "",
                "status": "review",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        account = FarmAccount.objects.get(owner=user, title="Farm trade url")
        self.assertEqual(account.steam_id64, "76561198000000000")

    def test_farmer_can_create_weekly_drop_with_one_item(self):
        user = self._make_farmer("farmer_drop")
        account = FarmAccount.objects.create(
            owner=user,
            title="Farm account",
            status=FarmAccount.Status.ACTIVE,
        )
        item = self._make_drop_item()

        self.client.login(username="farmer_drop", password="StrongPass123")
        response = self.client.post(
            reverse("farm:drops_add"),
            {
                "catalog_scope": "all",
                "item_type_filter": "case",
                "search": "",
                "account": account.id,
                "week_index": "1",
                "item_1": item.id,
                "item_2": "",
                "notes": "weekly case drop",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            WeeklyDropLog.objects.filter(
                account=account,
                week_index=1,
                item_1=item,
            ).exists()
        )

    def test_duplicate_weekly_drop_for_same_account_is_blocked(self):
        user = self._make_farmer("farmer_duplicate")
        account = FarmAccount.objects.create(
            owner=user,
            title="Farm account",
            status=FarmAccount.Status.ACTIVE,
        )
        item = self._make_drop_item("Fracture Case")

        WeeklyDropLog.objects.create(
            account=account,
            week_index=1,
            week_start="2025-12-31",
            week_end="2026-01-06",
            item_1=item,
            created_by=user,
        )

        self.client.login(username="farmer_duplicate", password="StrongPass123")
        response = self.client.post(
            reverse("farm:drops_add"),
            {
                "catalog_scope": "all",
                "item_type_filter": "case",
                "search": "",
                "account": account.id,
                "week_index": "1",
                "item_1": item.id,
                "item_2": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "дроп за выбранную неделю уже сохранён")
        self.assertEqual(WeeklyDropLog.objects.filter(account=account, week_index=1).count(), 1)

    def test_drops_list_search_without_results_returns_404_page(self):
        self._make_farmer("farmer_drop_search_404")
        self.client.login(username="farmer_drop_search_404", password="StrongPass123")

        response = self.client.get(reverse("farm:drops_list"), {"q": "missing drop"})

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Legacy weekly logs не найдены", status_code=404)

    def test_drops_add_search_without_results_returns_404_page(self):
        self._make_farmer("farmer_drop_add_search_404")
        self.client.login(username="farmer_drop_add_search_404", password="StrongPass123")

        response = self.client.get(reverse("farm:drops_add"), {"search": "missing item"})

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Предметы для legacy-log не найдены", status_code=404)

    def test_sessions_list_search_without_results_returns_404_page(self):
        self._make_farmer("farmer_sessions_search_404")
        self.client.login(username="farmer_sessions_search_404", password="StrongPass123")

        response = self.client.get(reverse("farm:sessions_list"), {"q": "missing session"})

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Farm drops не найдены", status_code=404)

    def test_farmer_cannot_open_drop_pool_manage(self):
        self._make_farmer("farmer_pool")
        self.client.login(username="farmer_pool", password="StrongPass123")

        response = self.client.get(reverse("farm:drop_pool"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_add_item_to_drop_pool(self):
        self._make_admin("admin_pool")
        item = self._make_drop_item("Graffiti | Test", SteamItem.ItemType.GRAFFITI)

        self.client.login(username="admin_pool", password="StrongPass123")
        response = self.client.post(
            reverse("farm:drop_pool"),
            {
                "item": item.id,
                "sort_order": 10,
                "note": "current pool",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ActiveDropPoolEntry.objects.filter(item=item).exists())

    @patch("modules.v2.farm.services.fetch_public_inventory")
    def test_sync_account_inventory_creates_only_tradable_sellable_and_catalog_blocked_items(self, mocked_fetch):
        user = self._make_farmer("farmer_inventory_sync")
        account = FarmAccount.objects.create(
            owner=user,
            title="Sync Account",
            trade_url="https://steamcommunity.com/tradeoffer/new/?partner=39734272&token=AbCdEf12",
            status=FarmAccount.Status.ACTIVE,
        )
    
        matched = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline (Field-Tested)",
            item_type=SteamItem.ItemType.SKIN,
            price_rub=Decimal("1000.00"),
            allow_sell=True,
            is_active=True,
        )
    
        mocked_fetch.return_value = [
            {
                "asset_id": "101",
                "class_id": "201",
                "instance_id": "0",
                "amount": 1,
                "market_hash_name": matched.market_hash_name,
                "icon_url": "test-icon-1",
                "tradable": True,
                "marketable": True,
            },
            {
                "asset_id": "102",
                "class_id": "202",
                "instance_id": "0",
                "amount": 1,
                "market_hash_name": "Unknown Item",
                "icon_url": "test-icon-2",
                "tradable": True,
                "marketable": True,
            },
            {
                "asset_id": "103",
                "class_id": "203",
                "instance_id": "0",
                "amount": 1,
                "market_hash_name": "Souvenir P250 | Sand Dune (Battle-Scarred)",
                "icon_url": "test-icon-3",
                "tradable": False,
                "marketable": True,
            },
        ]
    
        sync_run = sync_account_inventory(account=account)
    
        self.assertEqual(sync_run.status, FarmAccountInventorySync.Status.SUCCESS)
        self.assertEqual(sync_run.total_assets, 2)
        self.assertEqual(sync_run.total_sellable, 1)
    
        sellable_item = FarmAccountInventoryItem.objects.get(account=account, asset_id="101")
        blocked_item = FarmAccountInventoryItem.objects.get(account=account, asset_id="102")
    
        self.assertEqual(account.steam_id64, "76561198000000000")
        self.assertTrue(sellable_item.is_sellable)
        self.assertEqual(sellable_item.matched_item, matched)
        self.assertEqual(sellable_item.catalog_price_rub_snapshot, Decimal("1000.00"))
        self.assertEqual(sellable_item.payout_price_rub_snapshot, Decimal("650.00"))
    
        self.assertFalse(blocked_item.is_sellable)
        self.assertEqual(blocked_item.sell_block_reason, "Предмет отсутствует в Steam Catalog.")
    
        self.assertFalse(
            FarmAccountInventoryItem.objects.filter(account=account, asset_id="103").exists()
        )

    @patch("modules.v2.farm.services.fetch_public_inventory")
    def test_account_detail_inventory_is_available_for_owner(self, mocked_fetch):
        user = self._make_farmer("farmer_inventory_detail")
        account = FarmAccount.objects.create(
            owner=user,
            title="Detail Account",
            trade_url="https://steamcommunity.com/tradeoffer/new/?partner=39734272&token=AbCdEf12",
            status=FarmAccount.Status.ACTIVE,
        )

        SteamItem.objects.create(
            market_hash_name="USP-S | Ticket to Hell (Field-Tested)",
            family_name="USP-S | Ticket to Hell",
            name="USP-S | Ticket to Hell (Field-Tested)",
            item_type=SteamItem.ItemType.SKIN,
            price_rub=Decimal("250.00"),
            allow_sell=True,
            is_active=True,
        )

        mocked_fetch.return_value = [
            {
                "asset_id": "301",
                "class_id": "401",
                "instance_id": "0",
                "amount": 1,
                "market_hash_name": "USP-S | Ticket to Hell (Field-Tested)",
                "icon_url": "test-icon-3",
                "tradable": True,
                "marketable": True,
            }
        ]
        sync_account_inventory(account=account)

        self.client.login(username="farmer_inventory_detail", password="StrongPass123")
        response = self.client.get(reverse("farm:accounts_detail", args=[account.id]), {"tab": "inventory"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "USP-S | Ticket to Hell (Field-Tested)")
        self.assertContains(response, "Готов к продаже")

    @patch("modules.v2.farm.services.urlopen")
    def test_inventory_fetch_page_maps_http_400_to_human_error(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError(
            url="https://steamcommunity.com/inventory/test/730/2",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"success":false,"Error":"Inventory unavailable"}'),
        )

        with self.assertRaises(RuntimeError) as exc:
            _steam_inventory_fetch_page("76561198000000000")

        self.assertIn("Steam inventory недоступен", str(exc.exception))

    @patch("modules.v2.farm.services.urlopen")
    def test_inventory_fetch_page_maps_rate_limit_to_human_error(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError(
            url="https://steamcommunity.com/inventory/test/730/2",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=BytesIO(b"You've made too many requests recently"),
        )

        with self.assertRaises(RuntimeError) as exc:
            _steam_inventory_fetch_page("76561198000000000")

        self.assertIn("ограничил запросы", str(exc.exception))

    @patch("modules.v2.farm.services.urlopen")
    def test_inventory_fetch_page_uses_safe_page_size_and_norender(self, mocked_urlopen):
        response = MagicMock()
        response.read.return_value = b'{"success": true, "assets": [], "descriptions": [], "more_items": false}'
        response.headers.get_content_charset.return_value = "utf-8"

        mocked_urlopen.return_value.__enter__.return_value = response

        _steam_inventory_fetch_page("76561198000000000")

        request = mocked_urlopen.call_args.args[0]
        self.assertIn(f"count={STEAM_INVENTORY_PAGE_SIZE}", request.full_url)
        self.assertIn("norender=1", request.full_url)
from django.utils import timezone

from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import assign_user_subscription_plan

from .models import (
    FarmDropSale,
    FarmDropSession,
    FarmModuleConfig,
    FarmerRewardGrant,
    FarmerRewardTier,
    FarmNotificationEvent,
)
from .services import (
    calculate_session_analytics,
    create_drop_session,
    dispatch_caseup_advantage_notifications,
    dispatch_price_threshold_notifications,
    get_dashboard_analytics_for_user,
    grant_farmer_rewards_for_user,
    record_drop_sale,
    resolve_sale_fee_percent,
)


class FarmAnalyticsExpansionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="farmer_analytics", password="StrongPass123")
        profile = UserProfile.objects.get(user=self.user)
        profile.role = UserRole.FARMER
        profile.save(update_fields=["role"])
        self.account = FarmAccount.objects.create(owner=self.user, title="Farmer A", status=FarmAccount.Status.ACTIVE)
        self.item = SteamItem.objects.create(
            market_hash_name="Gamma 2 Case",
            family_name="Gamma 2 Case",
            name="Gamma 2 Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("160.00"),
            allow_sell=True,
            is_active=True,
        )
        self.farmer_start = SubscriptionPlan.objects.get(code="farmer_start")
        self.farmer_pro = SubscriptionPlan.objects.get(code="farmer_pro")
        self.farmer_ultra = SubscriptionPlan.objects.get(code="farmer_ultra")
        assign_user_subscription_plan(user=self.user, plan=self.farmer_ultra, family="farmer")

    def _create_session(self):
        return create_drop_session(
            account=self.account,
            collected_at=timezone.now(),
            source_type=FarmDropSession.SourceType.WEEKLY,
            title="Weekly #1",
            notes="analytics seed",
            created_by=self.user,
            items_payload=[
                {
                    "steam_item": self.item,
                    "market_hash_name": self.item.market_hash_name,
                    "name_snapshot": self.item.name,
                    "quantity": 2,
                    "collected_price_rub_snapshot": Decimal("100.00"),
                }
            ],
        )

    def test_session_analytics_calculates_collected_current_and_pnl(self):
        session = self._create_session()
        drop_item = session.items.get()

        sale = record_drop_sale(
            item=drop_item,
            quantity=1,
            platform=FarmDropSale.Platform.CASEUP,
            gross_sale_rub=Decimal("150.00"),
            sold_at=timezone.now(),
            notes="first sale",
        )

        analytics = calculate_session_analytics(FarmDropSession.objects.prefetch_related("items__sales", "items__steam_item").get(pk=session.id))

        self.assertEqual(analytics.collected_value_rub, Decimal("200.00"))
        self.assertEqual(analytics.current_value_rub, Decimal("160.00"))
        self.assertEqual(analytics.delta_rub, Decimal("-40.00"))
        self.assertEqual(analytics.realized_pnl_rub, sale.profit_rub)
        self.assertEqual(analytics.unrealized_pnl_rub, Decimal("60.00"))
        self.assertEqual(analytics.total_pnl_rub, Decimal("99.50"))

    def test_resolve_caseup_fee_uses_subscription_reduction(self):
        fee = resolve_sale_fee_percent(user=self.user, platform=FarmDropSale.Platform.CASEUP)
        self.assertEqual(fee, Decimal("7.00"))
        assign_user_subscription_plan(user=self.user, plan=self.farmer_start, family="farmer")
        fee_without_reduction = resolve_sale_fee_percent(user=self.user, platform=FarmDropSale.Platform.CASEUP)
        self.assertEqual(fee_without_reduction, Decimal("15.00"))

    def test_reward_tier_granted_on_caseup_turnover(self):
        FarmerRewardTier.objects.update_or_create(
            code="test_tier",
            defaults={
                "title": "Test Tier",
                "threshold_turnover_rub": Decimal("100.00"),
                "reward_title": "Bonus",
                "reward_description": "desc",
                "bonus_value_rub": Decimal("50.00"),
                "sort_order": 1,
                "is_active": True,
            },
        )
        session = self._create_session()
        drop_item = session.items.get()
        record_drop_sale(
            item=drop_item,
            quantity=1,
            platform=FarmDropSale.Platform.CASEUP,
            gross_sale_rub=Decimal("150.00"),
            sold_at=timezone.now(),
        )
        grants = grant_farmer_rewards_for_user(self.user)
        self.assertTrue(grants)
        self.assertTrue(FarmerRewardGrant.objects.filter(owner=self.user, tier__code="test_tier").exists())

    @patch("modules.v2.farm.services.send_user_message")
    def test_dispatch_price_threshold_notification_creates_event(self, mocked_send):
        mocked_send.return_value = object()
        session = self._create_session()
        drop_item = session.items.get()
        self.item.price_rub = Decimal("130.00")
        self.item.save(update_fields=["price_rub"])
        assign_user_subscription_plan(user=self.user, plan=self.farmer_pro, family="farmer")
        events = dispatch_price_threshold_notifications(user=self.user)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, FarmNotificationEvent.EventType.PRICE_UP)

    @patch("modules.v2.farm.services.send_user_message")
    def test_dispatch_caseup_advantage_notification_creates_event(self, mocked_send):
        mocked_send.return_value = object()
        config, _ = FarmModuleConfig.objects.get_or_create(singleton_key=1)
        config.external_reference_fee_percent = Decimal("25.00")
        config.caseup_advantage_min_rub = Decimal("5.00")
        config.save()
        session = self._create_session()
        assign_user_subscription_plan(user=self.user, plan=self.farmer_pro, family="farmer")
        events = dispatch_caseup_advantage_notifications(user=self.user)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, FarmNotificationEvent.EventType.CASEUP_ADVANTAGE)

    def test_dashboard_analytics_aggregates_sessions(self):
        session = self._create_session()
        drop_item = session.items.get()
        record_drop_sale(
            item=drop_item,
            quantity=1,
            platform=FarmDropSale.Platform.CASEUP,
            gross_sale_rub=Decimal("150.00"),
            sold_at=timezone.now(),
        )
        analytics = get_dashboard_analytics_for_user(self.user)
        self.assertEqual(analytics.collected_value_rub, Decimal("200.00"))
        self.assertEqual(analytics.sold_positions_count, 1)
        self.assertGreaterEqual(analytics.caseup_turnover_rub, Decimal("150.00"))


class FarmViewsExpansionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="farmer_views", password="StrongPass123")
        profile = UserProfile.objects.get(user=self.user)
        profile.role = UserRole.FARMER
        profile.save(update_fields=["role"])
        self.account = FarmAccount.objects.create(owner=self.user, title="Farmer V", status=FarmAccount.Status.ACTIVE)
        self.item = SteamItem.objects.create(
            market_hash_name="Recoil Case",
            family_name="Recoil Case",
            name="Recoil Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("120.00"),
            allow_sell=True,
            is_active=True,
        )
        self.client.login(username="farmer_views", password="StrongPass123")

    def _make_admin(self, username="admin_views"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        return user

    def _create_session(self, *, title="Locked Session", collected_price=Decimal("123.45"), quantity=2):
        return create_drop_session(
            account=self.account,
            collected_at=timezone.now(),
            source_type=FarmDropSession.SourceType.WEEKLY,
            title=title,
            notes="view seed",
            created_by=self.user,
            items_payload=[
                {
                    "steam_item": self.item,
                    "market_hash_name": self.item.market_hash_name,
                    "name_snapshot": self.item.name,
                    "quantity": quantity,
                    "collected_price_rub_snapshot": collected_price,
                }
            ],
        )

    def test_sessions_list_available_without_farmer_plan_but_shows_cta(self):
        response = self.client.get(reverse("farm:sessions_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Farm drops")
        self.assertContains(response, "При покупке тарифа")
        self.assertContains(response, "Добавить дроп")

    def test_sessions_list_available_with_farmer_plan(self):
        assign_user_subscription_plan(user=self.user, plan=SubscriptionPlan.objects.get(code="farmer_start"), family="farmer")
        response = self.client.get(reverse("farm:sessions_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Farm drops")
        self.assertNotContains(response, "При покупке тарифа")

    def test_dashboard_locked_does_not_render_raw_analytics(self):
        self._create_session(title="Dashboard Locked")

        response = self.client.get(reverse("farm:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analytics locked")
        self.assertNotContains(response, "246.90 ₽")
        self.assertNotContains(response, "240.00 ₽")

    def test_sessions_list_locked_does_not_render_raw_analytics(self):
        self._create_session(title="List Locked")

        response = self.client.get(reverse("farm:sessions_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "List Locked")
        self.assertContains(response, "Locked")
        self.assertNotContains(response, "246.90 ₽")
        self.assertNotContains(response, "240.00 ₽")

    def test_account_detail_sessions_locked_does_not_render_raw_analytics(self):
        self._create_session(title="Account Locked")

        response = self.client.get(reverse("farm:accounts_detail", args=[self.account.id]), {"tab": "sessions"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Account Locked")
        self.assertContains(response, "Analytics locked")
        self.assertNotContains(response, "246.90 ₽")
        self.assertNotContains(response, "240.00 ₽")

    def test_session_detail_locked_does_not_render_raw_analytics(self):
        session = self._create_session(title="Detail Locked")

        response = self.client.get(reverse("farm:sessions_detail", args=[session.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Locked")
        self.assertContains(response, "Analytics locked")
        self.assertContains(response, "Recoil Case")
        self.assertNotContains(response, "123.45 ₽")
        self.assertNotContains(response, "120.00 ₽")

    def test_session_detail_with_farmer_plan_renders_analytics_values(self):
        assign_user_subscription_plan(user=self.user, plan=SubscriptionPlan.objects.get(code="farmer_start"), family="farmer")
        session = self._create_session(title="Detail Unlocked")

        response = self.client.get(reverse("farm:sessions_detail", args=[session.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "123.45 ₽")
        self.assertContains(response, "120.00 ₽")
        self.assertNotContains(response, "Analytics locked")

    def test_can_create_session_and_sale_without_farmer_plan(self):
        response = self.client.post(
            reverse("farm:sessions_add"),
            {
                "account": self.account.id,
                "source_type": FarmDropSession.SourceType.WEEKLY,
                "title": "New Session",
                "collected_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "notes": "seed",
                "items-TOTAL_FORMS": "2",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "1000",
                "items-1-steam_item": "",
                "items-1-market_hash_name": "",
                "items-1-quantity": "1",
                "items-1-collected_price_rub_snapshot": "",
                "items-1-notes": "",
                "items-0-steam_item": str(self.item.id),
                "items-0-market_hash_name": "",
                "items-0-quantity": "2",
                "items-0-collected_price_rub_snapshot": "100.00",
                "items-0-notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        session = FarmDropSession.objects.get(title="New Session")
        self.assertEqual(session.items.count(), 1)
        drop_item = session.items.get()
        response = self.client.post(
            reverse("farm:sessions_detail", args=[session.id]),
            {
                "item": drop_item.id,
                "platform": FarmDropSale.Platform.CASEUP,
                "sold_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "quantity": "1",
                "gross_sale_rub": "140.00",
                "notes": "sold",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(FarmDropSale.objects.filter(item=drop_item).exists())

    def test_farm_settings_requires_admin_plus(self):
        response = self.client.get(reverse("farm:settings"))
        self.assertEqual(response.status_code, 403)

    @patch("modules.v2.farm.views.dispatch_price_threshold_notifications")
    @patch("modules.v2.farm.views.dispatch_caseup_advantage_notifications")
    def test_farm_settings_get_dispatch_param_does_not_dispatch(self, mocked_advantage, mocked_thresholds):
        self._make_admin("admin_settings_get")
        self.client.login(username="admin_settings_get", password="StrongPass123")

        response = self.client.get(reverse("farm:settings"), {"dispatch": "thresholds"})

        self.assertEqual(response.status_code, 200)
        mocked_thresholds.assert_not_called()
        mocked_advantage.assert_not_called()

    @patch("modules.v2.farm.views.dispatch_price_threshold_notifications")
    def test_farm_settings_threshold_dispatch_uses_post(self, mocked_thresholds):
        mocked_thresholds.return_value = []
        self._make_admin("admin_settings_thresholds")
        self.client.login(username="admin_settings_thresholds", password="StrongPass123")

        get_response = self.client.get(reverse("farm:settings_dispatch_thresholds"))
        post_response = self.client.post(reverse("farm:settings_dispatch_thresholds"))

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(post_response.status_code, 302)
        mocked_thresholds.assert_called_once()

    @patch("modules.v2.farm.views.dispatch_caseup_advantage_notifications")
    def test_farm_settings_advantage_dispatch_uses_post(self, mocked_advantage):
        mocked_advantage.return_value = []
        self._make_admin("admin_settings_advantage")
        self.client.login(username="admin_settings_advantage", password="StrongPass123")

        response = self.client.post(reverse("farm:settings_dispatch_advantage"))

        self.assertEqual(response.status_code, 302)
        mocked_advantage.assert_called_once()

    def test_farm_settings_dispatch_requires_csrf_when_enforced(self):
        self._make_admin("admin_settings_csrf")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="admin_settings_csrf", password="StrongPass123")

        response = csrf_client.post(reverse("farm:settings_dispatch_thresholds"))

        self.assertEqual(response.status_code, 403)
