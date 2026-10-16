from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription
from modules.v2.system.services import update_module_states
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.tg_notifier.models import TelegramNotifierConfig
from modules.v2.tg_notifier.services import TelegramNotifierError

from .models import SteamPurchase, SteamPurchaseSale
from .services import (
    build_purchase_row,
    build_purchase_summary,
    build_unlock_notification_message,
    create_purchase_sale,
    dispatch_due_unlock_notifications,
)

from .investment_services import (
    build_investment_analytics,
    build_investment_dashboard,
    build_investment_row,
)

User = get_user_model()


class SteamTradingTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _grant_plan(self, user, level: int):
        plan = SubscriptionPlan.objects.get(level=level)
        return UserSubscription.objects.create(user=user, plan=plan)

    def _make_farmer(self, username="farmer_trade"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.FARMER
        profile.save(update_fields=["role"])
        return user

    def _make_admin(self, username="admin_trade"):
        user = User.objects.create_user(username=username, password="StrongPass123")
        profile = UserProfile.objects.get(user=user)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        return user

    def _make_case(self, name="Recoil Case", price=Decimal("44.35")):
        return SteamItem.objects.create(
            market_hash_name=name,
            family_name=name,
            name=name,
            item_type=SteamItem.ItemType.CASE,
            price_rub=price,
            is_active=True,
        )

    def _make_purchase(self, *, user=None, item=None, quantity=29, purchase_price=Decimal("44.35"), purchased_at=None):
        user = user or self._make_farmer()
        item = item or self._make_case(price=purchase_price)
        purchased_at = purchased_at or timezone.now() - timezone.timedelta(days=8)
        return SteamPurchase.objects.create(
            item=item,
            purchased_at=purchased_at,
            quantity=quantity,
            purchase_price_rub=purchase_price,
            created_by=user,
        )

    def test_plain_user_cannot_open_trading_module(self):
        User.objects.create_user(username="plain_trade", password="StrongPass123")
        self.client.login(username="plain_trade", password="StrongPass123")

        response = self.client.get(reverse("steam_trading:investments"))
        self.assertEqual(response.status_code, 403)

    def test_farmer_can_create_purchase(self):
        user = self._make_farmer("farmer_create_purchase")
        item = self._make_case()
        self.client.login(username="farmer_create_purchase", password="StrongPass123")

        response = self.client.post(
            reverse("steam_trading:add"),
            {
                "item": item.id,
                "purchased_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "quantity": 29,
                "purchase_price_rub": "44.35",
            },
        )
        self.assertEqual(response.status_code, 302)
        purchase = SteamPurchase.objects.get(created_by=user)
        self.assertEqual(purchase.item, item)
        self.assertEqual(purchase.quantity, 29)
        self.assertEqual(purchase.total_spent_rub, Decimal("1286.15"))

    def test_model_recalculates_total_spent_rub(self):
        user = self._make_farmer("farmer_model_total")
        item = self._make_case("Dreams & Nightmares Case", Decimal("159.99"))

        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.now(),
            quantity=7,
            purchase_price_rub=Decimal("159.99"),
            total_spent_rub=Decimal("1.00"),
            created_by=user,
        )
        self.assertEqual(purchase.total_spent_rub, Decimal("1119.93"))

    def test_purchase_computes_unlocked_at_for_moscow_trade_hold(self):
        user = self._make_farmer("farmer_unlock_time")
        item = self._make_case()
        purchased_at = timezone.make_aware(timezone.datetime(2026, 3, 15, 3, 0))

        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=purchased_at,
            quantity=1,
            purchase_price_rub=Decimal("44.35"),
            created_by=user,
        )

        self.assertEqual(timezone.localtime(purchase.unlocked_at).strftime("%d.%m.%Y %H:%M"), "22.03.2026 10:00")

    def test_profit_metrics_are_computed_correctly(self):
        user = self._make_farmer("farmer_profit")
        item = self._make_case("Gamma Case", Decimal("50.00"))
        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.now(),
            quantity=10,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )

        row = build_purchase_row(purchase)
        self.assertEqual(row.current_total_rub, Decimal("500.00"))
        self.assertEqual(row.profit_rub, Decimal("100.00"))
        self.assertEqual(row.profit_percent, Decimal("25.00"))
        self.assertEqual(row.current_total_after_fee_rub, Decimal("425.00"))
        self.assertEqual(row.profit_after_fee_rub, Decimal("25.00"))

    def test_list_page_contains_summary_and_unlock_status(self):
        user = self._make_farmer("farmer_list")
        item = self._make_case()
        SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.now(),
            quantity=29,
            purchase_price_rub=Decimal("44.35"),
            created_by=user,
        )

        self.client.login(username="farmer_list", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:investments"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recoil Case")
        self.assertContains(response, "Стоимость позиций и прибыль считаются на тарифе")
        self.assertContains(response, "Мой портфель")
        self.assertContains(response, "Можно продать")
        self.assertContains(response, "Инвестиции")
        self.assertContains(response, "В трейд-бане")

    def test_admin_cannot_see_other_users_purchases_in_personal_portfolio(self):
        owner = self._make_farmer("farmer_owner")
        self._make_admin("admin_viewer")
        item = self._make_case()
        SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.now(),
            quantity=5,
            purchase_price_rub=Decimal("10.00"),
            created_by=owner,
        )

        self.client.login(username="admin_viewer", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:investments"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Recoil Case")

    def test_admin_can_open_sell_page_for_other_users_purchase(self):
        owner = self._make_farmer("farmer_sell_owner")
        self._make_admin("admin_sell_viewer")
        purchase = self._make_purchase(user=owner)
    
        self.client.login(username="admin_sell_viewer", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:sell", args=[purchase.id]))
        self.assertEqual(response.status_code, 200)

    def test_summary_handles_zero_spent(self):
        summary = build_purchase_summary([])
        self.assertEqual(summary.total_spent_sum, Decimal("0.00"))
        self.assertEqual(summary.current_total_sum, Decimal("0.00"))
        self.assertEqual(summary.total_profit_sum, Decimal("0.00"))
        self.assertEqual(summary.total_profit_percent, Decimal("0.00"))
        self.assertEqual(summary.realized_profit_sum, Decimal("0.00"))
        self.assertEqual(summary.sold_quantity_sum, 0)
        self.assertEqual(summary.remaining_quantity_sum, 0)

    def test_unlock_notification_message_includes_steam_fee_value(self):
        user = self._make_farmer("farmer_message")
        item = self._make_case(price=Decimal("50.00"))
        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 15, 3, 0)),
            quantity=10,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )

        message = build_unlock_notification_message(purchase)
        self.assertIn("Стоимость после комиссии Steam (15%)", message)
        self.assertIn("425.00 ₽", message)
        self.assertIn("Прибыль после комиссии", message)

    @patch("modules.v2.steam_trading.services._telegram_send_message")
    @patch("modules.v2.steam_trading.services.settings.TELEGRAM_NOTIFICATIONS_ENABLED", True)
    def test_dispatch_due_unlock_notifications_marks_purchase_notified(self, send_mock):
        send_mock.return_value = {"ok": True}
        user = self._make_farmer("farmer_notify")
        item = self._make_case(price=Decimal("50.00"))
        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 1, 3, 0)),
            quantity=10,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )

        summary = dispatch_due_unlock_notifications(now=timezone.make_aware(timezone.datetime(2026, 3, 8, 10, 1)), limit=10)

        purchase.refresh_from_db()
        self.assertEqual(summary.processed_count, 1)
        self.assertEqual(summary.sent_count, 1)
        self.assertEqual(summary.error_count, 0)
        self.assertIsNotNone(purchase.unlock_notified_at)
        send_mock.assert_called_once()

    @patch("modules.v2.steam_trading.services._telegram_enabled", return_value=True)
    @patch("modules.v2.steam_trading.services._telegram_send_message")
    @patch("modules.v2.steam_trading.services.settings.TELEGRAM_NOTIFICATIONS_ENABLED", True)
    def test_dispatch_due_unlock_notifications_saves_error(self, send_mock, _enabled):
        send_mock.side_effect = RuntimeError("telegram down")
        user = self._make_farmer("farmer_notify_error")
        item = self._make_case(price=Decimal("50.00"))
        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 1, 3, 0)),
            quantity=10,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )

        summary = dispatch_due_unlock_notifications(now=timezone.make_aware(timezone.datetime(2026, 3, 8, 10, 1)), limit=10)

        purchase.refresh_from_db()
        self.assertEqual(summary.error_count, 1)
        # The owner was told on the site, so the purchase leaves the queue; the Telegram error stays on record.
        self.assertIsNotNone(purchase.unlock_notified_at)
        self.assertEqual(purchase.unlock_notification_error, "telegram down")
        from modules.v2.notifications.models import Notification

        self.assertTrue(Notification.objects.filter(recipient=user, kind="steam_investment_unlocked", object_id=purchase.pk).exists())

    def test_create_sale_calculates_snapshots(self):
        user = self._make_farmer("farmer_sale_create")
        purchase = self._make_purchase(user=user, quantity=29, purchase_price=Decimal("44.35"))

        sale = create_purchase_sale(
            purchase=purchase,
            sold_at=timezone.now(),
            quantity=5,
            sale_price_rub=Decimal("60.00"),
            created_by=user,
        )

        self.assertEqual(sale.total_sale_rub, Decimal("300.00"))
        self.assertEqual(sale.cost_basis_rub, Decimal("221.75"))
        self.assertEqual(sale.profit_rub, Decimal("78.25"))
        self.assertEqual(sale.profit_percent, Decimal("35.29"))

    def test_cannot_sell_more_than_remaining(self):
        user = self._make_farmer("farmer_sale_limit")
        purchase = self._make_purchase(user=user, quantity=10)
        create_purchase_sale(
            purchase=purchase,
            sold_at=timezone.now(),
            quantity=4,
            sale_price_rub=Decimal("50.00"),
            created_by=user,
        )

        with self.assertRaisesMessage(Exception, "Нельзя продать больше остатка"):
            create_purchase_sale(
                purchase=purchase,
                sold_at=timezone.now(),
                quantity=7,
                sale_price_rub=Decimal("55.00"),
                created_by=user,
            )

    def test_partial_and_full_sale_update_purchase_state(self):
        user = self._make_farmer("farmer_sale_status")
        purchase = self._make_purchase(user=user, quantity=10, purchase_price=Decimal("40.00"))

        create_purchase_sale(
            purchase=purchase,
            sold_at=timezone.now(),
            quantity=4,
            sale_price_rub=Decimal("50.00"),
            created_by=user,
        )
        purchase.refresh_from_db()
        row = build_purchase_row(purchase)
        self.assertEqual(row.sold_quantity_total, 4)
        self.assertEqual(row.remaining_quantity, 6)
        self.assertEqual(row.sale_status_label, "Продано частично")
        self.assertEqual(row.realized_profit_rub, Decimal("40.00"))

        create_purchase_sale(
            purchase=purchase,
            sold_at=timezone.now(),
            quantity=6,
            sale_price_rub=Decimal("42.00"),
            created_by=user,
        )
        purchase.refresh_from_db()
        row = build_purchase_row(purchase)
        self.assertEqual(row.sold_quantity_total, 10)
        self.assertEqual(row.remaining_quantity, 0)
        self.assertEqual(row.sale_status_label, "Продано полностью")
        self.assertEqual(row.realized_profit_rub, Decimal("52.00"))

    def test_sell_page_is_available_for_owner(self):
        user = self._make_farmer("farmer_sell_page")
        purchase = self._make_purchase(user=user)

        self.client.login(username="farmer_sell_page", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:sell", args=[purchase.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Продажа закупки")
        self.assertContains(response, purchase.item.group_name)
        self.assertContains(response, "История продаж")

    def test_sell_page_creates_sale(self):
        user = self._make_farmer("farmer_sell_submit")
        purchase = self._make_purchase(user=user, quantity=12, purchase_price=Decimal("44.35"))

        self.client.login(username="farmer_sell_submit", password="StrongPass123")
        response = self.client.post(
            reverse("steam_trading:sell", args=[purchase.id]),
            {
                "sold_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "quantity": 5,
                "sale_price_rub": "60.00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SteamPurchaseSale.objects.filter(purchase=purchase).count(), 1)
        self.assertContains(response, "Продажа сохранена")
        self.assertContains(response, "300.00")

    def test_farmer_cannot_open_other_users_sell_page(self):
        owner = self._make_farmer("farmer_sale_owner")
        intruder = self._make_farmer("farmer_sale_intruder")
        purchase = self._make_purchase(user=owner)

        self.client.login(username="farmer_sale_intruder", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:sell", args=[purchase.id]))
        self.assertEqual(response.status_code, 404)

    def test_admin_can_open_other_users_sell_page(self):
        owner = self._make_farmer("farmer_sale_owner_admin")
        admin = self._make_admin("admin_sale_view")
        purchase = self._make_purchase(user=owner)

        self.client.login(username="admin_sale_view", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:sell", args=[purchase.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, purchase.item.group_name)

    def test_cannot_sell_locked_purchase(self):
        user = self._make_farmer("farmer_locked_sale")
        item = self._make_case(price=Decimal("44.35"))
        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.now(),
            quantity=5,
            purchase_price_rub=Decimal("44.35"),
            created_by=user,
        )

        self.client.login(username="farmer_locked_sale", password="StrongPass123")
        response = self.client.post(
            reverse("steam_trading:sell", args=[purchase.id]),
            {
                "sold_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "quantity": 1,
                "sale_price_rub": "60.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SteamPurchaseSale.objects.filter(purchase=purchase).count(), 0)
        self.assertContains(response, "недоступна для продажи")


    @patch("modules.v2.steam_trading.services.settings.TELEGRAM_NOTIFICATIONS_ENABLED", True)
    @patch("modules.v2.steam_trading.services._telegram_send_message")
    def test_dispatch_due_unlock_notifications_does_not_crash_if_purchase_disappears_before_mark(self, send_mock):
        user = self._make_farmer("farmer_notify_deleted")
        item = self._make_case(price=Decimal("50.00"))
        purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 1, 3, 0)),
            quantity=10,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )

        def delete_purchase(*args, **kwargs):
            SteamPurchase.objects.filter(pk=purchase.pk).delete()
            return {"ok": True}

        send_mock.side_effect = delete_purchase

        summary = dispatch_due_unlock_notifications(
            now=timezone.make_aware(timezone.datetime(2026, 3, 8, 10, 1)),
            limit=10,
        )

        self.assertEqual(summary.processed_count, 1)
        self.assertEqual(summary.sent_count, 0)
        self.assertEqual(summary.error_count, 0)
        self.assertFalse(SteamPurchase.objects.filter(pk=purchase.pk).exists())

    def test_investment_row_uses_remaining_quantity_for_active_metrics(self):
        user = self._make_farmer("farmer_invest_row")
        item = self._make_case(price=Decimal("50.00"))
        purchase = self._make_purchase(
            user=user,
            item=item,
            quantity=10,
            purchase_price=Decimal("40.00"),
        )
        create_purchase_sale(
            purchase=purchase,
            sold_at=timezone.now(),
            quantity=4,
            sale_price_rub=Decimal("50.00"),
            created_by=user,
        )

        purchase.refresh_from_db()
        row = build_investment_row(purchase)

        self.assertEqual(row.remaining_quantity, 6)
        self.assertEqual(row.invested_active_rub, Decimal("240.00"))
        self.assertEqual(row.current_value_rub, Decimal("300.00"))
        self.assertEqual(row.unrealized_pnl_rub, Decimal("60.00"))
        self.assertEqual(row.unrealized_pnl_percent, Decimal("25.00"))
        self.assertEqual(row.realized_pnl_rub, Decimal("40.00"))
        self.assertEqual(row.status_code, "partial")


    @patch("modules.v2.steam_trading.investment_services.timezone.now")
    def test_investment_analytics_splits_ready_and_upcoming_unlocks(self, now_mock):
        now_mock.return_value = timezone.make_aware(timezone.datetime(2026, 3, 17, 9, 0))
        user = self._make_farmer("farmer_invest_analytics")
        item = self._make_case(price=Decimal("50.00"))

        ready_purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 9, 12, 0)),
            quantity=10,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )
        today_purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 10, 13, 0)),
            quantity=5,
            purchase_price_rub=Decimal("45.00"),
            created_by=user,
        )
        tomorrow_purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 11, 12, 0)),
            quantity=6,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )
        next_week_purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.make_aware(timezone.datetime(2026, 3, 15, 12, 0)),
            quantity=7,
            purchase_price_rub=Decimal("40.00"),
            created_by=user,
        )

        analytics = build_investment_analytics([ready_purchase, today_purchase, tomorrow_purchase, next_week_purchase])

        self.assertEqual(analytics.kpi.active_purchases_count, 4)
        self.assertEqual(analytics.kpi.ready_count, 1)
        self.assertEqual(analytics.kpi.hold_count, 3)
        self.assertEqual(len(analytics.upcoming_unlocks.today), 1)
        self.assertEqual(len(analytics.upcoming_unlocks.tomorrow), 1)
        self.assertEqual(len(analytics.upcoming_unlocks.next_7_days), 1)


    def test_investments_page_available_for_farmer(self):
        user = self._make_farmer("farmer_invest_page")
        self._make_purchase(user=user, quantity=10, purchase_price=Decimal("40.00"))

        self.client.login(username="farmer_invest_page", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:investments"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "steam_trading/investments.html")
        self.assertContains(response, "Мой портфель")
        self.assertContains(response, "Можно продать")
        self.assertContains(response, "Аналитика портфеля")
        self.assertFalse(response.context["investment_can_view_advanced_analytics"])
        self.assertIn("analytics", response.context)

    def test_investments_page_shows_advanced_analytics_for_level_4(self):
        user = self._make_farmer("farmer_invest_level4")
        self._grant_plan(user, 4)
        self._make_purchase(user=user, quantity=10, purchase_price=Decimal("40.00"))

        self.client.login(username="farmer_invest_level4", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:investments"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["investment_can_view_advanced_analytics"])
        self.assertContains(response, "Заработано на продажах")
        self.assertContains(response, "Растут сильнее всего")


    def test_investments_page_handles_missing_catalog_price_and_closed_position(self):
        user = self._make_farmer("farmer_invest_missing")
        item = self._make_case(price=None)

        active_purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.now() - timezone.timedelta(days=8),
            quantity=3,
            purchase_price_rub=Decimal("10.00"),
            created_by=user,
        )
        closed_purchase = SteamPurchase.objects.create(
            item=item,
            purchased_at=timezone.now() - timezone.timedelta(days=9),
            quantity=2,
            purchase_price_rub=Decimal("10.00"),
            created_by=user,
        )
        create_purchase_sale(
            purchase=closed_purchase,
            sold_at=timezone.now(),
            quantity=2,
            sale_price_rub=Decimal("11.00"),
            created_by=user,
        )

        self.client.login(username="farmer_invest_missing", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:investments"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нет цены в каталоге")
        self.assertContains(response, active_purchase.item.group_name)
        self.assertEqual(len(response.context["analytics"].active_rows), 1)
        self.assertEqual(response.context["analytics"].active_rows[0].purchase.id, active_purchase.id)


    def test_empty_investments_page(self):
        user = self._make_farmer("farmer_invest_empty")
        self.client.login(username="farmer_invest_empty", password="StrongPass123")

        response = self.client.get(reverse("steam_trading:investments"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "С чего начать")

    def test_investments_search_without_results_returns_404_page(self):
        user = self._make_farmer("farmer_invest_search_404")
        self._make_purchase(user=user, item=self._make_case(name="Alpha Case", price=Decimal("50.00")))
        self.client.login(username="farmer_invest_search_404", password="StrongPass123")

        response = self.client.get(reverse("steam_trading:investments"), {"q": "Gamma"})

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Активные закупки не найдены", status_code=404)

    def test_investment_dashboard_filters_ready_rows_by_query(self):
        user = self._make_farmer("farmer_invest_filters")
        alpha_item = self._make_case(name="Alpha Case", price=Decimal("50.00"))
        beta_item = self._make_case(name="Beta Case", price=Decimal("55.00"))

        ready_purchase = self._make_purchase(
            user=user,
            item=alpha_item,
            quantity=5,
            purchase_price=Decimal("40.00"),
            purchased_at=timezone.now() - timezone.timedelta(days=9),
        )
        self._make_purchase(
            user=user,
            item=beta_item,
            quantity=5,
            purchase_price=Decimal("40.00"),
            purchased_at=timezone.now() - timezone.timedelta(days=1),
        )

        dashboard = build_investment_dashboard(
            [ready_purchase, *SteamPurchase.objects.exclude(pk=ready_purchase.pk)],
            query="alpha",
            status="ready",
            sort="purchased_desc",
        )

        self.assertEqual(dashboard.filtered_active_count, 1)
        self.assertEqual(dashboard.filtered_active_rows[0].purchase.item.group_name, "Alpha Case")
        self.assertEqual(dashboard.filter_state.status, "ready")
        self.assertEqual(dashboard.filter_state.query, "alpha")


    def test_investment_dashboard_sorts_rows_by_pnl_desc(self):
        user = self._make_farmer("farmer_invest_sort")
        high_item = self._make_case(name="High PnL Case", price=Decimal("80.00"))
        low_item = self._make_case(name="Low PnL Case", price=Decimal("45.00"))

        high_purchase = self._make_purchase(
            user=user,
            item=high_item,
            quantity=3,
            purchase_price=Decimal("40.00"),
        )
        low_purchase = self._make_purchase(
            user=user,
            item=low_item,
            quantity=3,
            purchase_price=Decimal("40.00"),
        )

        dashboard = build_investment_dashboard(
            [high_purchase, low_purchase],
            sort="pnl_desc",
        )

        self.assertEqual(dashboard.filtered_active_rows[0].purchase.id, high_purchase.id)
        self.assertEqual(dashboard.filtered_active_rows[1].purchase.id, low_purchase.id)


    def test_investments_page_applies_status_filter_and_exposes_filtered_rows(self):
        user = self._make_farmer("farmer_invest_page_filter")
        ready_item = self._make_case(name="Ready Case", price=Decimal("60.00"))
        hold_item = self._make_case(name="Hold Case", price=Decimal("60.00"))

        ready_purchase = self._make_purchase(
            user=user,
            item=ready_item,
            quantity=2,
            purchase_price=Decimal("40.00"),
            purchased_at=timezone.now() - timezone.timedelta(days=8),
        )
        self._make_purchase(
            user=user,
            item=hold_item,
            quantity=2,
            purchase_price=Decimal("40.00"),
            purchased_at=timezone.now() - timezone.timedelta(days=2),
        )

        self.client.login(username="farmer_invest_page_filter", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:investments"), {"status": "ready"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filtered_active_count"], 1)
        self.assertEqual(len(response.context["filtered_active_rows"]), 1)
        self.assertEqual(response.context["filtered_active_rows"][0].purchase.id, ready_purchase.id)
        self.assertEqual(response.context["investment_filter_state"].status, "ready")


    def test_investments_page_contains_ready_action_links(self):
        user = self._make_farmer("farmer_invest_ready_actions")
        item = self._make_case(name="Ready Action Case", price=Decimal("70.00"))
        purchase = self._make_purchase(
            user=user,
            item=item,
            quantity=2,
            purchase_price=Decimal("40.00"),
            purchased_at=timezone.now() - timezone.timedelta(days=8),
        )

        self.client.login(username="farmer_invest_ready_actions", password="StrongPass123")
        response = self.client.get(reverse("steam_trading:investments"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("steam_trading:sell", args=[purchase.id]))
        self.assertContains(response, reverse("steam_catalog:detail", args=[item.id]))
        # Without the Telegram feature there is no TG button at all (no dead "upgrade" buttons in every row).
        self.assertNotContains(response, reverse("steam_trading:send_tg", args=[purchase.id]))

    @patch("modules.v2.steam_trading.views.send_investment_purchase_notification")
    def test_send_tg_requires_level_3_subscription(self, send_mock):
        user = self._make_farmer("farmer_send_tg_locked")
        purchase = self._make_purchase(user=user)
        update_module_states({"tg_notifier": True})

        self.client.login(username="farmer_send_tg_locked", password="StrongPass123")
        response = self.client.post(
            reverse("steam_trading:send_tg", args=[purchase.id]),
            {"next": reverse("steam_trading:investments")},
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "TG-отправка закрыта тарифом", status_code=403)
        send_mock.assert_not_called()

    @patch("modules.v2.steam_trading.views.send_investment_purchase_notification")
    def test_owner_can_send_tg_for_own_purchase_from_investments(self, send_mock):
        user = self._make_farmer("farmer_send_tg")
        self._grant_plan(user, 3)
        purchase = self._make_purchase(user=user)
        update_module_states({"tg_notifier": True})
        TelegramNotifierConfig.objects.update_or_create(
            singleton_key=1,
            defaults={
                "is_enabled": True,
                "bot_token": "123456:ABCDEF",
                "default_chat_id": "-100555",
                "investment_message_template": "Предмет: {{ item_name }}",
            },
        )

        self.client.login(username="farmer_send_tg", password="StrongPass123")
        response = self.client.post(
            reverse("steam_trading:send_tg", args=[purchase.id]),
            {"next": reverse("steam_trading:investments")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        send_mock.assert_called_once()
        self.assertContains(response, "TG уведомление отправлено")

    @patch("modules.v2.steam_trading.views.send_investment_purchase_notification")
    def test_send_tg_returns_error_message_without_500(self, send_mock):
        send_mock.side_effect = TelegramNotifierError("telegram down")

        user = self._make_farmer("farmer_send_tg_error")
        self._grant_plan(user, 3)
        purchase = self._make_purchase(user=user)
        update_module_states({"tg_notifier": True})
        TelegramNotifierConfig.objects.update_or_create(
            singleton_key=1,
            defaults={
                "is_enabled": True,
                "bot_token": "123456:ABCDEF",
                "default_chat_id": "-100555",
                "investment_message_template": "Предмет: {{ item_name }}",
            },
        )

        self.client.login(username="farmer_send_tg_error", password="StrongPass123")
        response = self.client.post(
            reverse("steam_trading:send_tg", args=[purchase.id]),
            {"next": reverse("steam_trading:investments")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TG отправка не удалась")

    @patch("modules.v2.steam_trading.services._telegram_enabled", return_value=True)
    @patch("modules.v2.steam_trading.services._telegram_send_message")
    def test_dispatch_unlock_notifications_uses_html_parse_mode(self, send_mock, _enabled):
        owner = self._make_farmer("farmer_unlock_tg")
        purchase = self._make_purchase(user=owner, quantity=1)
        purchase.unlocked_at = timezone.now() - timezone.timedelta(minutes=1)
        purchase.unlock_notified_at = None
        purchase.save(update_fields=["unlocked_at", "unlock_notified_at", "updated_at"])

        from modules.v2.steam_trading.services import dispatch_due_unlock_notifications

        summary = dispatch_due_unlock_notifications(limit=5)

        self.assertEqual(summary.sent_count, 1)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.kwargs["parse_mode"], "HTML")

    @patch("modules.v2.steam_trading.views.send_investment_purchase_notification")
    def test_farmer_cannot_send_tg_for_foreign_purchase(self, send_mock):
        owner = self._make_farmer("farmer_owner_tg")
        self._grant_plan(owner, 3)
        intruder = self._make_farmer("farmer_intruder_tg")
        update_module_states({"tg_notifier": True})
        self._grant_plan(intruder, 3)
        purchase = self._make_purchase(user=owner)

        self.client.login(username="farmer_intruder_tg", password="StrongPass123")
        response = self.client.post(
            reverse("steam_trading:send_tg", args=[purchase.id]),
            {"next": reverse("steam_trading:investments")},
        )

        self.assertEqual(response.status_code, 404)
        send_mock.assert_not_called()

    @patch("modules.v2.steam_trading.views.send_investment_purchase_notification")
    def test_send_tg_targets_purchase_owner_profile(self, send_mock):
        owner = self._make_farmer("farmer_owner_profile_tg")
        self._grant_plan(owner, 3)
        purchase = self._make_purchase(user=owner)
        update_module_states({"tg_notifier": True})

        owner_profile = UserProfile.objects.get(user=owner)
        owner_profile.telegram_chat_id = "7151390596"
        owner_profile.save(update_fields=["telegram_chat_id"])

        self.client.login(username="farmer_owner_profile_tg", password="StrongPass123")
        response = self.client.post(
            reverse("steam_trading:send_tg", args=[purchase.id]),
            {"next": reverse("steam_trading:investments")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        send_mock.assert_called_once_with(
            purchase=purchase,
            recipient_user=purchase.created_by,
            allow_default_fallback=False,
        )
