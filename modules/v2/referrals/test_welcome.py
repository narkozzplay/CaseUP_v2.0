from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from django.test import Client
from django.urls import reverse
from django.utils import timezone
from social_django.models import UserSocialAuth

from modules.v2.antifraud.models import MultiAccountCase, MultiAccountMember, UserIp
from modules.v2.control_center.models import AdminAuditLog, AuditAction
from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import UserProfile, UserRole
from modules.v2.subscriptions.models import SubscriptionPlan, UserSubscription
from modules.v2.subscriptions.services import assign_user_subscription_plan, get_active_subscription

from . import services, welcome
from .models import Referral, ReferralProgramSettings, ReferralWelcomeBonus
from .notifications import KIND_REFERRAL_WELCOME_BONUS
from .pipeline import bind_referral
from .tests import ReferralTestCase, make_user, seen_on

GRANTED = ReferralWelcomeBonus.Status.GRANTED
HELD = ReferralWelcomeBonus.Status.HELD
DENIED = ReferralWelcomeBonus.Status.DENIED

_steam_seq = iter(range(76561198000000000, 76561199000000000))


def link_steam(user, steam_id: str | None = None) -> str:
    steam_id = steam_id or str(next(_steam_seq))
    UserSocialAuth.objects.create(user=user, provider="steam", uid=steam_id)
    return steam_id


class WelcomeBonusTestCase(ReferralTestCase):
    def setUp(self):
        super().setUp()
        self.plus = SubscriptionPlan.objects.get(code="site_plus")
        self.pro = SubscriptionPlan.objects.get(code="site_pro")
        self.no_ads = SubscriptionPlan.objects.get(code="site_no_ads")

    def steam_invite(self, username: str, *, ip: str = "203.0.113.50", steam_id: str | None = None):
        """What the Steam pipeline does for a new account: associate the Steam uid, then bind."""
        user = make_user(username)
        link_steam(user, steam_id)
        services.bind_new_user(user=user, code=self.code.code, ip_address=ip)
        return user

    def bonus(self, user, role=ReferralWelcomeBonus.Role.REFEREE) -> ReferralWelcomeBonus:
        return ReferralWelcomeBonus.objects.get(user=user, role=role)

    def assert_days_left(self, user, days: float, plan=None):
        subscription = get_active_subscription(user)
        self.assertIsNotNone(subscription)
        if plan is not None:
            self.assertEqual(subscription.plan, plan)
        left = (subscription.expires_at - timezone.now()).total_seconds() / 86400
        self.assertAlmostEqual(left, days, delta=0.01)


class GrantTests(WelcomeBonusTestCase):
    def test_both_sides_get_plus_after_first_steam_sign_in(self):
        friend = self.steam_invite("friend")
        for user, role in ((friend, "referee"), (self.referrer, "referrer")):
            row = self.bonus(user, role)
            self.assertEqual((row.status, row.days, row.plan, row.mode), (GRANTED, 7, self.plus, "new"))
            self.assert_days_left(user, 7, self.plus)
            self.assertTrue(Notification.objects.filter(recipient=user, kind=KIND_REFERRAL_WELCOME_BONUS, payload__status="granted").exists())

    def test_existing_plus_or_higher_is_extended(self):
        now = timezone.now()
        assign_user_subscription_plan(user=self.referrer, plan=self.pro, expires_at=now + timedelta(days=10))
        self.steam_invite("friend")
        self.assertEqual(self.bonus(self.referrer, "referrer").mode, "extended")
        self.assert_days_left(self.referrer, 17, self.pro)
        self.assertEqual(UserSubscription.objects.filter(user=self.referrer).count(), 1)

    def test_lower_plan_is_upgraded_with_remaining_value(self):
        assign_user_subscription_plan(user=self.referrer, plan=self.no_ads, expires_at=timezone.now() + timedelta(days=30))
        self.steam_invite("friend")
        row = self.bonus(self.referrer, "referrer")
        self.assertEqual(row.mode, "upgraded")
        # 99 ₽ of «Без рекламы» left = 99/199 of a 30-day Plus month ≈ 14.9 days, plus the 7 bonus days.
        self.assertEqual(row.converted_days, 14)
        self.assert_days_left(self.referrer, 7 + 30 * 99 / 199, self.plus)

    def test_days_and_switch_come_from_settings(self):
        config = ReferralProgramSettings.load()
        config.welcome_bonus_days = 14
        config.save()
        friend = self.steam_invite("friend")
        self.assert_days_left(friend, 14)

        config.welcome_bonus_enabled = False
        config.save()
        other = self.steam_invite("other", ip="198.51.100.7")
        self.assertFalse(ReferralWelcomeBonus.objects.filter(user=other).exists())

    def test_referrals_from_before_the_program_get_nothing(self):
        config = ReferralProgramSettings.load()
        config.welcome_bonus_since = timezone.now() + timedelta(minutes=1)
        config.save()
        self.steam_invite("friend")
        self.assertFalse(ReferralWelcomeBonus.objects.exists())


class AbuseTests(WelcomeBonusTestCase):
    def test_one_bonus_per_steam_account(self):
        friend = self.steam_invite("friend", steam_id="76561198111111111")
        friend.delete()
        again = self.steam_invite("friend_again", steam_id="76561198111111111")
        self.assertTrue(Referral.objects.filter(referee=again).exists())
        self.assertFalse(ReferralWelcomeBonus.objects.filter(user=again).exists())
        self.assertEqual(ReferralWelcomeBonus.objects.filter(user=self.referrer).count(), 1)

    def test_no_bonus_without_steam(self):
        friend = self.invite("password_friend")
        self.assertFalse(ReferralWelcomeBonus.objects.filter(user=friend).exists())

    def test_later_steam_sign_in_of_a_bound_account_grants_it(self):
        friend = self.invite("late_steam")
        steam_id = link_steam(friend)
        strategy = SimpleNamespace(request=SimpleNamespace(COOKIES={}, META={"REMOTE_ADDR": "203.0.113.60"}), session_get=lambda key: None, session_pop=lambda key: None)
        bind_referral(strategy, backend=SimpleNamespace(name="steam"), user=friend, is_new=False, uid=steam_id)
        self.assertEqual(self.bonus(friend).status, GRANTED)
        # Signing in again changes nothing.
        bind_referral(strategy, backend=SimpleNamespace(name="steam"), user=friend, is_new=False, uid=steam_id)
        self.assertEqual(ReferralWelcomeBonus.objects.count(), 2)

    def test_shared_ip_holds_the_bonus_until_the_admin_decides(self):
        seen_on(self.referrer, "95.24.10.5")
        friend = self.steam_invite("suspect", ip="95.24.10.5")
        self.assertEqual(self.bonus(friend).status, HELD)
        self.assertIsNone(get_active_subscription(friend))
        self.assertTrue(Notification.objects.filter(recipient=friend, kind=KIND_REFERRAL_WELCOME_BONUS, payload__status="held").exists())

        admin = make_user("ref_admin")
        UserProfile.objects.filter(user=admin).update(role=UserRole.ADMIN)
        client = Client()
        client.force_login(admin)
        referral = Referral.objects.get(referee=friend)
        client.post(reverse("control_center:referral_review", args=[referral.pk]), {"decision": "ok"})
        self.assertEqual(self.bonus(friend).status, GRANTED)
        self.assertEqual(self.bonus(self.referrer, "referrer").status, GRANTED)
        self.assert_days_left(friend, 7)

    def test_rejected_referral_cancels_the_held_bonus(self):
        UserIp.objects.create(user=self.referrer, ip="95.24.10.6")
        friend = self.steam_invite("suspect", ip="95.24.10.6")
        referral = Referral.objects.get(referee=friend)
        self.assertEqual(referral.review, Referral.Review.FLAGGED)  # the antifraud log put it on review
        self.assertEqual(self.bonus(friend).status, HELD)
        admin = make_user("ref_admin")
        services.review_referral(actor=admin, referral=referral, decision=Referral.Review.BLOCKED)
        self.assertEqual(self.bonus(friend).status, DENIED)
        self.assertIsNone(get_active_subscription(self.referrer))

    def test_old_shared_ip_is_ignored(self):
        old = timezone.now() - timedelta(days=60)
        UserIp.objects.create(user=self.referrer, ip="95.24.10.11", first_seen=old, last_seen=old)
        friend = self.steam_invite("friend", ip="95.24.10.11")
        self.assertEqual(self.bonus(friend).status, GRANTED)

    def test_crowded_ip_is_not_treated_as_shared(self):
        # 7 others + the inviter = 8 recorded accounts; the friend signing in is the 9th, over the limit of 8.
        for index in range(7):
            UserIp.objects.create(user=make_user(f"mobile{index}"), ip="95.24.10.7")
        UserIp.objects.create(user=self.referrer, ip="95.24.10.7")
        friend = self.steam_invite("mobile_friend", ip="95.24.10.7")
        self.assertEqual(self.bonus(friend).status, GRANTED)

    def test_abusive_multi_account_gets_nothing(self):
        case = MultiAccountCase.objects.create(status=MultiAccountCase.Status.ABUSE)
        MultiAccountMember.objects.create(case=case, user=self.referrer)
        friend = self.steam_invite("friend")
        self.assertEqual(self.bonus(friend).status, DENIED)
        self.assertIsNone(get_active_subscription(friend))

    def test_referrer_monthly_limit(self):
        config = ReferralProgramSettings.load()
        config.referrer_monthly_limit = 2
        config.save()
        friends = [self.steam_invite(f"friend{index}", ip=f"198.51.100.{index + 1}") for index in range(3)]
        rows = ReferralWelcomeBonus.objects.filter(user=self.referrer).order_by("id")
        self.assertEqual([row.status for row in rows], [GRANTED, GRANTED, DENIED])
        self.assertEqual(self.bonus(friends[2]).status, GRANTED)  # the invited friend still gets theirs
        self.assert_days_left(self.referrer, 14)
        self.assertTrue(Notification.objects.filter(recipient=self.referrer, kind=KIND_REFERRAL_WELCOME_BONUS, payload__status="denied").exists())

    def test_limit_is_rechecked_when_a_held_bonus_is_released(self):
        config = ReferralProgramSettings.load()
        config.referrer_monthly_limit = 1
        config.save()
        seen_on(self.referrer, "95.24.10.8")
        suspect = self.steam_invite("suspect", ip="95.24.10.8")
        self.steam_invite("friend", ip="198.51.100.9")
        referral = Referral.objects.get(referee=suspect)
        services.review_referral(actor=make_user("ref_admin"), referral=referral, decision=Referral.Review.OK)
        self.assertEqual(ReferralWelcomeBonus.objects.get(referral=referral, role="referrer").status, DENIED)
        self.assertEqual(self.bonus(suspect).status, GRANTED)

    def test_abuse_of_one_side_cancels_a_held_bonus(self):
        from modules.v2.antifraud.services import _block_internal_referrals

        seen_on(self.referrer, "95.24.10.9")
        suspect = self.steam_invite("suspect", ip="95.24.10.9")
        case = MultiAccountCase.objects.create()
        MultiAccountMember.objects.create(case=case, user=suspect)
        MultiAccountMember.objects.create(case=case, user=make_user("twin"))
        _block_internal_referrals(case)  # what mark_abuse does; the inviter is not in the group
        self.assertEqual(self.bonus(suspect).status, DENIED)
        self.assertEqual(self.bonus(self.referrer, "referrer").status, DENIED)

    def test_release_rechecks_abuse(self):
        seen_on(self.referrer, "95.24.10.10")
        suspect = self.steam_invite("suspect", ip="95.24.10.10")
        case = MultiAccountCase.objects.create(status=MultiAccountCase.Status.ABUSE)
        MultiAccountMember.objects.create(case=case, user=suspect)
        referral = Referral.objects.get(referee=suspect)
        services.review_referral(actor=make_user("ref_admin"), referral=referral, decision=Referral.Review.OK)
        self.assertEqual(self.bonus(suspect).status, DENIED)
        self.assertIsNone(get_active_subscription(self.referrer))


class PagesTests(WelcomeBonusTestCase):
    def test_referrals_page_shows_the_gift_block(self):
        self.steam_invite("friend")
        client = Client()
        client.force_login(self.referrer)
        response = client.get(reverse("referrals:dashboard"))
        self.assertContains(response, "Подарок обоим: +7 дней Плюс")
        self.assertContains(response, "+7 дн.")

    def test_admin_changes_the_settings_in_control_center(self):
        admin = make_user("ref_admin")
        UserProfile.objects.filter(user=admin).update(role=UserRole.ADMIN)
        client = Client()
        client.force_login(admin)
        self.assertContains(client.get(reverse("control_center:referrals")), "Бонус «+дни подписки» обеим сторонам")
        client.post(reverse("control_center:referral_settings"), {"welcome_bonus_enabled": "1", "welcome_bonus_days": "10", "referrer_monthly_limit": "5", "welcome_bonus_plan": ""})
        config = ReferralProgramSettings.load()
        self.assertEqual((config.welcome_bonus_days, config.referrer_monthly_limit), (10, 5))
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.SETTINGS_UPDATE, actor=admin).exists())

        client.post(reverse("control_center:referral_settings"), {"welcome_bonus_days": "500", "referrer_monthly_limit": "5"})
        self.assertEqual(ReferralProgramSettings.load().welcome_bonus_days, 10)

    def test_regular_user_cannot_change_settings(self):
        client = Client()
        client.force_login(self.referrer)
        response = client.post(reverse("control_center:referral_settings"), {"welcome_bonus_days": "90", "referrer_monthly_limit": "0"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ReferralProgramSettings.load().welcome_bonus_days, 7)

    def test_bonus_plan_falls_back_to_plus(self):
        self.assertEqual(welcome.bonus_plan(), self.plus)
