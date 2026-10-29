from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.steam_catalog.models import SWEEP_SOURCE, SteamItem, SteamItemPriceHistory
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription

from .models import SteamPurchase, SteamPurchaseSale
from .portfolio_history import build_portfolio_chart, build_portfolio_days, build_week_summary, item_week_changes

User = get_user_model()


def _local(days_ago: int, hour: int = 12) -> datetime:
    day = timezone.localdate() - timedelta(days=days_ago)
    return timezone.make_aware(datetime.combine(day, time(hour)), timezone.get_current_timezone())


class PortfolioHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="investor", password="StrongPass123")
        self.case = SteamItem.objects.create(
            market_hash_name="Recoil Case", family_name="Recoil Case", name="Recoil Case",
            item_type=SteamItem.ItemType.CASE, price_rub=Decimal("60.00"), is_active=True,
        )

    def _price(self, item, price, days_ago, *, source="steam"):
        row = SteamItemPriceHistory.objects.create(item=item, price_rub=Decimal(price), source=source)
        SteamItemPriceHistory.objects.filter(pk=row.pk).update(created_at=_local(days_ago, 8))

    def _buy(self, *, quantity=10, price="40.00", days_ago=10, item=None):
        return SteamPurchase.objects.create(
            item=item or self.case, purchased_at=_local(days_ago, 9), quantity=quantity,
            purchase_price_rub=Decimal(price), created_by=self.user,
        )

    def _purchases(self):
        return list(SteamPurchase.objects.filter(created_by=self.user).select_related("item"))

    def test_days_follow_price_history_and_end_at_current_price(self):
        self._buy(days_ago=5)
        self._price(self.case, "40.00", 6)
        self._price(self.case, "50.00", 3)
        days = build_portfolio_days(self._purchases(), days=30)

        self.assertEqual(days[0].day, timezone.localdate() - timedelta(days=5))  # starts at the first purchase
        self.assertEqual(days[0].value_rub, Decimal("400.00"))
        self.assertEqual(days[2].value_rub, Decimal("500.00"))
        self.assertEqual(days[-1].value_rub, Decimal("600.00"))
        self.assertEqual(days[-1].invested_rub, Decimal("400.00"))
        self.assertEqual(len(days), 6)

    def test_sales_leave_the_value_and_count_as_realized(self):
        purchase = self._buy(days_ago=10)
        self._price(self.case, "40.00", 11)
        sale = SteamPurchaseSale(purchase=purchase, sold_at=_local(2), quantity=4, sale_price_rub=Decimal("55.00"))
        sale.save()
        days = build_portfolio_days(self._purchases(), days=7)

        self.assertEqual(days[0].invested_rub, Decimal("400.00"))
        self.assertEqual(days[0].realized_rub, Decimal("0.00"))
        self.assertEqual(days[-1].invested_rub, Decimal("240.00"))
        self.assertEqual(days[-1].realized_rub, sale.profit_rub)

    def test_sweep_rows_are_ignored_when_median_exists(self):
        self._buy(days_ago=5)
        self._price(self.case, "40.00", 6)
        self._price(self.case, "10.00", 3, source=SWEEP_SOURCE)
        days = build_portfolio_days(self._purchases(), days=30)
        self.assertEqual(days[2].value_rub, Decimal("400.00"))

    def test_old_median_wins_over_many_later_sweep_rows(self):
        self._buy(days_ago=5)
        self._price(self.case, "40.00", 40)
        for days_ago in range(39, 9, -1):
            self._price(self.case, "10.00", days_ago, source=SWEEP_SOURCE)
        days = build_portfolio_days(self._purchases(), days=3)
        self.assertEqual(days[0].value_rub, Decimal("400.00"))

    def test_after_fee_also_applies_to_sales(self):
        purchase = self._buy(days_ago=10)
        SteamPurchaseSale(purchase=purchase, sold_at=_local(2), quantity=4, sale_price_rub=Decimal("57.50")).save()
        days = build_portfolio_days(self._purchases(), days=7, after_fee=True)
        # 4 × 57.50 / 1.15 − 4 × 40
        self.assertEqual(days[-1].realized_rub, Decimal("40.00"))

    def test_baseline_takes_few_queries_for_many_items(self):
        for index in range(12):
            item = SteamItem.objects.create(
                market_hash_name=f"Case {index}", family_name=f"Case {index}", name=f"Case {index}",
                item_type=SteamItem.ItemType.CASE, price_rub=Decimal("10.00"), is_active=True,
            )
            self._buy(days_ago=5, item=item)
            self._price(item, "9.00", 30)
        purchases = self._purchases()
        with self.assertNumQueries(3):  # baselines, history in range, sales
            days = build_portfolio_days(purchases, days=3)
        self.assertEqual(days[0].value_rub, Decimal("1080.00"))

    def test_after_fee_divides_prices(self):
        self._buy(days_ago=2)
        days = build_portfolio_days(self._purchases(), days=7, after_fee=True)
        self.assertEqual(days[-1].value_rub, Decimal("521.74"))

    def test_week_summary_counts_price_moves_not_new_money(self):
        self._buy(days_ago=20)
        self._price(self.case, "50.00", 9)
        self._buy(quantity=5, price="58.00", days_ago=2)  # new money this week: +2 ₽ each by today
        summary = build_week_summary(self._purchases())

        # 10 × (60 − 50) + 5 × (60 − 58)
        self.assertEqual(summary.change_rub, Decimal("110.00"))
        self.assertEqual(summary.gainers[0].change_rub, Decimal("110.00"))
        self.assertEqual(summary.gainers[0].quantity, 15)
        self.assertEqual(summary.value_rub, Decimal("900.00"))

    def test_item_week_changes(self):
        self._buy(days_ago=20)
        self._price(self.case, "50.00", 9)
        self.assertEqual(item_week_changes(self._purchases())[self.case.pk], (Decimal("10.00"), Decimal("20.0")))

    def test_chart_has_two_lines(self):
        self._buy(days_ago=3)
        chart = build_portfolio_chart(build_portfolio_days(self._purchases(), days=7))
        self.assertTrue(chart["has_data"])
        self.assertEqual(len(chart["points_json"]), 4)
        self.assertIn("вложено", chart["points_json"][0]["note"])
        self.assertTrue(chart["invested_path"].startswith("M"))

    def test_page_shows_chart_for_pro_and_teaser_otherwise(self):
        self._buy(days_ago=3)
        client = Client()
        client.login(username="investor", password="StrongPass123")
        response = client.get(reverse("steam_trading:investments"))
        self.assertNotIn("portfolio_chart", response.context)
        self.assertContains(response, "График портфеля и недельная сводка")

        UserSubscription.objects.create(user=self.user, plan=SubscriptionPlan.objects.get(code="site_pro"))
        response = client.get(reverse("steam_trading:investments") + "?range=7d&fee=1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["portfolio_chart"]["has_data"])
        self.assertTrue(response.context["portfolio_after_fee"])
        self.assertContains(response, "Стоимость портфеля")
        self.assertContains(response, "За неделю")
        self.assertContains(response, "Цена за 7 дней")

    def test_pro_plan_card_marks_the_new_feature(self):
        plan = SubscriptionPlan.objects.get(code="site_pro")
        self.assertIn("Новое: график портфеля и недельная сводка в Telegram", plan.ui_features)
        response = Client().get(reverse("subscriptions:plans"))
        self.assertContains(response, '<span class="plan-card__new">Новое</span>')
