from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.accounts.models import UserSession
from modules.v2.case_simulator import services as sim_services
from modules.v2.case_simulator.models import SimCase, SimCaseItem, SimOpening
from modules.v2.control_center.models import AdminAuditLog, AuditAction
from modules.v2.notifications.models import Notification
from modules.v2.payments.models import Payment
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import (
    UPGRADE_SWITCH_FEE_RUB,
    assign_user_subscription_plan,
    get_subscription_purchase_quote,
    purchase_subscription_with_balance,
)

from . import services
from .models import Referral, ReferralBonus, ReferralCode
from .notifications import KIND_REFERRAL_COUNTED
from .pipeline import bind_referral

User = get_user_model()


def make_user(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass12345")
    UserProfile.objects.get_or_create(user=user)
    return user


def seen_on(user, ip: str) -> None:
    now = timezone.now()
    UserSession.objects.create(user=user, session_key=f"{user.pk}-{ip}".ljust(40, "x")[:40], ip_address=ip, last_ip=ip, created_at=now, last_seen_at=now)


class ReferralTestCase(TestCase):
    def setUp(self):
        self.referrer = make_user("ref_owner")
        self.code = services.get_or_create_code(self.referrer)

    def invite(self, username: str, *, ip: str = "203.0.113.50") -> User:
        user = make_user(username)
        referral = services.bind_new_user(user=user, code=self.code.code, ip_address=ip)
        self.assertIsNotNone(referral)
        return user

    def top_up(self, user, amount: str, *, is_test: bool = False) -> None:
        amount = Decimal(amount)
        payment = Payment.objects.create(user=user, provider="yookassa", amount=amount, status=Payment.Status.SUCCEEDED, is_test=is_test, paid_at=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):
            entry = adjust_balance(user=user, delta=amount, reason=LedgerReason.TOPUP, amount_snapshot=amount, idempotency_key=f"payment:{payment.public_id}")
            Payment.objects.filter(pk=payment.pk).update(ledger_entry=entry)

    def qualify(self, count: int, *, prefix: str = "friend") -> list[User]:
        users = []
        for index in range(count):
            user = self.invite(f"{prefix}{index}", ip=f"198.51.100.{index % 250 + 1}")
            self.top_up(user, "100")
            users.append(user)
        return users


class LinkAndBindingTests(ReferralTestCase):
    def test_link_sets_cookie_and_counts_click(self):
        response = Client().get(reverse("referral_link", args=[self.code.code.lower()]))
        self.assertRedirects(response, reverse("frontend:home"), fetch_redirect_response=False)
        self.assertEqual(response.cookies[services.COOKIE_NAME].value, self.code.code)
        self.assertEqual(ReferralCode.objects.get(pk=self.code.pk).clicks, 1)

    def test_unknown_code_sets_nothing(self):
        response = Client().get(reverse("referral_link", args=["NOPE1234"]))
        self.assertNotIn(services.COOKIE_NAME, response.cookies)

    def test_pipeline_binds_only_new_accounts(self):
        def strategy_for(code):
            request = SimpleNamespace(COOKIES={services.COOKIE_NAME: code}, META={"REMOTE_ADDR": "203.0.113.9"})
            return SimpleNamespace(request=request, session_get=lambda key: None, session_pop=lambda key: None)

        old_user = make_user("old_account")
        bind_referral(strategy_for(self.code.code), backend=None, user=old_user, is_new=False)
        self.assertFalse(Referral.objects.filter(referee=old_user).exists())

        new_user = make_user("new_account")
        bind_referral(strategy_for(self.code.code), backend=None, user=new_user, is_new=True)
        referral = Referral.objects.get(referee=new_user)
        self.assertEqual(referral.referrer, self.referrer)
        self.assertEqual(referral.ip_address, "203.0.113.9")

    def test_no_self_referral_and_no_rebinding(self):
        self.assertIsNone(services.bind_new_user(user=self.referrer, code=self.code.code))
        user = self.invite("once")
        other = make_user("other_ref")
        other_code = services.get_or_create_code(other)
        self.assertIsNone(services.bind_new_user(user=user, code=other_code.code))
        self.assertEqual(Referral.objects.get(referee=user).referrer, self.referrer)

    def test_shared_ip_puts_referral_on_review(self):
        seen_on(self.referrer, "95.24.10.77")
        user = self.invite("twin", ip="95.24.10.77")
        referral = Referral.objects.get(referee=user)
        self.assertEqual(referral.review, Referral.Review.FLAGGED)
        self.top_up(user, "100")
        referral.refresh_from_db()
        self.assertTrue(referral.is_qualified)
        self.assertFalse(referral.counts)
        self.assertEqual(services.subscription_discount_percent(self.referrer), 0)

    def test_local_ips_are_not_compared(self):
        seen_on(self.referrer, "127.0.0.1")
        user = self.invite("local", ip="127.0.0.1")
        self.assertEqual(Referral.objects.get(referee=user).review, Referral.Review.OK)


class QualificationTests(ReferralTestCase):
    def test_counts_after_100_rub_of_real_top_ups(self):
        user = self.invite("payer")
        self.top_up(user, "60")
        self.assertIsNone(Referral.objects.get(referee=user).qualified_at)
        self.top_up(user, "40")
        self.assertIsNotNone(Referral.objects.get(referee=user).qualified_at)
        self.assertEqual(services.subscription_discount_percent(self.referrer), 1)
        notification = Notification.objects.get(recipient=self.referrer, kind=KIND_REFERRAL_COUNTED)
        self.assertEqual(notification.payload["discount"], 1)

    def test_test_payments_do_not_count(self):
        user = self.invite("tester")
        self.top_up(user, "500", is_test=True)
        self.assertIsNone(Referral.objects.get(referee=user).qualified_at)
        with override_settings(REFERRAL_COUNT_TEST_PAYMENTS=True):
            services.check_qualification(user)
        self.assertIsNotNone(Referral.objects.get(referee=user).qualified_at)

    def test_discount_is_capped(self):
        with mock.patch.object(services, "counted_count", return_value=80):
            self.assertEqual(services.subscription_discount_percent(self.referrer), 50)
        self.assertEqual(services.turnover_percent_for(50), 0)
        self.assertEqual(services.turnover_percent_for(53), 3)
        self.assertEqual(services.turnover_percent_for(80), 10)


class SubscriptionDiscountTests(ReferralTestCase):
    def setUp(self):
        super().setUp()
        family = SubscriptionPlan.Family.SITE
        self.basic = SubscriptionPlan.objects.get(family=family, level=1)
        self.pro = SubscriptionPlan.objects.get(family=family, level=2)
        SubscriptionPlan.objects.filter(pk=self.basic.pk).update(price_rub=Decimal("500.00"), is_active=True)
        SubscriptionPlan.objects.filter(pk=self.pro.pk).update(price_rub=Decimal("900.00"), is_active=True)
        self.basic.refresh_from_db()
        self.pro.refresh_from_db()

    def test_new_purchase_is_discounted(self):
        self.qualify(3)
        quote = get_subscription_purchase_quote(user=self.referrer, plan=self.basic)
        self.assertEqual(quote.discount_percent, 3)
        self.assertEqual(quote.full_amount, Decimal("500.00"))
        self.assertEqual(quote.charge_amount, Decimal("485.00"))

        adjust_balance(user=self.referrer, delta=Decimal("485.00"), reason=LedgerReason.ADMIN_ADJUST)
        purchase_subscription_with_balance(user=self.referrer, plan=self.basic)
        self.assertEqual(UserProfile.objects.get(user=self.referrer).balance_coins, Decimal("0.00"))

    def test_upgrade_discount_skips_switch_fee(self):
        self.qualify(10)
        assign_user_subscription_plan(user=self.referrer, plan=self.basic, family=self.basic.family)
        quote = get_subscription_purchase_quote(user=self.referrer, plan=self.pro)
        self.assertTrue(quote.is_upgrade)
        self.assertEqual(quote.charge_amount, Decimal("360.00") + UPGRADE_SWITCH_FEE_RUB)  # 400 − 10 %, fee in full
        self.assertEqual(quote.full_amount, Decimal("400.00") + UPGRADE_SWITCH_FEE_RUB)

    def test_no_referrals_no_discount(self):
        quote = get_subscription_purchase_quote(user=self.referrer, plan=self.basic)
        self.assertEqual((quote.discount_percent, quote.charge_amount), (0, Decimal("500.00")))


class TurnoverBonusTests(ReferralTestCase):
    def setUp(self):
        super().setUp()
        self.case = SimCase.objects.create(slug="ref-case", name="Ref case", case_price=Decimal("100"), key_price=Decimal("0"))
        self.item = SimCaseItem.objects.create(case=self.case, name="AK-47 | Test", weapon="AK-47", rarity="covert", value=Decimal("1000"), min_float=Decimal("0"), max_float=Decimal("1"))

    def sell(self, user, value: str) -> None:
        opening = SimOpening.objects.create(user=user, case=self.case, item=self.item, price_paid=Decimal("100"), item_value=Decimal(value))
        with self.captureOnCommitCallbacks(execute=True):
            sim_services.sell_openings(user=user, opening_ids=[opening.pk])

    def test_no_bonus_until_the_discount_is_maxed(self):
        seller = self.qualify(1)[0]
        self.sell(seller, "1000")
        self.assertFalse(ReferralBonus.objects.exists())

    def test_bonus_after_the_cap(self):
        with override_settings(REFERRAL_DISCOUNT_MAX_PERCENT=2):
            sellers = self.qualify(5)  # 3 above the cap → 3 %
            self.sell(sellers[0], "1000")
            bonus = ReferralBonus.objects.get()
            self.assertEqual((bonus.percent, bonus.amount), (Decimal("3"), Decimal("30.00")))
            entry = LedgerEntry.objects.get(user=self.referrer, reason=LedgerReason.REFERRAL_BONUS)
            self.assertEqual(entry.delta, Decimal("30.00"))
            self.assertEqual(Referral.objects.get(referee=sellers[0]).bonus_total, Decimal("30.00"))

            # The safety net does not pay the same sale twice.
            self.assertEqual(services.reconcile()["bonuses"], 0)
            self.assertEqual(ReferralBonus.objects.count(), 1)

    def test_reconcile_pays_a_missed_bonus(self):
        with override_settings(REFERRAL_DISCOUNT_MAX_PERCENT=1):
            sellers = self.qualify(2)
            opening = SimOpening.objects.create(user=sellers[0], case=self.case, item=self.item, price_paid=Decimal("100"), item_value=Decimal("500"))
            sim_services.sell_openings(user=sellers[0], opening_ids=[opening.pk])  # on-commit hook never ran
            self.assertFalse(ReferralBonus.objects.exists())
            self.assertEqual(services.reconcile()["bonuses"], 1)
            self.assertEqual(ReferralBonus.objects.get().amount, Decimal("5.00"))


class PagesTests(ReferralTestCase):
    def test_dashboard_shows_link_and_referrals(self):
        self.qualify(2)
        self.invite("pending_one")
        client = Client()
        client.force_login(self.referrer)
        response = client.get(reverse("referrals:dashboard"))
        self.assertContains(response, f"/r/{self.code.code}/")
        self.assertContains(response, "ждём пополнения")
        self.assertEqual(response.context["counted"], 2)
        self.assertEqual(response.context["discount"], 2)

    def test_admin_reviews_a_flagged_referral(self):
        seen_on(self.referrer, "95.24.10.5")
        user = self.invite("suspect", ip="95.24.10.5")
        self.top_up(user, "100")
        admin = make_user("ref_admin")
        profile = UserProfile.objects.get(user=admin)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        client = Client()
        client.force_login(admin)

        response = client.get(reverse("control_center:referrals") + "?status=flagged")
        self.assertContains(response, "suspect")

        referral = Referral.objects.get(referee=user)
        client.post(reverse("control_center:referral_review", args=[referral.pk]), {"decision": "ok"})
        referral.refresh_from_db()
        self.assertTrue(referral.counts)
        self.assertEqual(services.subscription_discount_percent(self.referrer), 1)
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.REFERRAL_REVIEW, actor=admin).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.referrer, kind=KIND_REFERRAL_COUNTED).exists())

        # A clean decision sticks: a later top-up does not flag it again.
        self.top_up(user, "100")
        self.assertEqual(Referral.objects.get(pk=referral.pk).review, Referral.Review.OK)

    def test_regular_user_cannot_open_the_admin_tab(self):
        client = Client()
        client.force_login(self.referrer)
        self.assertEqual(client.get(reverse("control_center:referrals")).status_code, 403)
