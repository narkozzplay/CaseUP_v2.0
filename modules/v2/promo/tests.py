from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.case_simulator import services as sim_services
from modules.v2.case_simulator.models import SimCase, SimCaseItem, SimOpening
from modules.v2.control_center.models import AdminAuditLog, AuditAction
from modules.v2.payments.models import Payment
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole
from modules.v2.profiles.services.balance import adjust_balance
from modules.v2.referrals.models import Referral
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import get_subscription_purchase_quote, purchase_subscription_with_balance

from . import services
from .models import PromoActivation, PromoCode

User = get_user_model()


def make_user(username: str, *, joined_days_ago: int = 0) -> User:
    user = User.objects.create_user(username=username, password="pass12345")
    if joined_days_ago:
        User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=joined_days_ago))
        user.refresh_from_db()
    UserProfile.objects.get_or_create(user=user)
    return user


def balance(user) -> Decimal:
    return UserProfile.objects.get(user=user).balance_coins


class PromoTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("promo_user")

    def top_up(self, user, amount: str, *, is_test: bool = False) -> None:
        amount = Decimal(amount)
        payment = Payment.objects.create(user=user, provider="yookassa", amount=amount, status=Payment.Status.SUCCEEDED, is_test=is_test, paid_at=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):
            entry = adjust_balance(user=user, delta=amount, reason=LedgerReason.TOPUP, amount_snapshot=amount, idempotency_key=f"payment:{payment.public_id}")
            Payment.objects.filter(pk=payment.pk).update(ledger_entry=entry)


class BalanceBonusTests(PromoTestCase):
    def test_credits_once_per_account_and_respects_the_limit(self):
        promo = PromoCode.objects.create(code="CASEUP50", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("50"), max_activations=2)
        result = services.activate(user=self.user, raw_code=" caseup50 ")
        self.assertIn("50.00", result.message)
        self.assertEqual(balance(self.user), Decimal("50.00"))
        self.assertEqual(LedgerEntry.objects.get(user=self.user).reason, LedgerReason.PROMO_BONUS)
        with self.assertRaisesMessage(services.PromoError, "уже активировали"):
            services.activate(user=self.user, raw_code="CASEUP50")

        services.activate(user=make_user("second"), raw_code="CASEUP50")
        with self.assertRaisesMessage(services.PromoError, "максимальное число раз"):
            services.activate(user=make_user("third"), raw_code="CASEUP50")
        self.assertEqual(PromoCode.objects.get(pk=promo.pk).activations_count, 2)

    def test_limits(self):
        PromoCode.objects.create(code="OLD", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("10"), expires_at=timezone.now() - timedelta(hours=1))
        PromoCode.objects.create(code="SOON", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("10"), starts_at=timezone.now() + timedelta(days=1))
        PromoCode.objects.create(code="OFF", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("10"), is_active=False)
        PromoCode.objects.create(code="NEWBIE", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("10"), only_new_accounts=True)
        PromoCode.objects.create(code="FIRSTPAY", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("10"), only_without_topups=True)
        veteran = make_user("veteran", joined_days_ago=60)
        self.top_up(veteran, "100")
        for code, text in (("OLD", "истёк"), ("SOON", "ещё не начал"), ("OFF", "больше не действует"), ("NEWBIE", "новых аккаунтов"), ("FIRSTPAY", "не пополнял")):
            with self.subTest(code=code), self.assertRaisesMessage(services.PromoError, text):
                services.activate(user=veteran, raw_code=code)
        services.activate(user=self.user, raw_code="NEWBIE")  # registered today

    def test_wrong_codes_are_rate_limited(self):
        for _ in range(services.MAX_WRONG_CODES):
            with self.assertRaises(services.PromoError):
                services.activate(user=self.user, raw_code="NOPE")
        PromoCode.objects.create(code="REAL", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("10"))
        with self.assertRaisesMessage(services.PromoError, "Слишком много"):
            services.activate(user=self.user, raw_code="REAL")


class TopupBonusTests(PromoTestCase):
    def setUp(self):
        super().setUp()
        PromoCode.objects.create(code="PLUS10", kind=PromoCode.Kind.TOPUP_BONUS, percent=10, min_topup=Decimal("300"), max_bonus=Decimal("100"))

    def test_bonus_on_the_next_qualifying_top_up(self):
        services.activate(user=self.user, raw_code="PLUS10")
        self.top_up(self.user, "200")  # below the minimum
        self.assertEqual(balance(self.user), Decimal("200.00"))
        self.top_up(self.user, "500")
        self.assertEqual(balance(self.user), Decimal("750.00"))  # +50
        activation = PromoActivation.objects.get(user=self.user)
        self.assertEqual((activation.status, activation.benefit_amount), (PromoActivation.Status.USED, Decimal("50.00")))
        self.top_up(self.user, "500")  # used up: no more bonus
        self.assertEqual(balance(self.user), Decimal("1250.00"))

    def test_bonus_is_capped_and_test_payments_do_not_count(self):
        services.activate(user=self.user, raw_code="PLUS10")
        self.top_up(self.user, "5000", is_test=True)
        self.assertEqual(balance(self.user), Decimal("5000.00"))
        self.top_up(self.user, "5000")
        self.assertEqual(balance(self.user), Decimal("10100.00"))  # capped at 100

    def test_only_one_pending_code_of_a_kind(self):
        PromoCode.objects.create(code="PLUS20", kind=PromoCode.Kind.TOPUP_BONUS, percent=20)
        services.activate(user=self.user, raw_code="PLUS10")
        with self.assertRaisesMessage(services.PromoError, "неиспользованный промокод этого типа"):
            services.activate(user=self.user, raw_code="PLUS20")

    def test_topup_page_shows_the_pending_code(self):
        services.activate(user=self.user, raw_code="PLUS10")
        client = Client()
        client.force_login(self.user)
        self.assertContains(client.get(reverse("payments:topup")), "Активен промокод")


class SubscriptionDiscountTests(PromoTestCase):
    def setUp(self):
        super().setUp()
        self.plan = SubscriptionPlan.objects.get(code="site_no_ads")
        SubscriptionPlan.objects.filter(pk=self.plan.pk).update(price_rub=Decimal("1000.00"), is_active=True)
        self.plan.refresh_from_db()
        PromoCode.objects.create(code="SUB30", kind=PromoCode.Kind.SUBSCRIPTION_DISCOUNT, percent=30)

    def test_discount_is_used_by_the_purchase(self):
        services.activate(user=self.user, raw_code="SUB30")
        quote = get_subscription_purchase_quote(user=self.user, plan=self.plan)
        self.assertEqual((quote.charge_amount, quote.discount_source), (Decimal("700.00"), "promo"))
        adjust_balance(user=self.user, delta=Decimal("700"), reason=LedgerReason.ADMIN_ADJUST)
        purchase_subscription_with_balance(user=self.user, plan=self.plan)
        activation = PromoActivation.objects.get(user=self.user)
        self.assertEqual((activation.status, activation.benefit_amount), (PromoActivation.Status.USED, Decimal("300.00")))
        self.assertEqual(balance(self.user), Decimal("0.00"))

    def test_larger_referral_discount_wins_and_keeps_the_code(self):
        services.activate(user=self.user, raw_code="SUB30")
        from unittest import mock

        with mock.patch("modules.v2.referrals.services.subscription_discount_percent", return_value=40):
            quote = get_subscription_purchase_quote(user=self.user, plan=self.plan)
            self.assertEqual((quote.charge_amount, quote.discount_source), (Decimal("600.00"), "referral"))
            adjust_balance(user=self.user, delta=Decimal("600"), reason=LedgerReason.ADMIN_ADJUST)
            purchase_subscription_with_balance(user=self.user, plan=self.plan)
        self.assertEqual(PromoActivation.objects.get(user=self.user).status, PromoActivation.Status.PENDING)

    def test_family_restriction(self):
        PromoCode.objects.create(code="FARM20", kind=PromoCode.Kind.SUBSCRIPTION_DISCOUNT, percent=20, subscription_family="farmer")
        other = make_user("farmer_fan")
        services.activate(user=other, raw_code="FARM20")
        self.assertEqual(get_subscription_purchase_quote(user=other, plan=self.plan).charge_amount, Decimal("1000.00"))


class FreeCaseTests(PromoTestCase):
    def setUp(self):
        super().setUp()
        self.case = SimCase.objects.create(slug="promo-case", name="Promo case", case_price=Decimal("150"), key_price=Decimal("0"))
        SimCaseItem.objects.create(case=self.case, name="P250 | Test", weapon="P250", rarity="milspec", value=Decimal("10"), min_float=Decimal("0"), max_float=Decimal("1"))
        PromoCode.objects.create(code="FREECASE", kind=PromoCode.Kind.FREE_CASE, case=self.case)

    def test_one_free_opening(self):
        services.activate(user=self.user, raw_code="FREECASE")
        client = Client()
        client.force_login(self.user)
        self.assertContains(client.get(reverse("case_simulator:detail", args=[self.case.slug])), "бесплатно")

        response = client.post(reverse("case_simulator:open", args=[self.case.slug]), {"count": "1"})
        payload = response.json()
        self.assertTrue(payload["ok"], payload)
        self.assertEqual((Decimal(payload["spent"]), payload["free_used"]), (Decimal("0"), True))
        self.assertEqual(SimOpening.objects.get(user=self.user).price_paid, Decimal("0"))
        self.assertEqual(PromoActivation.objects.get(user=self.user).status, PromoActivation.Status.USED)

        # The next one costs money again (and the balance is empty).
        response = client.post(reverse("case_simulator:open", args=[self.case.slug]), {"count": "1"})
        self.assertFalse(response.json()["ok"])

    def test_free_opening_inside_a_multi_open(self):
        services.activate(user=self.user, raw_code="FREECASE")
        adjust_balance(user=self.user, delta=Decimal("150"), reason=LedgerReason.ADMIN_ADJUST)
        openings = sim_services.open_case(user=self.user, case=self.case, count=2)
        self.assertEqual(sorted(opening.price_paid for opening in openings), [Decimal("0"), Decimal("150.00")])
        self.assertEqual(balance(self.user), Decimal("0.00"))


class BloggerCodeTests(PromoTestCase):
    def test_fresh_account_becomes_the_bloggers_referral(self):
        blogger = make_user("blogger")
        PromoCode.objects.create(code="NARKOZZ", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("25"), owner=blogger)
        result = services.activate(user=self.user, raw_code="narkozz")
        self.assertEqual(result.became_referral_of, blogger)
        self.assertEqual(Referral.objects.get(referee=self.user).referrer, blogger)

        old = make_user("old_timer", joined_days_ago=90)
        services.activate(user=old, raw_code="NARKOZZ")
        self.assertFalse(Referral.objects.filter(referee=old).exists())

        with self.assertRaisesMessage(services.PromoError, "Свой промокод"):
            services.activate(user=blogger, raw_code="NARKOZZ")


class PagesTests(PromoTestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("promo_admin")
        profile = UserProfile.objects.get(user=self.admin)
        profile.role = UserRole.ADMIN
        profile.save(update_fields=["role"])
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)

    def test_user_page_activates_a_code(self):
        PromoCode.objects.create(code="HELLO", kind=PromoCode.Kind.BALANCE_BONUS, amount=Decimal("15"))
        client = Client()
        client.force_login(self.user)
        self.assertContains(client.get(reverse("promo:dashboard") + "?code=hello"), 'value="HELLO"')
        response = client.post(reverse("promo:dashboard"), {"code": "hello"}, follow=True)
        self.assertContains(response, "зачислено 15.00")
        self.assertContains(response, "использован")

    def test_admin_creates_and_edits_a_code(self):
        response = self.admin_client.post(reverse("control_center:promo_create"), {
            "code": "stream-50", "kind": "topup_bonus", "percent": "50", "min_topup": "500", "max_bonus": "", "amount": "999",
            "subscription_family": "", "title": "Стрим", "owner_username": "", "max_activations": "100", "is_active": "on",
        })
        promo = PromoCode.objects.get(code="STREAM-50")
        self.assertRedirects(response, reverse("control_center:promo_edit", args=[promo.pk]), fetch_redirect_response=False)
        self.assertIsNone(promo.amount)  # irrelevant field for this kind is cleared
        self.assertEqual(promo.created_by, self.admin)
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.PROMO_CREATE, actor=self.admin).exists())

        self.assertContains(self.admin_client.get(reverse("control_center:promo")), "STREAM-50")
        services.activate(user=self.user, raw_code="STREAM-50")
        page = self.admin_client.get(reverse("control_center:promo_edit", args=[promo.pk]))
        self.assertTrue(page.context["form"].fields["kind"].disabled)

    def test_form_validation(self):
        response = self.admin_client.post(reverse("control_center:promo_create"), {"code": "x", "kind": "subscription_discount", "percent": "95", "is_active": "on"})
        form = response.context["form"]
        self.assertIn("code", form.errors)
        self.assertIn("percent", form.errors)
        self.assertFalse(PromoCode.objects.exists())

    def test_admin_cancels_a_pending_activation(self):
        PromoCode.objects.create(code="LATER", kind=PromoCode.Kind.TOPUP_BONUS, percent=5)
        result = services.activate(user=self.user, raw_code="LATER")
        self.admin_client.post(reverse("control_center:promo_activation_cancel", args=[result.activation.pk]))
        self.assertEqual(PromoActivation.objects.get(pk=result.activation.pk).status, PromoActivation.Status.CANCELED)

    def test_regular_user_has_no_admin_access(self):
        client = Client()
        client.force_login(self.user)
        self.assertEqual(client.get(reverse("control_center:promo")).status_code, 403)
