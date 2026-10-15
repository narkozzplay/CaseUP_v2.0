from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.models import AdminAuditLog, AuditAction
from modules.v2.notifications.models import Notification
from modules.v2.payments.models import Payment
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole

from . import services
from .models import DailyBonusReward, DailyBonusSettings, DailyBonusState, DailyFreeCase
from .notifications import KIND_REMINDER

User = get_user_model()


def make_user(username: str, *, steam: bool = True, topup: bool = True, role: str = UserRole.USER) -> User:
    user = User.objects.create_user(username=username, password="StrongPass123")
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.steam_id = f"7656119{user.pk:010d}" if steam else ""
    profile.role = role
    profile.save()
    if topup:
        Payment.objects.create(user=user, provider="yookassa", amount=Decimal("100.00"), status=Payment.Status.SUCCEEDED, paid_at=timezone.now())
    return User.objects.get(pk=user.pk)


def balance(user) -> Decimal:
    return UserProfile.objects.get(user=user).balance_coins


class EligibilityTests(TestCase):
    def test_steam_and_real_topup_are_required(self):
        self.assertIn("Steam", services.eligibility(make_user("db_nosteam", steam=False)).reason)
        no_topup = make_user("db_notopup", topup=False)
        Payment.objects.create(user=no_topup, provider="yookassa", amount=Decimal("50"), status=Payment.Status.SUCCEEDED, is_test=True)
        self.assertIn("пополнения", services.eligibility(no_topup).reason)
        self.assertTrue(services.eligibility(make_user("db_ok")).ok)

        DailyBonusSettings.objects.filter(pk=1).update(require_topup=False)
        self.assertTrue(services.eligibility(no_topup).ok)
        DailyBonusSettings.objects.filter(pk=1).update(is_enabled=False)
        with self.assertRaises(services.DailyBonusError):
            services.claim(no_topup)


class ClaimTests(TestCase):
    def setUp(self):
        self.user = make_user("db_player")
        self.now = timezone.now()

    def at(self, days: int):
        return self.now + timedelta(days=days)

    def test_streak_grows_daily_and_resets_after_a_miss(self):
        first = services.claim(self.user, now=self.at(0))
        self.assertEqual((first.day, first.amount, first.streak), (1, Decimal("2.00"), 1))
        with self.assertRaisesMessage(services.DailyBonusError, "уже получен"):
            services.claim(self.user, now=self.at(0))
        second = services.claim(self.user, now=self.at(1))
        self.assertEqual((second.day, second.amount, second.streak), (2, Decimal("3.00"), 2))
        self.assertEqual(balance(self.user), Decimal("5.00"))
        self.assertEqual(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.DAILY_BONUS).count(), 2)

        after_miss = services.claim(self.user, now=self.at(3))
        self.assertEqual((after_miss.day, after_miss.streak), (1, 1))
        state = DailyBonusState.objects.get(user=self.user)
        self.assertEqual((state.best_streak, state.claims_total), (2, 3))

    def test_day_seven_gives_a_free_case_that_the_next_opening_uses(self):
        from modules.v2.case_simulator import services as sim
        from modules.v2.case_simulator.tests import _make_case

        case = _make_case(slug="daily-case", case_price="100", key_price="0")
        DailyBonusReward.objects.filter(day=7).update(kind=DailyBonusReward.Kind.CASE, case=case)
        for offset in range(6):
            services.claim(self.user, now=self.at(offset))
        seventh = services.claim(self.user, now=self.at(6))
        self.assertEqual((seventh.day, seventh.kind, seventh.case_id), (7, DailyBonusReward.Kind.CASE, case.pk))
        self.assertIsNotNone(services.free_case_for(self.user, case))

        before = balance(self.user)
        opening = sim.open_case(user=self.user, case=case, count=1)[0]
        self.assertEqual(opening.price_paid, Decimal("0"))
        self.assertEqual(balance(self.user), before)
        self.assertEqual(DailyFreeCase.objects.get(user=self.user).status, DailyFreeCase.Status.USED)

        eighth = services.claim(self.user, now=self.at(7))
        self.assertEqual((eighth.day, eighth.streak), (1, 8))  # a new cycle, the streak keeps counting

    def test_switched_off_case_falls_back_to_the_amount(self):
        from modules.v2.case_simulator.tests import _make_case

        case = _make_case(slug="retired-case")
        DailyBonusReward.objects.filter(day=1).update(kind=DailyBonusReward.Kind.CASE, case=case, amount=Decimal("9"))
        case.is_active = False
        case.save(update_fields=["is_active"])
        entry = services.claim(self.user)
        self.assertEqual((entry.kind, entry.amount), (DailyBonusReward.Kind.BALANCE, Decimal("9.00")))

    def test_calendar_states(self):
        services.claim(self.user, now=self.at(0))
        today = services.calendar(self.user, now=self.at(0))
        self.assertTrue(today["claimed_today"])
        self.assertEqual([cell["status"] for cell in today["cells"]][:3], ["claimed", "upcoming", "upcoming"])
        tomorrow = services.calendar(self.user, now=self.at(1))
        self.assertEqual([cell["status"] for cell in tomorrow["cells"]][:3], ["claimed", "today", "upcoming"])
        lost = services.calendar(self.user, now=self.at(3))
        self.assertTrue(lost["streak_lost"])
        self.assertEqual(lost["cells"][0]["status"], "today")

    def test_evening_reminder_and_its_resolution(self):
        services.claim(self.user, now=self.at(-1))
        self.assertEqual(services.remind_streaks(now=self.at(0)), 1)
        reminder = Notification.objects.get(recipient=self.user, kind=KIND_REMINDER)
        self.assertEqual(reminder.payload["day"], 2)
        services.claim(self.user, now=self.at(0))
        reminder.refresh_from_db()
        self.assertTrue(reminder.is_read)
        self.assertEqual(services.remind_streaks(now=self.at(0)), 0)


class PageTests(TestCase):
    def test_user_claims_from_the_page(self):
        user = make_user("db_page")
        client = Client()
        client.force_login(user)
        page = client.get(reverse("daily_bonus:page"))
        self.assertContains(page, "Забрать бонус дня 1")
        client.post(reverse("daily_bonus:claim"))
        self.assertContains(client.get(reverse("daily_bonus:page")), "Сегодняшний бонус получен")
        self.assertEqual(balance(user), Decimal("2.00"))

    def test_locked_page_explains_what_to_do(self):
        client = Client()
        client.force_login(make_user("db_locked", topup=False))
        page = client.get(reverse("daily_bonus:page"))
        self.assertContains(page, "после первого пополнения")
        self.assertContains(page, reverse("payments:topup"))
        self.assertEqual(client.post(reverse("daily_bonus:claim")).status_code, 302)
        self.assertFalse(DailyBonusState.objects.filter(user__username="db_locked", claims_total__gt=0).exists())

    def test_admin_edits_rewards_with_an_audit_entry(self):
        admin = make_user("db_admin", role=UserRole.ADMIN)
        client = Client()
        client.force_login(admin)
        self.assertContains(client.get(reverse("control_center:daily_bonus")), "Награды и правила")
        form = {f"kind_{day}": "balance" for day in range(1, 8)}
        form.update({f"amount_{day}": str(day) for day in range(1, 8)})
        form.update({"is_enabled": "1", "require_steam": "1", "free_case_valid_days": "3"})
        client.post(reverse("control_center:daily_bonus"), form)
        self.assertEqual(DailyBonusReward.objects.get(day=7).amount, Decimal("7.00"))
        settings = DailyBonusSettings.load()
        self.assertEqual((settings.require_topup, settings.free_case_valid_days), (False, 3))
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.DAILY_BONUS_UPDATE, actor=admin).exists())

        form["kind_2"] = "case"  # a case day without a case is refused
        client.post(reverse("control_center:daily_bonus"), form)
        self.assertEqual(DailyBonusReward.objects.get(day=2).kind, DailyBonusReward.Kind.BALANCE)

        user_client = Client()
        user_client.force_login(make_user("db_regular"))
        self.assertEqual(user_client.get(reverse("control_center:daily_bonus")).status_code, 403)

    def test_business_panel_counts_daily_bonus_as_a_cost(self):
        from modules.v2.business import stats

        services.claim(make_user("db_cost"))
        period = stats.resolve_period("today")
        money = stats.money_summary(period.start, period.end)
        daily = next(row for row in money["bonuses"] if row["reason"] == LedgerReason.DAILY_BONUS)
        self.assertEqual(daily["total"], Decimal("2.00"))
