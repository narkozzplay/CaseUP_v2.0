from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from modules.v2.system.services import update_module_states
from modules.v2.profiles.models import LedgerReason
from modules.v2.profiles.services.balance import adjust_balance

from .feature_codes import (
    FARM_ANALYTICS_DEEP,
    STEAM_CATALOG_DEEP_ANALYTICS,
    STEAM_CATALOG_VIEW,
    TG_NOTIFIER_USER,
)
from .models import SubscriptionFeature, SubscriptionFeaturePricingRule, SubscriptionPlan, UserSubscription
from .pricing import STATUS_REASONABLE, evaluate_plan_pricing
from .services import (
    FEATURE_ACCESS_SUBSCRIPTIONS_DISABLED,
    SubscriptionInvariantError,
    assign_user_subscription_plan,
    get_feature_access_state,
    get_subscription_context,
    user_has_feature,
    get_subscription_purchase_quote,
    purchase_subscription_with_balance,
    sync_feature_rules_to_plans,
)

User = get_user_model()


class SubscriptionServicesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sub_user", password="StrongPass123")

    def test_default_seed_contains_site_and_farmer_plan_lines(self):
        self.assertEqual(SubscriptionPlan.objects.filter(family=SubscriptionPlan.Family.SITE).count(), 5)
        self.assertEqual(SubscriptionPlan.objects.filter(family=SubscriptionPlan.Family.FARMER).count(), 3)
        self.assertTrue(SubscriptionPlan.objects.filter(level=1, code="level_1_viewer").exists())
        self.assertTrue(SubscriptionPlan.objects.filter(code="farmer_start", family=SubscriptionPlan.Family.FARMER).exists())
        self.assertTrue(SubscriptionFeature.objects.filter(code=STEAM_CATALOG_VIEW).exists())
        self.assertTrue(SubscriptionFeature.objects.filter(code=STEAM_CATALOG_DEEP_ANALYTICS).exists())
        self.assertTrue(SubscriptionFeature.objects.filter(code=TG_NOTIFIER_USER).exists())
        self.assertTrue(SubscriptionFeature.objects.filter(code=FARM_ANALYTICS_DEEP).exists())

    def test_get_subscription_context_returns_active_plan_features(self):
        plan = SubscriptionPlan.objects.get(level=2)
        UserSubscription.objects.create(user=self.user, plan=plan)

        summary = get_subscription_context(self.user)

        self.assertTrue(summary.has_active_subscription)
        self.assertEqual(summary.plan_level, 2)
        self.assertTrue(summary.feature_map[STEAM_CATALOG_VIEW])
        self.assertTrue(summary.feature_map[STEAM_CATALOG_DEEP_ANALYTICS])

    def test_feature_access_respects_global_module_flags(self):
        plan = SubscriptionPlan.objects.get(level=2)
        UserSubscription.objects.create(user=self.user, plan=plan)

        update_module_states({"steam_catalog": False, "steam_trading": False})
        access_state = get_feature_access_state(self.user, STEAM_CATALOG_VIEW)
        self.assertFalse(access_state.is_available)
        self.assertEqual(access_state.reason, "module_disabled")

    def test_feature_access_requires_upgrade_when_plan_does_not_include_feature(self):
        plan = SubscriptionPlan.objects.get(level=1)
        UserSubscription.objects.create(user=self.user, plan=plan)
        update_module_states({"tg_notifier": True})

        access_state = get_feature_access_state(self.user, TG_NOTIFIER_USER)
        self.assertFalse(access_state.is_available)
        self.assertEqual(access_state.reason, "upgrade_required")
        self.assertEqual(access_state.required_plan.level, 3)

    def test_user_has_feature_false_without_subscription(self):
        self.assertFalse(user_has_feature(self.user, STEAM_CATALOG_VIEW))

    def test_feature_access_allows_when_subscriptions_module_disabled(self):
        update_module_states({"subscriptions": False})

        access_state = get_feature_access_state(self.user, STEAM_CATALOG_VIEW)

        self.assertTrue(access_state.is_available)
        self.assertEqual(access_state.reason, FEATURE_ACCESS_SUBSCRIPTIONS_DISABLED)
        summary = get_subscription_context(self.user)
        self.assertFalse(summary.module_enabled)
        self.assertTrue(summary.feature_map[STEAM_CATALOG_VIEW])

    def test_assign_user_subscription_plan_replaces_previous_active_subscription_same_family_only(self):
        level_1 = SubscriptionPlan.objects.get(level=1)
        level_3 = SubscriptionPlan.objects.get(level=3)
        farmer = SubscriptionPlan.objects.get(code="farmer_start")
        first = assign_user_subscription_plan(user=self.user, plan=level_1, notes="first")
        second = assign_user_subscription_plan(user=self.user, plan=level_3, notes="second")
        farmer_sub = assign_user_subscription_plan(user=self.user, plan=farmer, family="farmer", notes="farmer")

        first.refresh_from_db()
        self.assertEqual(first.status, UserSubscription.Status.CANCELED)
        self.assertEqual(second.plan_id, level_3.id)
        self.assertEqual(farmer_sub.plan_id, farmer.id)
        self.assertEqual(UserSubscription.objects.filter(user=self.user, status=UserSubscription.Status.ACTIVE).count(), 2)

    def test_farmer_family_feature_gate_isolated_from_site_subscription(self):
        site_plan = SubscriptionPlan.objects.get(level=5)
        farmer_plan = SubscriptionPlan.objects.get(code="farmer_start")
        assign_user_subscription_plan(user=self.user, plan=site_plan)
        access_without_farmer = get_feature_access_state(self.user, FARM_ANALYTICS_DEEP, family="farmer")
        self.assertFalse(access_without_farmer.is_available)
        assign_user_subscription_plan(user=self.user, plan=farmer_plan, family="farmer")
        access_with_farmer = get_feature_access_state(self.user, FARM_ANALYTICS_DEEP, family="farmer")
        self.assertTrue(access_with_farmer.is_available)

    def test_farmer_pricing_engine_evaluates_plan(self):
        plan = SubscriptionPlan.objects.get(code="farmer_pro")
        evaluation = evaluate_plan_pricing(plan)
        self.assertGreater(evaluation.recommended_price_rub, 0)
        self.assertIn(evaluation.price_status, {"underpriced", "reasonable", "overpriced"})
        self.assertTrue(evaluation.explanation)

    def test_purchase_quote_for_upgrade_uses_delta_plus_switch_fee(self):
        level_1 = SubscriptionPlan.objects.get(level=1)
        level_3 = SubscriptionPlan.objects.get(level=3)
        assign_user_subscription_plan(user=self.user, plan=level_1)

        quote = get_subscription_purchase_quote(user=self.user, plan=level_3)

        self.assertTrue(quote.is_upgrade)
        self.assertFalse(quote.blocked_by_higher_level)
        self.assertEqual(
            quote.charge_amount,
            (level_3.price_rub - level_1.price_rub) + Decimal("100.00"),
        )

    def test_purchase_quote_blocks_lower_plan_when_higher_level_is_active(self):
        level_5 = SubscriptionPlan.objects.get(level=5)
        level_4 = SubscriptionPlan.objects.get(level=4)
        assign_user_subscription_plan(user=self.user, plan=level_5)

        quote = get_subscription_purchase_quote(user=self.user, plan=level_4)

        self.assertTrue(quote.blocked_by_higher_level)
        self.assertEqual(quote.charge_amount, Decimal("0.00"))

    def test_purchase_plan_with_balance_writes_subscription_purchase_reason(self):
        user = User.objects.create_user(username="buyer_reason", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=2)
        adjust_balance(user=user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))

        purchase_subscription_with_balance(user=user, plan=plan)

        debit_entry = user.ledger_entries.order_by("-id").first()
        self.assertIsNotNone(debit_entry)
        self.assertEqual(debit_entry.reason, LedgerReason.SUBSCRIPTION_PURCHASE)
        self.assertEqual(debit_entry.delta, -plan.price_rub)
        self.assertEqual(debit_entry.amount_snapshot, plan.price_rub)


    def test_sync_feature_rules_to_plans_assigns_features_from_required_plan(self):
        feature = SubscriptionFeature.objects.get(code=TG_NOTIFIER_USER)
        level_2 = SubscriptionPlan.objects.get(level=2)
        rule = SubscriptionFeaturePricingRule.objects.get(feature=feature, family=SubscriptionPlan.Family.SITE)
        rule.required_plan = level_2
        rule.save()

        sync_feature_rules_to_plans(family=SubscriptionPlan.Family.SITE)

        level_1 = SubscriptionPlan.objects.get(level=1)
        level_5 = SubscriptionPlan.objects.get(level=5)
        self.assertFalse(level_1.features.filter(code=TG_NOTIFIER_USER).exists())
        self.assertTrue(level_2.features.filter(code=TG_NOTIFIER_USER).exists())
        self.assertTrue(level_5.features.filter(code=TG_NOTIFIER_USER).exists())


class SubscriptionInvariantTests(TestCase):
    def test_assign_user_subscription_plan_rejects_duplicate_active_family_state(self):
        user = User.objects.create_user(username="dup_sub_user", password="StrongPass123")
        first_plan = SubscriptionPlan.objects.get(level=1)
        second_plan = SubscriptionPlan.objects.get(level=2)
        now = timezone.now()
        expires_at = now + timedelta(days=30)

        UserSubscription.objects.bulk_create([
            UserSubscription(
                user=user,
                plan=first_plan,
                status=UserSubscription.Status.ACTIVE,
                starts_at=now,
                expires_at=expires_at,
                notes="seed-1",
            ),
            UserSubscription(
                user=user,
                plan=second_plan,
                status=UserSubscription.Status.ACTIVE,
                starts_at=now,
                expires_at=expires_at,
                notes="seed-2",
            ),
        ])

        with self.assertRaises(SubscriptionInvariantError):
            assign_user_subscription_plan(user=user, plan=second_plan)

        self.assertEqual(
            UserSubscription.objects.filter(
                user=user,
                status=UserSubscription.Status.ACTIVE,
                plan__family=first_plan.family,
            ).count(),
            2,
        )


class SubscriptionViewsTests(TestCase):
    def test_plans_page_renders_current_subscription(self):
        user = User.objects.create_user(username="plans_user", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=3)
        UserSubscription.objects.create(user=user, plan=plan)
        self.client.login(username="plans_user", password="StrongPass123")

        response = self.client.get(reverse("subscriptions:plans"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тарифы подписки")
        self.assertContains(response, plan.name)
        self.assertContains(response, "Текущий тариф")

    def test_farmer_plans_page_renders_farmer_line(self):
        response = self.client.get(reverse("subscriptions:farmer_plans"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Farmer-подписки")
        self.assertContains(response, "Farmer Start")

    def test_pricing_dashboard_renders_site_and_farmer_sections_for_admin(self):
        user = User.objects.create_user(username="admin_viewer", password="StrongPass123", is_staff=True, is_superuser=True)
        self.client.login(username="admin_viewer", password="StrongPass123")

        response = self.client.get(reverse("subscriptions:pricing_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Subscription control center")
        self.assertContains(response, "Site-планы")
        self.assertContains(response, "Farmer-планы")

    def test_pricing_dashboard_requires_admin_plus(self):
        user = User.objects.create_user(username="viewer", password="StrongPass123")
        self.client.login(username="viewer", password="StrongPass123")
        response = self.client.get(reverse("subscriptions:pricing_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_purchase_plan_with_balance_creates_subscription_and_debits_profile(self):
        user = User.objects.create_user(username="buyer", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=2)
        adjust_balance(user=user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))
        self.client.login(username="buyer", password="StrongPass123")

        response = self.client.post(reverse("subscriptions:purchase_plan", args=[plan.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.profile_v2.balance_coins, Decimal("500.00") - plan.price_rub)
        active = UserSubscription.objects.get(user=user, status=UserSubscription.Status.ACTIVE, plan__family=plan.family)
        self.assertEqual(active.plan_id, plan.id)
        self.assertContains(response, "успешно куплен")

    def test_purchase_plan_with_balance_rejects_when_insufficient_funds(self):
        user = User.objects.create_user(username="poor_buyer", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=3)
        adjust_balance(user=user, delta=Decimal("10.00"), reason="topup", amount_snapshot=Decimal("10.00"))
        self.client.login(username="poor_buyer", password="StrongPass123")

        response = self.client.post(reverse("subscriptions:purchase_plan", args=[plan.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.profile_v2.balance_coins, Decimal("10.00"))
        self.assertFalse(
            UserSubscription.objects.filter(
                user=user,
                status=UserSubscription.Status.ACTIVE,
                plan__family=plan.family,
            ).exists()
        )
        self.assertContains(response, "Недостаточно средств")

    def test_purchase_plan_with_balance_rejects_same_active_plan(self):
        user = User.objects.create_user(username="duplicate_buyer", password="StrongPass123")
        plan = SubscriptionPlan.objects.get(level=1)
        adjust_balance(user=user, delta=Decimal("500.00"), reason="topup", amount_snapshot=Decimal("500.00"))
        assign_user_subscription_plan(user=user, plan=plan)
        starting_balance = user.profile_v2.balance_coins
        self.client.login(username="duplicate_buyer", password="StrongPass123")

        response = self.client.post(reverse("subscriptions:purchase_plan", args=[plan.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.profile_v2.balance_coins, starting_balance)
        self.assertContains(response, "уже активен")

    def test_purchase_plan_with_balance_blocks_downgrade_when_higher_level_is_active(self):
        user = User.objects.create_user(username="high_level_user", password="StrongPass123")
        higher_plan = SubscriptionPlan.objects.get(level=5)
        lower_plan = SubscriptionPlan.objects.get(level=4)
        adjust_balance(user=user, delta=Decimal("1000.00"), reason="topup", amount_snapshot=Decimal("1000.00"))
        assign_user_subscription_plan(user=user, plan=higher_plan)
        starting_balance = user.profile_v2.balance_coins
        self.client.login(username="high_level_user", password="StrongPass123")

        response = self.client.post(reverse("subscriptions:purchase_plan", args=[lower_plan.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.profile_v2.balance_coins, starting_balance)
        active = UserSubscription.objects.get(user=user, status=UserSubscription.Status.ACTIVE, plan__family=higher_plan.family)
        self.assertEqual(active.plan_id, higher_plan.id)
        self.assertContains(response, "У вас действует подписка более высокого уровня")

    def test_purchase_plan_with_balance_upgrade_uses_price_delta_plus_switch_fee(self):
        user = User.objects.create_user(username="upgrade_user", password="StrongPass123")
        old_plan = SubscriptionPlan.objects.get(level=1)
        new_plan = SubscriptionPlan.objects.get(level=3)
        assign_user_subscription_plan(user=user, plan=old_plan)
        charge_amount = (new_plan.price_rub - old_plan.price_rub) + Decimal("100.00")
        adjust_balance(user=user, delta=charge_amount, reason="topup", amount_snapshot=charge_amount)
        self.client.login(username="upgrade_user", password="StrongPass123")

        response = self.client.post(reverse("subscriptions:purchase_plan", args=[new_plan.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.profile_v2.balance_coins, Decimal("0.00"))

        active = UserSubscription.objects.get(
            user=user,
            status=UserSubscription.Status.ACTIVE,
            plan__family=new_plan.family,
        )
        self.assertEqual(active.plan_id, new_plan.id)
        self.assertEqual(active.expires_at - active.starts_at, timedelta(days=new_plan.billing_period_days))
        self.assertContains(response, "успешно активирован")

    def test_plans_page_shows_higher_level_warning_for_lower_plan_cards(self):
        user = User.objects.create_user(username="ui_warn_user", password="StrongPass123")
        highest_plan = SubscriptionPlan.objects.get(level=5)
        assign_user_subscription_plan(user=user, plan=highest_plan)
        self.client.login(username="ui_warn_user", password="StrongPass123")

        response = self.client.get(reverse("subscriptions:plans"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "У вас действует подписка более высокого уровня.")

    def test_balance_topup_placeholder_page_renders(self):
        user = User.objects.create_user(username="topup_user", password="StrongPass123")
        self.client.login(username="topup_user", password="StrongPass123")

        response = self.client.get(reverse("subscriptions:balance_topup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пополнение баланса")