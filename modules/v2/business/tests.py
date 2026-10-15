from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.payments.models import Payment
from modules.v2.profiles.models import LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.balance import adjust_balance

from . import revenue, stats
from .models import RevenueCategory, RevenueEntry, RevenueSource
from .report import build_daily_report, send_daily_report

User = get_user_model()
PASSWORD = "StrongPass123"


def make_user(username: str, role: str = UserRole.USER) -> User:
    user = User.objects.create_user(username=username, password=PASSWORD)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if role != UserRole.USER:
        profile.role = role
        profile.save(update_fields=["role"])
    return User.objects.get(pk=user.pk)


def fund(user, amount: str) -> None:
    adjust_balance(user=user, delta=Decimal(amount), reason=LedgerReason.TOPUP, amount_snapshot=Decimal(amount))


def pay(user, amount: str, *, is_test: bool = False, paid_at=None) -> Payment:
    return Payment.objects.create(user=user, provider="yookassa", amount=Decimal(amount), status=Payment.Status.SUCCEEDED, is_test=is_test, paid_at=paid_at or timezone.now())


def amounts(**filters) -> list[Decimal]:
    return list(RevenueEntry.objects.filter(**filters).order_by("id").values_list("amount", flat=True))


class RecordingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("biz_buyer")
        fund(self.user, "10000.00")

    def test_upgrade_books_switch_fee_as_commission(self):
        from modules.v2.subscriptions.models import SubscriptionPlan
        from modules.v2.subscriptions.services import assign_user_subscription_plan, purchase_subscription_with_balance

        level_1 = SubscriptionPlan.objects.get(level=1)
        level_2 = SubscriptionPlan.objects.get(level=2)
        assign_user_subscription_plan(user=self.user, plan=level_1)
        purchase_subscription_with_balance(user=self.user, plan=level_2)

        delta = level_2.price_rub - level_1.price_rub
        self.assertEqual(amounts(source=RevenueSource.SWITCH_FEE, category=RevenueCategory.COMMISSION), [Decimal("100.00")])
        self.assertEqual(amounts(source=RevenueSource.SUBSCRIPTION), [delta] if delta > 0 else [])

    def test_new_subscription_is_subscription_income_only(self):
        from modules.v2.subscriptions.models import SubscriptionPlan
        from modules.v2.subscriptions.services import purchase_subscription_with_balance

        plan = SubscriptionPlan.objects.get(level=2)
        purchase_subscription_with_balance(user=self.user, plan=plan)
        self.assertEqual(amounts(source=RevenueSource.SUBSCRIPTION), [plan.price_rub])
        self.assertFalse(RevenueEntry.objects.filter(category=RevenueCategory.COMMISSION).exists())

    def test_cases_count_openings_minus_buyback(self):
        from modules.v2.case_simulator import services as sim
        from modules.v2.case_simulator.tests import _make_case

        case = _make_case(case_price="100", key_price="50")
        openings = sim.open_case(user=self.user, case=case, count=2)
        sim.sell_openings(user=self.user, opening_ids=[openings[0].pk])

        self.assertEqual(amounts(source=RevenueSource.CASE_OPEN), [Decimal("300.00")])
        self.assertEqual(amounts(source=RevenueSource.CASE_BUYBACK), [-openings[0].item_value])
        self.assertTrue(RevenueEntry.objects.filter(category=RevenueCategory.CASES).count() == 2)

    def test_store_purchase_and_plain_moves_are_not_mixed(self):
        adjust_balance(user=self.user, delta=Decimal("-250.00"), reason=LedgerReason.STORE_PURCHASE, amount_snapshot=Decimal("250.00"))
        adjust_balance(user=self.user, delta=Decimal("50.00"), reason=LedgerReason.PROMO_BONUS, amount_snapshot=Decimal("50.00"))
        self.assertEqual(amounts(category=RevenueCategory.STORE), [Decimal("250.00")])
        self.assertEqual(RevenueEntry.objects.count(), 1)  # top-ups and bonuses are not income

    def test_tradehub_spread_and_market_commission(self):
        order = SimpleNamespace(pk=7, seller_id=self.user.pk, total_catalog_price_rub=Decimal("1000.00"), total_payout_rub=Decimal("850.00"), completed_at=timezone.now(), updated_at=timezone.now())
        revenue.record_tradehub_order(order)
        revenue.record_tradehub_order(order)  # idempotent
        revenue.record_commission(source=RevenueSource.MARKET, amount="9.00", ref="market:MKT-000001", user=self.user)
        self.assertEqual(amounts(source=RevenueSource.TRADEHUB), [Decimal("150.00")])
        self.assertEqual(amounts(source=RevenueSource.MARKET), [Decimal("9.00")])
        self.assertEqual(revenue.record_commission(source=RevenueSource.STORE, amount="1", ref="x"), 0)  # not a commission source

    def test_sync_restores_history_with_switch_fee(self):
        from modules.v2.subscriptions.models import SubscriptionPlan
        from modules.v2.subscriptions.services import assign_user_subscription_plan, purchase_subscription_with_balance

        assign_user_subscription_plan(user=self.user, plan=SubscriptionPlan.objects.get(level=1))
        purchase_subscription_with_balance(user=self.user, plan=SubscriptionPlan.objects.get(level=2))
        adjust_balance(user=self.user, delta=Decimal("-40.00"), reason=LedgerReason.BUNDLE_PURCHASE, amount_snapshot=Decimal("40.00"))
        before = sorted(RevenueEntry.objects.values_list("source", "amount"))
        RevenueEntry.objects.all().delete()

        call_command("sync_revenue", stdout=open("/dev/null", "w"))
        self.assertEqual(sorted(RevenueEntry.objects.values_list("source", "amount")), before)
        self.assertEqual(revenue.sync(), {"ledger": 0, "tradehub": 0})


class StatsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("biz_payer")

    def test_money_summary_separates_topups_income_and_bonuses(self):
        pay(self.user, "1000.00")
        pay(self.user, "500.00", is_test=True)
        fund(self.user, "1000.00")
        adjust_balance(user=self.user, delta=Decimal("-300.00"), reason=LedgerReason.STORE_PURCHASE, amount_snapshot=Decimal("300.00"))
        adjust_balance(user=self.user, delta=Decimal("50.00"), reason=LedgerReason.REFERRAL_BONUS, amount_snapshot=Decimal("50.00"))
        revenue.record_commission(source=RevenueSource.SWITCH_FEE, amount="100", ref="test:1", user=self.user)

        period = stats.resolve_period("today")
        money = stats.money_summary(period.start, period.end)
        self.assertEqual(money["topups"], Decimal("1000.00"))  # the test-mode payment is left out
        self.assertEqual(money["revenue"], Decimal("400.00"))
        self.assertEqual(money["commission"], Decimal("100.00"))
        self.assertEqual(money["bonus_total"], Decimal("50.00"))
        self.assertEqual(money["net"], Decimal("350.00"))

        commissions = {row["source"]: row for row in stats.commission_breakdown(money)}
        self.assertEqual(commissions[RevenueSource.SWITCH_FEE]["share_commission"], 100)
        self.assertEqual(commissions[RevenueSource.SWITCH_FEE]["share_revenue"], 25)

    def test_periods(self):
        now = timezone.now()
        week = stats.resolve_period("7d", now=now)
        self.assertEqual(week.prev_end, week.start)
        self.assertEqual(stats.resolve_period("bogus", now=now).key, "30d")
        self.assertIsNone(stats.resolve_period("all", now=now).start)
        custom = stats.resolve_period("custom", timezone.localdate(now), timezone.localdate(now) - timedelta(days=2), now=now)
        self.assertLess(custom.date_from, custom.date_to)

    def test_dashboard_builds_for_all_time(self):
        pay(self.user, "200.00", paid_at=timezone.now() - timedelta(days=200))
        data = stats.build_dashboard(stats.resolve_period("all"), use_cache=False)
        self.assertTrue(data["series"]["monthly"])
        self.assertEqual(data["users"]["paid_once"], 1)


class PageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.chief = make_user("biz_chief", UserRole.CHIEF_ADMIN)
        self.admin = make_user("biz_admin", UserRole.ADMIN)
        revenue.record_commission(source=RevenueSource.SWITCH_FEE, amount="100", ref="test:page", user=self.admin)

    def test_chief_sees_the_panel_and_commissions(self):
        client = Client()
        client.force_login(self.chief)
        page = client.get(reverse("control_center:business"))
        self.assertContains(page, "из них комиссии")
        self.assertContains(page, "Смена тарифа подписки")
        self.assertContains(page, reverse("control_center:business"))  # the tab
        journal = client.get(reverse("control_center:business_revenue"), {"period": "7d", "category": "commission"})
        self.assertContains(journal, "test:page")
        export = client.get(reverse("control_center:business_revenue_export"), {"period": "all"})
        self.assertIn("test:page", export.content.decode("utf-8"))

    def test_admin_has_no_access_and_no_tab(self):
        client = Client()
        client.force_login(self.admin)
        self.assertEqual(client.get(reverse("control_center:business")).status_code, 403)
        self.assertEqual(client.get(reverse("control_center:business_revenue_export")).status_code, 403)
        self.assertNotContains(client.get(reverse("control_center:ledger")), reverse("control_center:business"))


@override_settings(ADMINS=[("Admin", "admin@case-up.ru")], CSRF_TRUSTED_ORIGINS=["https://case-up.ru"])
class ReportTests(TestCase):
    def test_daily_report_is_mailed_with_commissions(self):
        user = make_user("biz_report")
        yesterday = timezone.now() - timedelta(days=1)
        pay(user, "700.00", paid_at=yesterday)
        revenue.record_commission(source=RevenueSource.TRADEHUB, amount="70", ref="test:report", user=user, occurred_at=yesterday)

        subject, text, html = build_daily_report()
        self.assertIn("комиссии 70.00 ₽", subject)
        self.assertIn("Выкуп предметов (tradehub): 70.00 ₽", text)
        self.assertIn("https://case-up.ru/", html)

        self.assertTrue(send_daily_report())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin@case-up.ru"])

    @override_settings(BUSINESS_DAILY_REPORT=False)
    def test_report_can_be_switched_off(self):
        self.assertFalse(send_daily_report())
        self.assertEqual(mail.outbox, [])
