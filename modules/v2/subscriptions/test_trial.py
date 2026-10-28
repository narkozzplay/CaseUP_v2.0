from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import UserProfile
from modules.v2.profiles.services.balance import adjust_balance

from .lifecycle import process_subscription_lifecycle, remind_expiring_subscriptions
from .models import SubscriptionPlan, UserSubscription
from .services import assign_user_subscription_plan, get_active_subscription, get_period_price, get_subscription_pricing_config
from .trial import TrialError, renew_due_subscriptions, set_auto_renew, start_trial, trial_offer

User = get_user_model()


def balance(user) -> Decimal:
    return UserProfile.objects.get(user=user).balance_coins


class TrialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="trial_user", password="StrongPass123")
        self.plus = SubscriptionPlan.objects.get(code="site_plus")
        adjust_balance(user=self.user, delta=Decimal("10.00"), reason="topup", amount_snapshot=Decimal("10.00"))

    def test_trial_costs_one_ruble_and_turns_auto_renew_on(self):
        subscription = start_trial(self.user, accepted_auto_renew=True)

        self.assertEqual(balance(self.user), Decimal("9.00"))
        self.assertEqual(subscription.plan, self.plus)
        self.assertTrue(subscription.is_trial)
        self.assertTrue(subscription.auto_renew)
        self.assertAlmostEqual(subscription.expires_at, timezone.now() + timedelta(days=7), delta=timedelta(minutes=1))

    def test_trial_needs_consent_and_is_once_per_user(self):
        with self.assertRaises(TrialError):
            start_trial(self.user, accepted_auto_renew=False)
        self.assertEqual(balance(self.user), Decimal("10.00"))

        start_trial(self.user, accepted_auto_renew=True)
        self.assertIsNone(trial_offer(self.user))
        with self.assertRaises(TrialError):
            start_trial(self.user, accepted_auto_renew=True)

    def test_no_trial_after_a_paid_subscription_or_when_switched_off(self):
        other = User.objects.create_user(username="paid_before")
        assign_user_subscription_plan(user=other, plan=self.plus, starts_at=timezone.now() - timedelta(days=31), expires_at=timezone.now() - timedelta(days=1))
        self.assertIsNone(trial_offer(other))
        self.assertIsNotNone(trial_offer(self.user))

        config = get_subscription_pricing_config()
        config.trial_enabled = False
        config.save()
        self.assertIsNone(trial_offer(self.user))

    def test_trial_needs_the_price_on_balance(self):
        poor = User.objects.create_user(username="poor")
        with self.assertRaises(TrialError):
            start_trial(poor, accepted_auto_renew=True)
        self.assertFalse(UserSubscription.objects.filter(user=poor).exists())

    def test_renewal_charges_the_monthly_price_and_extends(self):
        subscription = start_trial(self.user, accepted_auto_renew=True)
        price = get_period_price(self.plus, 1)
        adjust_balance(user=self.user, delta=price, reason="topup", amount_snapshot=price)
        old_end = subscription.expires_at

        result = renew_due_subscriptions(now=old_end - timedelta(hours=1))

        self.assertEqual(result, {"renewed": 1, "renew_failed": 0})
        subscription.refresh_from_db()
        self.assertEqual(balance(self.user), Decimal("9.00"))
        self.assertFalse(subscription.is_trial)
        self.assertTrue(subscription.auto_renew)
        self.assertEqual(subscription.expires_at, old_end + timedelta(days=self.plus.billing_period_days))
        self.assertTrue(Notification.objects.filter(recipient=self.user, kind="subscription_renewal").exists())

    def test_renewal_is_not_due_early(self):
        subscription = start_trial(self.user, accepted_auto_renew=True)
        self.assertEqual(renew_due_subscriptions(now=subscription.expires_at - timedelta(days=1)), {"renewed": 0, "renew_failed": 0})

    def test_short_balance_notifies_once_and_lets_it_expire(self):
        subscription = start_trial(self.user, accepted_auto_renew=True)
        due = subscription.expires_at - timedelta(hours=1)

        self.assertEqual(renew_due_subscriptions(now=due)["renew_failed"], 1)
        renew_due_subscriptions(now=due + timedelta(minutes=30))
        self.assertEqual(Notification.objects.filter(recipient=self.user, kind="subscription_renewal").count(), 1)

        result = process_subscription_lifecycle(now=subscription.expires_at + timedelta(minutes=1))
        self.assertEqual(result["expired"], 1)
        self.assertEqual(balance(self.user), Decimal("9.00"))

    def test_switched_off_auto_renew_is_not_charged(self):
        subscription = start_trial(self.user, accepted_auto_renew=True)
        price = get_period_price(self.plus, 1)
        adjust_balance(user=self.user, delta=price, reason="topup", amount_snapshot=price)
        set_auto_renew(self.user, enabled=False)

        renew_due_subscriptions(now=subscription.expires_at - timedelta(hours=1))

        self.assertEqual(balance(self.user), Decimal("9.00") + price)
        self.assertFalse(get_active_subscription(self.user).auto_renew)

    def test_reminder_names_the_upcoming_charge(self):
        subscription = start_trial(self.user, accepted_auto_renew=True)
        remind_expiring_subscriptions(now=subscription.expires_at - timedelta(days=2))
        notice = Notification.objects.get(recipient=self.user, kind="subscription_expiring")
        self.assertIn("автопродление", str(notice.payload).lower())


class TrialViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="trial_view", password="StrongPass123")
        adjust_balance(user=self.user, delta=Decimal("5.00"), reason="topup", amount_snapshot=Decimal("5.00"))
        self.client.login(username="trial_view", password="StrongPass123")

    def test_plans_page_offers_trial_and_form_starts_it(self):
        response = self.client.get(reverse("subscriptions:plans"))
        self.assertContains(response, "Согласен на автопродление")

        self.client.post(reverse("subscriptions:start_trial"), {})
        self.assertIsNone(get_active_subscription(self.user))

        self.client.post(reverse("subscriptions:start_trial"), {"accept_auto_renew": "1"})
        self.assertTrue(get_active_subscription(self.user).is_trial)

        response = self.client.get(reverse("subscriptions:plans"))
        self.assertNotContains(response, "Согласен на автопродление")
        self.assertContains(response, "Отключить автопродление")

        self.client.post(reverse("subscriptions:auto_renew"), {"enabled": "0"})
        self.assertFalse(get_active_subscription(self.user).auto_renew)
