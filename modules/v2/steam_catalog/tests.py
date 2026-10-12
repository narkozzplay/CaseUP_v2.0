from __future__ import annotations

import importlib
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.utils import timezone
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription
from modules.v2.system.services import update_module_states
from modules.v2.tg_notifier.models import TelegramNotifierConfig
from .models import (
    PriceDropAlertEvent,
    PriceDropAlertRule,
    SteamItem,
    SteamItemPriceHistory,
    StockReason,
    UserPriceAlertEvent,
    UserPriceAlertRule,
)
from .services import (
    detect_price_drop_alerts_for_category,
    dispatch_pending_price_drop_alerts,
    extract_market_hash_name,
    refresh_item_prices,
    upsert_from_market_input,
)

User = get_user_model()


class SteamCatalogTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _grant_plan(self, user, level: int):
        plan = SubscriptionPlan.objects.get(level=level)
        return UserSubscription.objects.create(user=user, plan=plan)

    def test_catalog_forbidden_for_plain_user(self):
        user = User.objects.create_user(username="user1", password="StrongPass123")
        self.client.login(username="user1", password="StrongPass123")

        response = self.client.get(reverse("steam_catalog:list"))
        self.assertEqual(response.status_code, 403)

    def test_catalog_available_for_admin_role(self):
        user = User.objects.create_user(username="admin1", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])

        self.client.login(username="admin1", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))
        self.assertEqual(response.status_code, 403)

    def test_catalog_forbidden_for_superuser_without_subscription(self):
        user = User.objects.create_user(
            username="catalog_super_no_sub",
            password="StrongPass123",
            is_staff=True,
            is_superuser=True,
        )

        self.client.login(username="catalog_super_no_sub", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Steam Catalog закрыт тарифом", status_code=403)

    def test_catalog_available_for_level_1_subscription_in_view_mode(self):
        user = User.objects.create_user(username="catalog_subscriber", password="StrongPass123")
        self._grant_plan(user, 1)
        item = SteamItem.objects.create(
            market_hash_name="Revolution Case",
            family_name="Revolution Case",
            name="Revolution Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("36.83"),
            is_active=True,
        )

        self.client.login(username="catalog_subscriber", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, item.group_name)
        self.assertContains(response, "режиме просмотра")
        self.assertNotContains(response, "Добавить предмет")

    def test_catalog_search_without_results_returns_404_page(self):
        user = User.objects.create_user(username="catalog_search_404", password="StrongPass123")
        self._grant_plan(user, 1)
        SteamItem.objects.create(
            market_hash_name="Revolution Case",
            family_name="Revolution Case",
            name="Revolution Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("36.83"),
            is_active=True,
        )

        self.client.login(username="catalog_search_404", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"), {"q": "Gamma"})

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Предметы не найдены", status_code=404)
        self.assertContains(response, 'value="Gamma"', status_code=404, html=False)

    def test_catalog_detail_shows_upgrade_state_for_level_1_subscription(self):
        user = User.objects.create_user(username="catalog_level1_detail", password="StrongPass123")
        self._grant_plan(user, 1)
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("51.20"),
            is_active=True,
        )

        self.client.login(username="catalog_level1_detail", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, item.group_name)
        self.assertContains(response, "Глубокая аналитика закрыта")
        self.assertContains(response, "Редактирование закрыто")

    def test_catalog_detail_uses_item_media_class_for_case_png(self):
        user = User.objects.create_user(username="catalog_case_png", password="StrongPass123")
        self._grant_plan(user, 1)
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            icon_url="https://example.com/kilowatt.png",
            is_active=True,
        )

        self.client.login(username="catalog_case_png", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="steam-catalog-detail__media"', html=False)
        self.assertContains(response, 'class="steam-catalog-detail__image"', html=False)
        self.assertContains(response, "https://example.com/kilowatt.png")
        self.assertNotContains(response, "profile-avatar--lg")

    def test_catalog_detail_uses_item_media_class_for_skin_png(self):
        user = User.objects.create_user(username="catalog_skin_png", password="StrongPass123")
        self._grant_plan(user, 1)
        item = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline",
            item_type=SteamItem.ItemType.SKIN,
            icon_url="https://example.com/redline.png",
            is_active=True,
        )

        self.client.login(username="catalog_skin_png", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="steam-catalog-detail__image"', html=False)
        self.assertContains(response, "https://example.com/redline.png")

    def test_catalog_detail_renders_image_fallback_without_icon(self):
        user = User.objects.create_user(username="catalog_no_icon", password="StrongPass123")
        self._grant_plan(user, 1)
        item = SteamItem.objects.create(
            market_hash_name="Mystery Item",
            family_name="Mystery Item",
            name="Mystery Item",
            item_type=SteamItem.ItemType.OTHER,
            icon_url="",
            is_active=True,
        )

        self.client.login(username="catalog_no_icon", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="steam-catalog-detail__image-fallback"', html=False)
        self.assertContains(response, "Other")

    def test_catalog_detail_allows_deep_analytics_for_level_2_subscription(self):
        user = User.objects.create_user(username="catalog_level2_detail", password="StrongPass123")
        self._grant_plan(user, 2)
        item = SteamItem.objects.create(
            market_hash_name="Gamma Case",
            family_name="Gamma Case",
            name="Gamma Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("42.00"),
            is_active=True,
        )
        SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("40.00"), source="manual")
        SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("42.00"), source="manual")

        self.client.login(username="catalog_level2_detail", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Период анализа")
        self.assertNotContains(response, "Глубокая аналитика закрыта")

    def test_catalog_subscription_respects_global_module_flag(self):
        user = User.objects.create_user(username="catalog_flag_user", password="StrongPass123")
        self._grant_plan(user, 1)

        update_module_states({"steam_catalog": False, "steam_trading": False})
        self.client.login(username="catalog_flag_user", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))

        self.assertEqual(response.status_code, 404)

    def test_stock_movement_updates_stock(self):
        item = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline",
            item_type=SteamItem.ItemType.SKIN,
            price_rub=Decimal("999.00"),
        )
        reason = StockReason.objects.create(name="manual income", direction=StockReason.Direction.IN)

        self.client.force_login(User.objects.create_user(username="staff1", password="StrongPass123", is_staff=True))
        response = self.client.post(
            reverse("steam_catalog:add_stock_move", args=[item.id]),
            {"reason": reason.id, "qty": 3, "note": "seed"},
        )
        self.assertEqual(response.status_code, 302)

        item.refresh_from_db()
        self.assertEqual(item.stock, 3)

    def test_extract_market_hash_name_from_listing_url(self):
        value = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        self.assertEqual(
            extract_market_hash_name(value),
            "AK-47 | Redline (Field-Tested)",
        )

    @patch("modules.v2.steam_catalog.services.fetch_price_rub_from_steam")
    @patch("modules.v2.steam_catalog.services.try_fetch_listing_render_data")
    @patch("modules.v2.steam_catalog.services.try_fetch_listing_metadata")
    def test_upsert_skin_creates_all_variants_with_individual_prices(self, metadata_mock, render_mock, price_mock):
        metadata_mock.return_value = {
            "name": "AK-47 | Redline (Field-Tested)",
            "icon_url": "https://example.com/redline.png",
        }
        render_mock.return_value = {
            "rarity": "Classified",
            "exterior": "Field-Tested",
            "icon_url": "https://example.com/redline.png",
        }

        price_map = {
            "AK-47 | Redline (Factory New)": Decimal("2100.00"),
            "AK-47 | Redline (Minimal Wear)": Decimal("1800.00"),
            "AK-47 | Redline (Field-Tested)": Decimal("1200.00"),
            "AK-47 | Redline (Well-Worn)": Decimal("950.00"),
            "AK-47 | Redline (Battle-Scarred)": Decimal("800.00"),
            "StatTrak™ AK-47 | Redline (Factory New)": Decimal("4900.00"),
            "StatTrak™ AK-47 | Redline (Minimal Wear)": Decimal("4200.00"),
            "StatTrak™ AK-47 | Redline (Field-Tested)": Decimal("3100.00"),
            "StatTrak™ AK-47 | Redline (Well-Worn)": Decimal("2500.00"),
            "StatTrak™ AK-47 | Redline (Battle-Scarred)": Decimal("1900.00"),
        }

        def price_side_effect(*, market_hash_name, appid=730, timeout=10.0):
            return price_map.get(market_hash_name)

        price_mock.side_effect = price_side_effect

        result = upsert_from_market_input(
            value="AK-47 | Redline (Field-Tested)",
            stock=7,
        )

        self.assertEqual(result.variants_created, 10)
        self.assertEqual(
            SteamItem.objects.filter(family_name="AK-47 | Redline").count(),
            10,
        )

        anchor = SteamItem.objects.get(market_hash_name="AK-47 | Redline (Field-Tested)")
        self.assertEqual(anchor.stock, 7)
        self.assertEqual(anchor.item_type, SteamItem.ItemType.SKIN)
        self.assertEqual(anchor.weapon, "AK-47")
        self.assertEqual(anchor.rarity_key, "classified")
        self.assertEqual(anchor.price_rub, Decimal("1200.00"))
        self.assertEqual(anchor.family_name, "AK-47 | Redline")

        stattrak = SteamItem.objects.get(
            market_hash_name="StatTrak™ AK-47 | Redline (Factory New)"
        )
        self.assertEqual(stattrak.price_rub, Decimal("4900.00"))
        self.assertEqual(stattrak.stock, 0)
        self.assertTrue(stattrak.is_stattrak)

    @patch("modules.v2.steam_catalog.services.fetch_price_rub_from_steam")
    @patch("modules.v2.steam_catalog.services.try_fetch_listing_render_data")
    @patch("modules.v2.steam_catalog.services.try_fetch_listing_metadata")
    def test_upsert_case_creates_single_item(self, metadata_mock, render_mock, price_mock):
        metadata_mock.return_value = {
            "name": "Dreams & Nightmares Case",
            "icon_url": "https://example.com/case.png",
        }
        render_mock.return_value = {
            "rarity": "",
            "exterior": "",
            "icon_url": "https://example.com/case.png",
        }
        price_mock.return_value = Decimal("222.00")

        result = upsert_from_market_input(
            value="Dreams & Nightmares Case",
            stock=2,
        )

        self.assertEqual(result.variants_created, 0)
        item = SteamItem.objects.get(market_hash_name="Dreams & Nightmares Case")
        self.assertEqual(item.item_type, SteamItem.ItemType.CASE)
        self.assertEqual(item.stock, 2)
        self.assertEqual(item.price_rub, Decimal("222.00"))
        self.assertEqual(item.family_name, "Dreams & Nightmares Case")

    def test_update_name_ru_view_updates_all_skin_variants(self):
        user = User.objects.create_user(username="admin2", password="StrongPass123", is_staff=True)
        first = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Factory New)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline (Factory New)",
            item_type=SteamItem.ItemType.SKIN,
        )
        second = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline (Field-Tested)",
            item_type=SteamItem.ItemType.SKIN,
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("steam_catalog:update_name_ru", args=[first.id]),
            {"name_ru": "АК-47 | Красная линия"},
        )
        self.assertEqual(response.status_code, 302)

        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(first.name_ru, "АК-47 | Красная линия")
        self.assertEqual(second.name_ru, "АК-47 | Красная линия")

    @patch("modules.v2.steam_catalog.services.fetch_price_rub_from_steam")
    def test_refresh_item_price_updates_price_and_writes_history(self, price_mock):
        item = SteamItem.objects.create(
            market_hash_name="Sealed Dead Hand Terminal",
            family_name="Sealed Dead Hand Terminal",
            name="Sealed Dead Hand Terminal",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("1370.10"),
        )
        price_mock.return_value = Decimal("1499.99")

        result = refresh_item_prices(item, include_family=False, source="manual")

        item.refresh_from_db()
        self.assertEqual(result.touched_count, 1)
        self.assertEqual(result.updated_count, 1)
        self.assertEqual(result.history_written, 1)
        self.assertEqual(item.price_rub, Decimal("1499.99"))
        self.assertEqual(item.price_history.count(), 1)

    @patch("modules.v2.steam_catalog.services.fetch_price_rub_from_steam")
    def test_refresh_item_price_does_not_duplicate_same_history_value(self, price_mock):
        item = SteamItem.objects.create(
            market_hash_name="Sealed Dead Hand Terminal",
            family_name="Sealed Dead Hand Terminal",
            name="Sealed Dead Hand Terminal",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("1370.10"),
        )
        SteamItemPriceHistory.objects.create(
            item=item,
            price_rub=Decimal("1370.10"),
            source="manual",
        )
        price_mock.return_value = Decimal("1370.10")

        result = refresh_item_prices(item, include_family=False, source="manual")

        self.assertEqual(result.touched_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.history_written, 0)
        self.assertEqual(item.price_history.count(), 1)

    @patch("modules.v2.steam_catalog.services.fetch_price_rub_from_steam")
    def test_update_price_view_available_for_staff(self, price_mock):
        user = User.objects.create_user(username="staff2", password="StrongPass123", is_staff=True)
        item = SteamItem.objects.create(
            market_hash_name="Sealed Dead Hand Terminal",
            family_name="Sealed Dead Hand Terminal",
            name="Sealed Dead Hand Terminal",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("1370.10"),
        )
        price_mock.return_value = Decimal("1400.00")

        self.client.force_login(user)
        response = self.client.post(reverse("steam_catalog:update_price", args=[item.id]))

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.price_rub, Decimal("1400.00"))
        self.assertEqual(item.price_history.count(), 1)

    def test_detail_week_range_chart_shows_all_raw_points(self):
        user = User.objects.create_user(username="staff_chart_week", password="StrongPass123", is_staff=True)
        item = SteamItem.objects.create(
            market_hash_name="Sealed Dead Hand Terminal",
            family_name="Sealed Dead Hand Terminal",
            name="Sealed Dead Hand Terminal",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("100.00"),
        )

        base_now = timezone.localtime(timezone.now()).replace(hour=12, minute=30, second=0, microsecond=0)

        first = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("100.00"), source="manual")
        second = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("150.00"), source="manual")
        third = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("130.00"), source="manual")

        SteamItemPriceHistory.objects.filter(pk=first.pk).update(created_at=base_now - timedelta(days=2, hours=5))
        SteamItemPriceHistory.objects.filter(pk=second.pk).update(created_at=base_now - timedelta(days=2, hours=1))
        SteamItemPriceHistory.objects.filter(pk=third.pk).update(created_at=base_now - timedelta(days=1, hours=2))

        self._grant_plan(user, 2)
        self.client.force_login(user)
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]), {"range": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_price_history_range"], "week")
        self.assertEqual(len(response.context["price_chart"]["points"]), 3)

        point_values = [point["value"] for point in response.context["price_chart"]["points"]]
        self.assertEqual(point_values, [Decimal("100.00"), Decimal("150.00"), Decimal("130.00")])


    def test_detail_day_range_chart_shows_all_raw_points(self):
        user = User.objects.create_user(username="staff_chart_day", password="StrongPass123", is_staff=True)
        item = SteamItem.objects.create(
            market_hash_name="Sealed Dead Hand Terminal 2",
            family_name="Sealed Dead Hand Terminal 2",
            name="Sealed Dead Hand Terminal 2",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("100.00"),
        )
    
        base_now = timezone.localtime(timezone.now()).replace(minute=30, second=0, microsecond=0)
    
        first = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("100.00"), source="manual")
        second = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("140.00"), source="manual")
        third = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("130.00"), source="manual")
    
        # Оба попадают в один и тот же часовой бакет: 3 часа назад, 10 и 20 минут
        SteamItemPriceHistory.objects.filter(pk=first.pk).update(created_at=base_now - timedelta(hours=3, minutes=20))
        SteamItemPriceHistory.objects.filter(pk=second.pk).update(created_at=base_now - timedelta(hours=3, minutes=10))
    
        # Отдельный часовой бакет
        SteamItemPriceHistory.objects.filter(pk=third.pk).update(created_at=base_now - timedelta(hours=1, minutes=10))
    
        self._grant_plan(user, 2)
        self.client.force_login(user)
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]), {"range": "day"})
    
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_price_history_range"], "day")
        self.assertEqual(len(response.context["price_chart"]["points"]), 3)
    
        point_values = [point["value"] for point in response.context["price_chart"]["points"]]
        self.assertEqual(point_values, [Decimal("100.00"), Decimal("140.00"), Decimal("130.00")])


    def test_detail_week_range_analytics_uses_raw_history_not_bucket_maximums(self):
        user = User.objects.create_user(username="staff_chart_analytics", password="StrongPass123", is_staff=True)
        item = SteamItem.objects.create(
            market_hash_name="Analytics Probe",
            family_name="Analytics Probe",
            name="Analytics Probe",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("100.00"),
        )

        base_now = timezone.localtime(timezone.now()).replace(hour=12, minute=30, second=0, microsecond=0)

        first = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("142.00"), source="manual")
        second = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("150.00"), source="manual")
        third = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("149.00"), source="manual")

        SteamItemPriceHistory.objects.filter(pk=first.pk).update(created_at=base_now - timedelta(days=2, hours=5))
        SteamItemPriceHistory.objects.filter(pk=second.pk).update(created_at=base_now - timedelta(days=2, hours=1))
        SteamItemPriceHistory.objects.filter(pk=third.pk).update(created_at=base_now - timedelta(days=1, hours=2))

        self._grant_plan(user, 2)
        self.client.force_login(user)
        response = self.client.get(reverse("steam_catalog:detail", args=[item.id]), {"range": "week"})

        self.assertEqual(response.status_code, 200)

        analytics = response.context["price_analytics"]
        chart = response.context["price_chart"]

        self.assertEqual(analytics["first_value"], Decimal("142.00"))
        self.assertEqual(analytics["current_value"], Decimal("149.00"))
        self.assertEqual(analytics["min_value"], Decimal("142.00"))
        self.assertEqual(analytics["max_value"], Decimal("150.00"))
        self.assertEqual(analytics["trend_delta"], Decimal("7.00"))
        self.assertEqual(analytics["trend_direction"], "up")

        point_values = [point["value"] for point in chart["points"]]
        self.assertEqual(point_values, [Decimal("142.00"), Decimal("150.00"), Decimal("149.00")])


    def test_catalog_uses_grid_columns_setting(self):
        user = User.objects.create_user(username="admin_grid", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])

        SteamItem.objects.create(
            market_hash_name="Recoil Case",
            family_name="Recoil Case",
            name="Recoil Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("100.00"),
        )

        self._grant_plan(user, 1)
        self.client.login(username="admin_grid", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "--catalog-columns: 7;")


    def test_catalog_card_contains_hover_overlay_content(self):
        user = User.objects.create_user(username="admin_overlay", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])

        SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("123.45"),
        )

        self._grant_plan(user, 1)
        self.client.login(username="admin_overlay", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'catalog-card__overlay')
        self.assertContains(response, 'catalog-card__title')
        self.assertContains(response, 'catalog-card__price')

    def test_detail_day_range_analytics_contains_peak_drawdown(self):
        user = User.objects.create_user(
            username="staff_chart_drawdown",
            password="StrongPass123",
            is_staff=True,
        )
        item = SteamItem.objects.create(
            market_hash_name="Drawdown Probe",
            family_name="Drawdown Probe",
            name="Drawdown Probe",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("100.00"),
        )
    
        base_now = timezone.localtime(timezone.now()).replace(
            hour=12,
            minute=30,
            second=0,
            microsecond=0,
        )
    
        first = SteamItemPriceHistory.objects.create(
            item=item,
            price_rub=Decimal("1543.21"),
            source="manual",
        )
        peak = SteamItemPriceHistory.objects.create(
            item=item,
            price_rub=Decimal("1753.51"),
            source="manual",
        )
        current = SteamItemPriceHistory.objects.create(
            item=item,
            price_rub=Decimal("1467.09"),
            source="manual",
        )
    
        SteamItemPriceHistory.objects.filter(pk=first.pk).update(
            created_at=base_now - timedelta(hours=11, minutes=28)
        )
        SteamItemPriceHistory.objects.filter(pk=peak.pk).update(
            created_at=base_now - timedelta(hours=3, minutes=11)
        )
        SteamItemPriceHistory.objects.filter(pk=current.pk).update(
            created_at=base_now - timedelta(minutes=11)
        )
    
        self._grant_plan(user, 2)
        self.client.force_login(user)
        response = self.client.get(
            reverse("steam_catalog:detail", args=[item.id]),
            {"range": "day"},
        )
    
        self.assertEqual(response.status_code, 200)
    
        analytics = response.context["price_analytics"]
    
        self.assertEqual(analytics["first_value"], Decimal("1543.21"))
        self.assertEqual(analytics["current_value"], Decimal("1467.09"))
        self.assertEqual(analytics["max_value"], Decimal("1753.51"))
        self.assertEqual(analytics["trend_delta"], Decimal("-76.12"))
        self.assertEqual(analytics["drawdown_delta"], Decimal("-286.42"))
        self.assertEqual(analytics["drawdown_direction"], "down")
        self.assertTrue(analytics["peak_created_label"])

    def test_detect_price_drop_alert_event_for_category_rule(self):
        item = SteamItem.objects.create(
            market_hash_name="Sealed Dead Hand Terminal",
            family_name="Sealed Dead Hand Terminal",
            name="Sealed Dead Hand Terminal",
            item_type=SteamItem.ItemType.OTHER,
            price_rub=Decimal("1467.09"),
        )
        rule = PriceDropAlertRule.objects.create(
            item_type=SteamItem.ItemType.OTHER,
            is_enabled=True,
            direction=PriceDropAlertRule.Direction.DOWN,
            lookback_hours=6,
            min_drop_rub=Decimal("200.00"),
            min_drop_percent=Decimal("10.00"),
            repeat_step_rub=Decimal("50.00"),
            repeat_step_percent=Decimal("5.00"),
            cooldown_minutes=360,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )

        base_now = timezone.now().replace(second=0, microsecond=0)

        first = SteamItemPriceHistory.objects.create(
            item=item,
            price_rub=Decimal("1585.31"),
            source="celery:other",
        )
        peak = SteamItemPriceHistory.objects.create(
            item=item,
            price_rub=Decimal("1753.51"),
            source="celery:other",
        )
        current = SteamItemPriceHistory.objects.create(
            item=item,
            price_rub=Decimal("1467.09"),
            source="celery:other",
        )

        SteamItemPriceHistory.objects.filter(pk=first.pk).update(
            created_at=base_now - timedelta(hours=4)
        )
        SteamItemPriceHistory.objects.filter(pk=peak.pk).update(
            created_at=base_now - timedelta(hours=3, minutes=11)
        )
        SteamItemPriceHistory.objects.filter(pk=current.pk).update(
            created_at=base_now - timedelta(minutes=11)
        )

        created_count = detect_price_drop_alerts_for_category(
            SteamItem.ItemType.OTHER,
            trigger_source="beat",
        )

        self.assertEqual(created_count, 1)

        event = PriceDropAlertEvent.objects.get(rule=rule, item=item)
        self.assertEqual(event.status, PriceDropAlertEvent.Status.PENDING)
        self.assertEqual(event.notify_transport, PriceDropAlertRule.NotifyTransport.TELEGRAM)
        self.assertEqual(event.direction, PriceDropAlertEvent.Direction.DOWN)
        self.assertEqual(event.baseline_price_rub, Decimal("1753.51"))
        self.assertEqual(event.drop_rub, Decimal("-286.42"))
        self.assertEqual(event.drop_percent, Decimal("-16.33"))
        self.assertEqual(event.step_index, 2)

    @patch("modules.v2.steam_catalog.services.urllib.request.urlopen")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_CHAT_ID", "123456")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_BOT_TOKEN", "token-123")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_NOTIFICATIONS_ENABLED", True)
    def test_dispatch_pending_price_drop_alerts_sends_telegram_message_legacy_case(self, urlopen_mock):
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("1467.09"),
        )
        rule = PriceDropAlertRule.objects.create(
            item_type=SteamItem.ItemType.CASE,
            is_enabled=True,
            lookback_hours=4,
            min_drop_rub=Decimal("200.00"),
            min_drop_percent=Decimal("10.00"),
            cooldown_minutes=360,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )
        event = PriceDropAlertEvent.objects.create(
            rule=rule,
            item=item,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
            trigger_source="beat",
            status=PriceDropAlertEvent.Status.PENDING,
            direction=PriceDropAlertEvent.Direction.DOWN,
            window_started_at=timezone.now() - timedelta(hours=4),
            baseline_at=timezone.now() - timedelta(hours=3),
            current_at=timezone.now(),
            baseline_price_rub=Decimal("1753.51"),
            current_price_rub=Decimal("1467.09"),
            drop_rub=Decimal("-286.42"),
            drop_percent=Decimal("-16.33"),
            step_index=2,
            payload={"market_hash_name": item.market_hash_name},
        )
    
        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "ok": True,
                "result": {"message_id": 777},
            }
        ).encode("utf-8")
        urlopen_mock.return_value.__enter__.return_value = mocked_response
    
        sent_count = dispatch_pending_price_drop_alerts(limit=10)
    
        self.assertEqual(sent_count, 1)
    
        event.refresh_from_db()
        self.assertEqual(event.status, PriceDropAlertEvent.Status.SENT)
        self.assertIsNotNone(event.sent_at)
        self.assertEqual(event.payload["telegram_response"]["result"]["message_id"], 777)
        self.assertEqual(urlopen_mock.call_count, 1)

    def test_detect_price_movement_alert_event_for_growth_rule(self):
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("120.00"),
        )
        rule = PriceDropAlertRule.objects.create(
            item_type=SteamItem.ItemType.CASE,
            is_enabled=True,
            direction=PriceDropAlertRule.Direction.UP,
            lookback_hours=6,
            min_drop_rub=Decimal("10.00"),
            min_drop_percent=Decimal("10.00"),
            repeat_step_rub=Decimal("5.00"),
            repeat_step_percent=Decimal("5.00"),
            cooldown_minutes=0,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )

        base_now = timezone.now().replace(second=0, microsecond=0)
        first = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("100.00"), source="celery:case")
        mid = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("109.00"), source="celery:case")
        current = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("115.00"), source="celery:case")
        SteamItemPriceHistory.objects.filter(pk=first.pk).update(created_at=base_now - timedelta(hours=2))
        SteamItemPriceHistory.objects.filter(pk=mid.pk).update(created_at=base_now - timedelta(hours=1))
        SteamItemPriceHistory.objects.filter(pk=current.pk).update(created_at=base_now - timedelta(minutes=5))

        created_count = detect_price_drop_alerts_for_category(SteamItem.ItemType.CASE, trigger_source="beat")
        self.assertEqual(created_count, 1)

        event = PriceDropAlertEvent.objects.get(rule=rule, item=item)
        self.assertEqual(event.direction, PriceDropAlertEvent.Direction.UP)
        self.assertEqual(event.baseline_price_rub, Decimal("100.00"))
        self.assertEqual(event.current_price_rub, Decimal("115.00"))
        self.assertEqual(event.drop_rub, Decimal("15.00"))
        self.assertEqual(event.drop_percent, Decimal("15.00"))
        self.assertEqual(event.step_index, 2)

    def test_detect_price_movement_alert_creates_next_step_only_once(self):
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("120.00"),
        )
        rule = PriceDropAlertRule.objects.create(
            item_type=SteamItem.ItemType.CASE,
            is_enabled=True,
            direction=PriceDropAlertRule.Direction.UP,
            lookback_hours=6,
            min_drop_rub=Decimal("10.00"),
            min_drop_percent=Decimal("10.00"),
            repeat_step_rub=Decimal("5.00"),
            repeat_step_percent=Decimal("5.00"),
            cooldown_minutes=0,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )

        base_now = timezone.now().replace(second=0, microsecond=0)
        first = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("100.00"), source="celery:case")
        current = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("115.00"), source="celery:case")
        SteamItemPriceHistory.objects.filter(pk=first.pk).update(created_at=base_now - timedelta(hours=2))
        SteamItemPriceHistory.objects.filter(pk=current.pk).update(created_at=base_now - timedelta(minutes=30))

        created_count = detect_price_drop_alerts_for_category(SteamItem.ItemType.CASE, trigger_source="beat")
        self.assertEqual(created_count, 1)
        self.assertEqual(PriceDropAlertEvent.objects.filter(rule=rule, item=item).count(), 1)

        created_count = detect_price_drop_alerts_for_category(SteamItem.ItemType.CASE, trigger_source="beat")
        self.assertEqual(created_count, 0)
        self.assertEqual(PriceDropAlertEvent.objects.filter(rule=rule, item=item).count(), 1)

        higher = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal("120.00"), source="celery:case")
        SteamItemPriceHistory.objects.filter(pk=higher.pk).update(created_at=base_now - timedelta(minutes=5))

        created_count = detect_price_drop_alerts_for_category(SteamItem.ItemType.CASE, trigger_source="beat")
        self.assertEqual(created_count, 1)

        latest_event = PriceDropAlertEvent.objects.filter(rule=rule, item=item).order_by("-created_at", "-id").first()
        self.assertEqual(latest_event.step_index, 3)
        self.assertEqual(latest_event.drop_rub, Decimal("20.00"))
        self.assertEqual(latest_event.drop_percent, Decimal("20.00"))

    @patch("modules.v2.steam_catalog.services.urllib.request.urlopen")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_CHAT_ID", "123456")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_BOT_TOKEN", "token-123")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_NOTIFICATIONS_ENABLED", True)
    def test_dispatch_pending_price_drop_alerts_sends_telegram_message(self, urlopen_mock):
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("1467.09"),
        )
        rule = PriceDropAlertRule.objects.create(
            item_type=SteamItem.ItemType.CASE,
            is_enabled=True,
            direction=PriceDropAlertRule.Direction.BOTH,
            lookback_hours=4,
            min_drop_rub=Decimal("200.00"),
            min_drop_percent=Decimal("10.00"),
            repeat_step_rub=Decimal("50.00"),
            repeat_step_percent=Decimal("5.00"),
            cooldown_minutes=360,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )
        event = PriceDropAlertEvent.objects.create(
            rule=rule,
            item=item,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
            trigger_source="beat",
            status=PriceDropAlertEvent.Status.PENDING,
            direction=PriceDropAlertEvent.Direction.DOWN,
            window_started_at=timezone.now() - timedelta(hours=4),
            baseline_at=timezone.now() - timedelta(hours=3),
            current_at=timezone.now(),
            baseline_price_rub=Decimal("1753.51"),
            current_price_rub=Decimal("1467.09"),
            drop_rub=Decimal("-286.42"),
            drop_percent=Decimal("-16.33"),
            step_index=2,
            payload={"market_hash_name": item.market_hash_name},
        )
    
        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "ok": True,
                "result": {"message_id": 777},
            }
        ).encode("utf-8")
        urlopen_mock.return_value.__enter__.return_value = mocked_response
    
        sent_count = dispatch_pending_price_drop_alerts(limit=10)
    
        self.assertEqual(sent_count, 1)
    
        event.refresh_from_db()
        self.assertEqual(event.status, PriceDropAlertEvent.Status.SENT)
        self.assertIsNotNone(event.sent_at)
        self.assertEqual(event.payload["telegram_response"]["result"]["message_id"], 777)
        self.assertEqual(urlopen_mock.call_count, 1)

    @patch("modules.v2.steam_catalog.services.urllib.request.urlopen")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_BOT_TOKEN", "token-123")
    @patch("modules.v2.steam_catalog.services.settings.TELEGRAM_NOTIFICATIONS_ENABLED", True)
    def test_dispatch_pending_price_drop_alerts_uses_linked_profile_chat_ids(self, urlopen_mock):
        user = User.objects.create_user(username="tg_alert_user", password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.telegram_chat_id = "7151390596"
        profile.role = UserRole.USER
        profile.save(update_fields=["telegram_chat_id", "role"])
    
        TelegramNotifierConfig.objects.create(
            singleton_key=1,
            is_enabled=True,
            bot_token="token-123",
            bot_username="caseup_bot",
            default_chat_id="123456",
            investment_message_template="Предмет: {{ item_name }}",
        )
    
        item = SteamItem.objects.create(
            market_hash_name="Kilowatt Case",
            family_name="Kilowatt Case",
            name="Kilowatt Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("1467.09"),
        )
        rule = PriceDropAlertRule.objects.create(
            item_type=SteamItem.ItemType.CASE,
            is_enabled=True,
            direction=PriceDropAlertRule.Direction.DOWN,
            lookback_hours=4,
            min_drop_rub=Decimal("200.00"),
            min_drop_percent=Decimal("10.00"),
            repeat_step_rub=Decimal("50.00"),
            repeat_step_percent=Decimal("5.00"),
            cooldown_minutes=360,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
        )
        event = PriceDropAlertEvent.objects.create(
            rule=rule,
            item=item,
            notify_transport=PriceDropAlertRule.NotifyTransport.TELEGRAM,
            trigger_source="beat",
            status=PriceDropAlertEvent.Status.PENDING,
            direction=PriceDropAlertEvent.Direction.DOWN,
            window_started_at=timezone.now() - timedelta(hours=4),
            baseline_at=timezone.now() - timedelta(hours=3),
            current_at=timezone.now(),
            baseline_price_rub=Decimal("1753.51"),
            current_price_rub=Decimal("1467.09"),
            drop_rub=Decimal("-286.42"),
            drop_percent=Decimal("-16.33"),
            step_index=2,
            payload={"market_hash_name": item.market_hash_name},
        )
    
        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "ok": True,
                "result": {"message_id": 777},
            }
        ).encode("utf-8")
        urlopen_mock.return_value.__enter__.return_value = mocked_response
    
        sent_count = dispatch_pending_price_drop_alerts(limit=10)
    
        self.assertEqual(sent_count, 1)
    
        request_obj = urlopen_mock.call_args.args[0]
        request_body = json.loads(request_obj.data.decode("utf-8"))
    
        self.assertEqual(request_body["chat_id"], "7151390596")
        self.assertNotEqual(request_body["chat_id"], "123456")
    
        event.refresh_from_db()
        self.assertEqual(event.status, PriceDropAlertEvent.Status.SENT)
        self.assertIn("7151390596", event.payload["sent_chat_ids"])


class SteamCatalogImportSmokeTests(TestCase):

    def test_services_package_exports_backward_compatible_api(self):
        services_module = importlib.import_module("modules.v2.steam_catalog.services")

        self.assertTrue(callable(services_module.upsert_from_market_input))
        self.assertTrue(callable(services_module.refresh_item_prices))
        self.assertTrue(callable(services_module.dispatch_pending_price_drop_alerts))
        self.assertTrue(hasattr(services_module, "_telegram_send_message"))



class UserPriceAlertRuleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alert_user", password="StrongPass123")
        self.client = Client()
        self.client.login(username="alert_user", password="StrongPass123")
        self.item = SteamItem.objects.create(
            market_hash_name="Revolution Case",
            family_name="Revolution Case",
            name="Revolution Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("180.00"),
        )

    def _make_history(self, prices):
        base_time = timezone.now() - timedelta(hours=5)
        for idx, price in enumerate(prices):
            row = SteamItemPriceHistory.objects.create(item=self.item, price_rub=Decimal(price), source="test")
            SteamItemPriceHistory.objects.filter(pk=row.pk).update(created_at=base_time + timedelta(hours=idx))

    def test_user_can_create_rule_view(self):
        response = self.client.post(reverse("steam_catalog:alert_rule_create"), {
            "name": "Case drop",
            "scope_type": UserPriceAlertRule.ScopeType.ITEM,
            "item": self.item.id,
            "item_type": "",
            "family_name": "",
            "direction": UserPriceAlertRule.Direction.DOWN,
            "reference_point": UserPriceAlertRule.ReferencePoint.WINDOW_MAX,
            "threshold_mode": UserPriceAlertRule.ThresholdMode.AND,
            "window_kind": UserPriceAlertRule.WindowKind.HOURS,
            "window_value": 24,
            "min_change_rub": "20.00",
            "min_change_percent": "10.00",
            "cooldown_minutes": 10,
            "repeat_step_rub": "5.00",
            "repeat_step_percent": "2.00",
            "repeat_mode": UserPriceAlertRule.RepeatMode.STEP,
            "transport": UserPriceAlertRule.Transport.TELEGRAM,
            "delivery_target": UserPriceAlertRule.DeliveryTarget.USER_CHAT,
            "status": UserPriceAlertRule.Status.ACTIVE,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(UserPriceAlertRule.objects.filter(user=self.user).count(), 1)

    def test_detect_user_alert_from_window_max(self):
        from .services.user_alerts import evaluate_user_price_alerts_for_item_type
        self._make_history(["200.00", "210.00", "205.00", "170.00"])
        rule = UserPriceAlertRule.objects.create(
            user=self.user,
            name="Drop from peak",
            scope_type=UserPriceAlertRule.ScopeType.ITEM,
            item=self.item,
            direction=UserPriceAlertRule.Direction.DOWN,
            reference_point=UserPriceAlertRule.ReferencePoint.WINDOW_MAX,
            threshold_mode=UserPriceAlertRule.ThresholdMode.AND,
            window_kind=UserPriceAlertRule.WindowKind.HOURS,
            window_value=24,
            min_change_rub=Decimal("30.00"),
            min_change_percent=Decimal("10.00"),
            repeat_mode=UserPriceAlertRule.RepeatMode.ONCE,
            status=UserPriceAlertRule.Status.ACTIVE,
        )
        created = evaluate_user_price_alerts_for_item_type(SteamItem.ItemType.CASE, trigger_source="beat")
        self.assertEqual(created, 1)
        event = UserPriceAlertEvent.objects.get(rule=rule)
        self.assertEqual(event.baseline_price_rub, Decimal("210.00"))
        self.assertEqual(event.current_price_rub, Decimal("170.00"))

    def test_dispatch_user_alert_uses_profile_chat(self):
        from .services.user_alerts import dispatch_pending_user_price_alerts
        profile = UserProfile.objects.get(user=self.user)
        profile.telegram_chat_id = "7151390596"
        profile.save(update_fields=["telegram_chat_id"])
        rule = UserPriceAlertRule.objects.create(
            user=self.user,
            name="Dispatch me",
            scope_type=UserPriceAlertRule.ScopeType.ITEM,
            item=self.item,
            direction=UserPriceAlertRule.Direction.DOWN,
            reference_point=UserPriceAlertRule.ReferencePoint.WINDOW_MAX,
            threshold_mode=UserPriceAlertRule.ThresholdMode.RUB,
            window_kind=UserPriceAlertRule.WindowKind.HOURS,
            window_value=24,
            min_change_rub=Decimal("30.00"),
            min_change_percent=Decimal("0.00"),
            repeat_mode=UserPriceAlertRule.RepeatMode.ONCE,
            status=UserPriceAlertRule.Status.ACTIVE,
        )
        event = UserPriceAlertEvent.objects.create(
            rule=rule, user=self.user, item=self.item,
            direction=UserPriceAlertRule.Direction.DOWN,
            reference_point=rule.reference_point,
            reference_at=timezone.now() - timedelta(hours=2),
            current_at=timezone.now() - timedelta(hours=1),
            window_started_at=timezone.now() - timedelta(hours=3),
            baseline_price_rub=Decimal("210.00"), current_price_rub=Decimal("170.00"),
            change_rub=Decimal("-40.00"), change_percent=Decimal("-19.05"),
            dedupe_key="dispatch-test",
        )
        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEF", default_chat_id="-100555", investment_message_template="Предмет: {{ item_name }}")
        with patch("modules.v2.tg_notifier.services._telegram_api_request") as api_mock:
            api_mock.return_value = {"ok": True, "result": {"message_id": 1}}
            sent = dispatch_pending_user_price_alerts(limit=10)
        self.assertEqual(sent, 1)
        event.refresh_from_db()
        self.assertEqual(event.delivery_status, UserPriceAlertEvent.Status.SENT)
        self.assertEqual(event.sent_to, "7151390596")

    def test_user_cannot_edit_foreign_rule(self):
        foreign = User.objects.create_user(username="foreign", password="StrongPass123")
        rule = UserPriceAlertRule.objects.create(
            user=foreign,
            name="Foreign",
            scope_type=UserPriceAlertRule.ScopeType.ITEM_TYPE,
            item_type=SteamItem.ItemType.CASE,
            direction=UserPriceAlertRule.Direction.DOWN,
            reference_point=UserPriceAlertRule.ReferencePoint.WINDOW_MAX,
            threshold_mode=UserPriceAlertRule.ThresholdMode.RUB,
            window_kind=UserPriceAlertRule.WindowKind.HOURS,
            window_value=12,
            min_change_rub=Decimal("10.00"),
            min_change_percent=Decimal("0.00"),
            status=UserPriceAlertRule.Status.ACTIVE,
        )
        response = self.client.get(reverse("steam_catalog:alert_rule_edit", args=[rule.id]))
        self.assertEqual(response.status_code, 404)

from modules.v2.store.models import ThemeStoreListing
from modules.v2.store.services import (
    STEAM_CATALOG_DETAIL_CONTENT_SLOT,
    STEAM_CATALOG_DETAIL_MODE_SLOT,
    activate_module_slot,
    purchase_store_listing_with_balance,
)


class SteamCatalogStoreModuleSlotTests(TestCase):
    def setUp(self):
        self.client = Client()
        update_module_states({"steam_catalog": True, "store": True, "subscriptions": True})

        self.user = User.objects.create_user(username="catalog_store_slot", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=1)
        UserSubscription.objects.create(user=self.user, plan=plan)

        self.item = SteamItem.objects.create(
            market_hash_name="Store Slot Case",
            family_name="Store Slot Case",
            name="Store Slot Case",
            item_type=SteamItem.ItemType.CASE,
            price_rub=Decimal("50.00"),
            is_active=True,
        )

        self.module_listing = ThemeStoreListing.objects.create(
            key="steam-slot-module-listing",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_update_plugin_price_graphics",
            title="Steam slot module",
            short_description="slot module",
            full_description="slot module",
            author="CaseUP",
            price_rub=Decimal("10.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
        )
        self.mode_module_listing = ThemeStoreListing.objects.create(
            key="steam-slot-mode-module-listing",
            category=ThemeStoreListing.Category.MODULES,
            module_key="steam_catalog_v2",
            title="Steam slot mode module",
            short_description="slot mode module",
            full_description="slot mode module",
            author="CaseUP",
            price_rub=Decimal("15.00"),
            status=ThemeStoreListing.Status.PUBLISHED,
            is_public=True,
        )

    def test_detail_renders_without_module_override_by_default(self):
        self.client.login(username="catalog_store_slot", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[self.item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Price Graphics Plus")

    def test_detail_renders_with_runtime_module_override(self):
        adjust_balance(user=self.user, delta=Decimal("100.00"), reason="topup", amount_snapshot=Decimal("100.00"))
        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        activate_module_slot(user=self.user, listing_id=self.module_listing.id, slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT)

        self.client.login(username="catalog_store_slot", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[self.item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Price Graphics Plus")
        self.assertContains(response, "data-steam-update-plugin-price-graphics")

    def test_detail_renders_with_mode_slot_and_prefers_v2_foundation(self):
        adjust_balance(user=self.user, delta=Decimal("200.00"), reason="topup", amount_snapshot=Decimal("200.00"))
        purchase_store_listing_with_balance(user=self.user, listing_id=self.module_listing.id)
        activate_module_slot(user=self.user, listing_id=self.module_listing.id, slot=STEAM_CATALOG_DETAIL_CONTENT_SLOT)
        purchase_store_listing_with_balance(user=self.user, listing_id=self.mode_module_listing.id)
        activate_module_slot(user=self.user, listing_id=self.mode_module_listing.id, slot=STEAM_CATALOG_DETAIL_MODE_SLOT)

        self.client.login(username="catalog_store_slot", password="StrongPass123")
        response = self.client.get(reverse("steam_catalog:detail", args=[self.item.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Steam Catalog 2.0 foundation")


class SteamMarketSearchTests(TestCase):
    PAYLOAD = {
        "success": True,
        "results": [
            {
                "name": "AK-47 | Redline (Field-Tested)",
                "hash_name": "AK-47 | Redline (Field-Tested)",
                "sell_listings": 812,
                "sell_price_text": "$12.40",
                "asset_description": {"icon_url": "abc123", "type": "Classified Rifle"},
            },
            {
                "name": "Kilowatt Case",
                "hash_name": "Kilowatt Case",
                "sell_listings": 40210,
                "sell_price_text": "$0.62",
                "asset_description": {"icon_url": "case42", "type": "Base Grade Container"},
            },
        ],
    }

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        User = get_user_model()
        self.staff = User.objects.create_user(username="market_staff", password="StrongPass123", is_staff=True)
        self.user = User.objects.create_user(username="market_user", password="StrongPass123")
        self.existing = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Minimal Wear)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline",
            item_type=SteamItem.ItemType.SKIN,
            price_rub=Decimal("1500.00"),
        )

    @patch("modules.v2.steam_catalog.services.market_search._fetch_search_payload")
    def test_search_returns_results_and_marks_existing_family(self, fetch_mock):
        fetch_mock.return_value = self.PAYLOAD
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steam_catalog:market_search"), {"q": "redline"})

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([row["hash_name"] for row in results], ["AK-47 | Redline (Field-Tested)", "Kilowatt Case"])
        self.assertEqual(results[0]["rarity_key"], "classified")
        self.assertEqual(results[0]["catalog_item_id"], self.existing.id)
        self.assertEqual(results[0]["catalog_url"], reverse("steam_catalog:detail", args=[self.existing.id]))
        self.assertIn("abc123", results[0]["icon"])
        self.assertIsNone(results[1]["catalog_item_id"])
        self.assertEqual(results[1]["catalog_url"], "")

    @patch("modules.v2.steam_catalog.services.market_search._fetch_search_payload")
    def test_search_is_cached_per_query(self, fetch_mock):
        fetch_mock.return_value = self.PAYLOAD
        self.client.force_login(self.staff)

        self.client.get(reverse("steam_catalog:market_search"), {"q": "redline"})
        self.client.get(reverse("steam_catalog:market_search"), {"q": "Redline"})

        self.assertEqual(fetch_mock.call_count, 1)

    @patch("modules.v2.steam_catalog.services.market_search._fetch_search_payload")
    def test_short_query_skips_steam(self, fetch_mock):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steam_catalog:market_search"), {"q": "a"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])
        fetch_mock.assert_not_called()

    @patch("modules.v2.steam_catalog.services.market_search._fetch_search_payload", side_effect=OSError("down"))
    def test_steam_failure_returns_readable_error(self, fetch_mock):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steam_catalog:market_search"), {"q": "redline"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("Steam Market", response.json()["error"])

    def test_search_requires_staff(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("steam_catalog:market_search"), {"q": "redline"})

        self.assertEqual(response.status_code, 403)

    def test_add_page_renders_market_search(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steam_catalog:add"))

        self.assertContains(response, "Поиск в Steam Market")
        self.assertContains(response, reverse("steam_catalog:market_search"))

    def test_alert_create_prefills_item(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("steam_catalog:alert_rule_create"), {"item": self.existing.id})

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial["item"], self.existing.id)
        self.assertEqual(form.initial["scope_type"], UserPriceAlertRule.ScopeType.ITEM)


class CS2ReferenceTests(TestCase):
    SKIN_ROWS = [
        {
            "id": "skin-redline",
            "name": "AK-47 | Redline",
            "weapon": {"name": "AK-47"},
            "category": {"name": "Rifles"},
            "pattern": {"name": "Redline"},
            "min_float": 0.1,
            "max_float": 0.7,
            "rarity": {"name": "Classified", "color": "#d32ce6"},
            "stattrak": True,
            "souvenir": False,
            "wears": [{"name": "Minimal Wear"}, {"name": "Field-Tested"}],
            "collections": [{"name": "The Phoenix Collection"}],
            "crates": [{"name": "Operation Phoenix Weapon Case"}],
            "image": "https://example.com/redline.png",
        },
        {
            "id": "skin-asiimov",
            "name": "AWP | Asiimov",
            "weapon": {"name": "AWP"},
            "category": {"name": "Sniper Rifles"},
            "rarity": {"name": "Covert", "color": "#eb4b4b"},
            "wears": [{"name": "Field-Tested"}],
            "collections": [{"name": "The Phoenix Collection"}],
            "image": "https://example.com/asiimov.png",
        },
        {"id": "", "name": "broken"},
    ]
    CASE_ROWS = [
        {"id": "crate-1", "name": "Kilowatt Case", "market_hash_name": "Kilowatt Case", "type": "Case", "first_sale_date": "2024/02/06", "rarity": {"name": "Base Grade"}, "image": "https://example.com/kw.png"},
        {"id": "crate-2", "name": "No Market", "market_hash_name": None, "type": "Case"},
    ]

    def setUp(self):
        from .models import CS2ReferenceItem
        from .services.reference import import_reference_rows

        self.Ref = CS2ReferenceItem
        self.skins = import_reference_rows(kind=CS2ReferenceItem.Kind.SKIN, rows=self.SKIN_ROWS)
        self.cases = import_reference_rows(kind=CS2ReferenceItem.Kind.CASE, rows=self.CASE_ROWS)
        self.staff = User.objects.create_user(username="ref_staff", password="StrongPass123", is_staff=True)

    def test_import_parses_and_skips_invalid_rows(self):
        self.assertEqual((self.skins.created, self.skins.skipped), (2, 1))
        self.assertEqual((self.cases.created, self.cases.skipped), (1, 1))

        redline = self.Ref.objects.get(external_id="skin-redline")
        self.assertEqual(redline.weapon, "AK-47")
        self.assertEqual(redline.rarity_key, "classified")
        self.assertEqual(redline.collection_name, "The Phoenix Collection")
        self.assertEqual(redline.wears, ["Minimal Wear", "Field-Tested"])
        self.assertEqual(redline.min_float, Decimal("0.1000"))
        self.assertTrue(redline.has_stattrak)
        self.assertEqual(redline.catalog_input_value, "AK-47 | Redline (Minimal Wear)")

    def test_reimport_updates_existing_rows(self):
        from .services.reference import import_reference_rows

        rows = [dict(self.SKIN_ROWS[0], image="https://example.com/new.png")]
        result = import_reference_rows(kind=self.Ref.Kind.SKIN, rows=rows)

        self.assertEqual((result.created, result.updated), (0, 1))
        self.assertEqual(self.Ref.objects.get(external_id="skin-redline").image_url, "https://example.com/new.png")

    def test_reference_page_filters_by_weapon_and_rarity(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steam_catalog:reference"), {"weapon": "AWP"})
        self.assertContains(response, "AWP | Asiimov")
        self.assertNotContains(response, "AK-47 | Redline")

        response = self.client.get(reverse("steam_catalog:reference"), {"rarity": "classified"})
        self.assertContains(response, "AK-47 | Redline")
        self.assertNotContains(response, "AWP | Asiimov")

        response = self.client.get(reverse("steam_catalog:reference"), {"kind": "case"})
        self.assertContains(response, "Kilowatt Case")

    def test_reference_marks_items_already_in_catalog(self):
        item = SteamItem.objects.create(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            family_name="AK-47 | Redline",
            name="AK-47 | Redline",
            item_type=SteamItem.ItemType.SKIN,
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse("steam_catalog:reference"))

        self.assertContains(response, reverse("steam_catalog:detail", args=[item.id]))
        self.assertContains(response, reverse("steam_catalog:reference_add", args=[self.Ref.objects.get(external_id="skin-asiimov").id]))

    @patch("modules.v2.steam_catalog.services.fetch_price_rub_from_steam", return_value=Decimal("100.00"))
    @patch("modules.v2.steam_catalog.services.try_fetch_listing_render_data", return_value={})
    @patch("modules.v2.steam_catalog.services.try_fetch_listing_metadata", return_value={})
    def test_add_from_reference_creates_family_and_fills_metadata(self, *_mocks):
        reference = self.Ref.objects.get(external_id="skin-redline")
        self.client.force_login(self.staff)

        response = self.client.post(reverse("steam_catalog:reference_add", args=[reference.id]))

        anchor = SteamItem.objects.get(market_hash_name="AK-47 | Redline (Minimal Wear)")
        self.assertRedirects(response, reverse("steam_catalog:detail", args=[anchor.id]), fetch_redirect_response=False)
        family = SteamItem.objects.filter(family_name="AK-47 | Redline")
        self.assertGreater(family.count(), 1)
        self.assertFalse(family.filter(icon_url="").exists())
        self.assertEqual(set(family.values_list("rarity_key", flat=True)), {"classified"})

    def test_add_from_reference_requires_staff(self):
        user = User.objects.create_user(username="ref_user", password="StrongPass123")
        self.client.force_login(user)

        response = self.client.post(reverse("steam_catalog:reference_add", args=[self.Ref.objects.first().id]))

        self.assertEqual(response.status_code, 403)

    def test_import_command_reads_local_files(self):
        import tempfile
        from pathlib import Path

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "skins.json").write_text(json.dumps(self.SKIN_ROWS), encoding="utf-8")
            call_command("import_cs2_reference", "--kinds", "skin", "--from-dir", tmp, stdout=MagicMock())

        self.assertEqual(self.Ref.objects.filter(kind="skin").count(), 2)
