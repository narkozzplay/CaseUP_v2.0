from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.models import AdminAuditLog, AuditAction
from modules.v2.payments.views import _safe_return_to
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile, UserRole

from . import services, sponsors
from .forms import DonationGoalForm
from .models import Donation, DonationGoal, Sponsorship
from .notifications import KIND_SPONSOR_CANDIDATES, KIND_SPONSOR_GRANTED

User = get_user_model()


def make_user(username: str, *, balance: str = "0", role: str = UserRole.USER) -> User:
    user = User.objects.create_user(username=username, password="StrongPass123")
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.balance_coins = Decimal(balance)
    profile.role = role
    profile.save()
    return User.objects.get(pk=user.pk)


def balance(user) -> Decimal:
    return UserProfile.objects.get(user=user).balance_coins


def enable(**fields) -> DonationGoal:
    goal = DonationGoal.load()
    goal.is_enabled = True
    for name, value in fields.items():
        setattr(goal, name, value)
    goal.save()
    return goal


class DonateServiceTests(TestCase):
    def setUp(self):
        enable(goal_amount=Decimal("1000.00"))
        self.user = make_user("don_player", balance="500")

    def test_donation_moves_balance_once_per_token(self):
        token = uuid.uuid4()
        first = services.donate(user=self.user, amount=Decimal("150"), token=token, message="  на сервер  ")
        again = services.donate(user=self.user, amount=Decimal("150"), token=token)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(balance(self.user), Decimal("350.00"))
        self.assertEqual(first.message, "на сервер")
        entry = LedgerEntry.objects.get(pk=first.ledger_entry_id)
        self.assertEqual((entry.reason, entry.delta), (LedgerReason.DONATION, Decimal("-150.00")))
        self.assertEqual(Donation.objects.count(), 1)

    def test_refusals(self):
        with self.assertRaisesMessage(services.DonationError, "недостаточно"):
            services.donate(user=self.user, amount=Decimal("600"), token=uuid.uuid4())
        with self.assertRaisesMessage(services.DonationError, "от 10"):
            services.donate(user=self.user, amount=Decimal("5"), token=uuid.uuid4())
        DonationGoal.objects.filter(pk=1).update(is_enabled=False)
        with self.assertRaisesMessage(services.DonationError, "не ведётся"):
            services.donate(user=self.user, amount=Decimal("50"), token=uuid.uuid4())
        self.assertEqual(balance(self.user), Decimal("500.00"))
        self.assertFalse(Donation.objects.exists())

    def test_progress_counts_offset_and_restarts_with_a_new_campaign(self):
        goal = enable(collected_offset=Decimal("100.00"))
        services.donate(user=self.user, amount=Decimal("150"), token=uuid.uuid4(), is_anonymous=True)
        state = services.build_state(goal)
        self.assertEqual((state.collected, state.percent, state.donors, state.reached), (Decimal("250.00"), 25, 1, False))
        self.assertEqual(state.recent[0].name, "Аноним")

        Donation.objects.update(created_at=timezone.now() - timedelta(minutes=1))
        goal = services.start_new_campaign(goal)
        state = services.build_state(goal)
        self.assertEqual((state.collected, state.donors), (Decimal("0.00"), 0))
        self.assertEqual(Donation.objects.count(), 1)

        goal.collected_offset = Decimal("1500.00")
        self.assertEqual((services.build_state(goal).percent, services.build_state(goal).bar_percent), (150, 100))


class DonatePageTests(TestCase):
    def test_page_form_and_shortfall_redirect_to_topup(self):
        enable(title="На новые серверы")
        anonymous = Client().get(reverse("donations:page"))
        self.assertContains(anonymous, "На новые серверы")
        self.assertContains(anonymous, "Войдите")

        user = make_user("don_page", balance="20")
        client = Client()
        client.force_login(user)
        self.assertContains(client.get(reverse("donations:page")), 'name="token"')

        response = client.post(reverse("donations:give"), {"amount": "100", "token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("payments:topup"), response["Location"])
        self.assertIn("next=%2Fdonate%2F", response["Location"])
        self.assertEqual(balance(user), Decimal("20.00"))

        response = client.post(reverse("donations:give"), {"preset": "15", "token": str(uuid.uuid4()), "message": "спасибо"})
        self.assertRedirects(response, reverse("donations:page"), fetch_redirect_response=False)
        self.assertEqual(balance(user), Decimal("5.00"))
        self.assertTrue(Donation.objects.filter(user=user, amount=Decimal("15.00"), message="спасибо").exists())

    def test_disabled_page(self):
        self.assertContains(Client().get(reverse("donations:page")), "Сейчас сбор не ведётся")

    def test_topup_may_return_to_the_donation_page(self):
        self.assertEqual(_safe_return_to("/donate/?amount=100"), "/donate/?amount=100")
        self.assertEqual(_safe_return_to("/en/donate/?amount=100"), "/en/donate/?amount=100")
        self.assertEqual(_safe_return_to("//evil.example/donate/"), "")
        self.assertEqual(_safe_return_to("/en//evil.example/donate/"), "")
        self.assertEqual(_safe_return_to("/en/profile/"), "")


class WidgetPlacementTests(TestCase):
    def test_home_shows_the_widget_only_when_enabled_there(self):
        self.assertNotContains(Client().get(reverse("frontend:home")), "data-donation-widget")
        enable(title="Помогите CaseUP", accent_color="#123abc")
        home = Client().get(reverse("frontend:home"))
        self.assertContains(home, "Помогите CaseUP")
        self.assertContains(home, "--donation-accent: #123abc")
        DonationGoal.objects.filter(pk=1).update(show_on_home=False)
        self.assertNotContains(Client().get(reverse("frontend:home")), "data-donation-widget")

    def test_menu_item_follows_the_switch(self):
        client = Client()
        client.force_login(make_user("don_menu"))
        donate_url = reverse("donations:page")
        self.assertNotContains(client.get(reverse("daily_bonus:page")), f'href="{donate_url}"')
        enable(show_in_sidebar=True)
        page = client.get(reverse("daily_bonus:page"))
        self.assertContains(page, "Поддержать проект")
        self.assertContains(page, "donation--at-sidebar")


class ControlCenterTests(TestCase):
    def form_data(self, **changes):
        data = {key: value for key, value in model_to_dict(DonationGoal.load(), fields=DonationGoalForm.Meta.fields).items() if value is not None and value is not False}
        data.update(changes)
        return {key: ("1" if value is True else value) for key, value in data.items() if value is not False}

    def test_admin_edits_the_goal_with_an_audit_entry(self):
        admin = make_user("don_admin", role=UserRole.ADMIN)
        client = Client()
        client.force_login(admin)
        self.assertContains(client.get(reverse("control_center:donations")), "Предпросмотр")

        client.post(reverse("control_center:donations"), self.form_data(is_enabled=True, title="Сбор на VDS", goal_amount="5000", variant="banner", accent_color="#00ff88"))
        goal = DonationGoal.load()
        self.assertEqual((goal.is_enabled, goal.title, goal.goal_amount, goal.variant, goal.accent_color), (True, "Сбор на VDS", Decimal("5000.00"), "banner", "#00ff88"))
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.DONATION_UPDATE, actor=admin).exists())

        client.post(reverse("control_center:donations"), self.form_data(accent_color="red", preset_amounts="10, abc"))
        self.assertEqual(DonationGoal.load().accent_color, "#00ff88")

        client.post(reverse("control_center:donations"), {"action": "new_campaign"})
        self.assertGreater(DonationGoal.load().started_at, goal.started_at)

        user_client = Client()
        user_client.force_login(make_user("don_regular"))
        self.assertEqual(user_client.get(reverse("control_center:donations")).status_code, 403)

    def test_presets_must_fit_the_limits(self):
        form = DonationGoalForm(self.form_data(preset_amounts="5, 100", min_amount="10"), instance=DonationGoal.load())
        self.assertFalse(form.is_valid())
        self.assertIn("preset_amounts", form.errors)


class SponsorTests(TestCase):
    def setUp(self):
        from modules.v2.notifications.models import Notification

        self.Notification = Notification
        enable()
        self.admin = make_user("sp_admin", role=UserRole.ADMIN)
        self.today = timezone.localdate()
        self.last_month = sponsors.add_months(sponsors.month_start(self.today), -1)

    def donation(self, user, amount, *, period=None):
        when = timezone.make_aware(timezone.datetime.combine((period or self.last_month).replace(day=10), timezone.datetime.min.time()))
        return Donation.objects.create(user=user, amount=Decimal(amount), created_at=when)

    def test_monthly_top_gives_candidates_once_and_starts_over_each_month(self):
        rich, middle, small, fourth = (make_user(f"sp_{name}") for name in ("rich", "middle", "small", "fourth"))
        self.donation(rich, "4000")
        self.donation(rich, "3500")
        self.donation(middle, "6000")
        self.donation(small, "5000")  # «больше 5000» — exactly 5000 does not count
        self.donation(fourth, "100")

        created = sponsors.find_monthly_candidates(today=self.today)
        self.assertEqual([(item.user.username, item.rank, item.amount) for item in created], [("sp_rich", 1, Decimal("7500.00")), ("sp_middle", 2, Decimal("6000.00"))])
        notice = self.Notification.objects.get(recipient=self.admin, kind=KIND_SPONSOR_CANDIDATES)
        self.assertIn("sp_rich", notice.payload["names"])
        self.assertEqual(sponsors.find_monthly_candidates(today=self.today), [])

        self.assertEqual(sponsors.monthly_top(sponsors.month_start(self.today)), [])
        history = sponsors.history(today=self.today)
        rich_row = next(row for row in history["rows"] if row["user"] == rich)
        self.assertEqual((rich_row["months"][1], rich_row["lifetime"]), (Decimal("7500.00"), Decimal("7500.00")))

    def test_lifetime_threshold_makes_a_candidate_once(self):
        donor = make_user("sp_loyal", balance="20000")
        self.donation(donor, "9980")
        with self.captureOnCommitCallbacks(execute=True):
            services.donate(user=donor, amount=Decimal("20"), token=uuid.uuid4())  # exactly 10000 is not «больше»
        self.assertFalse(Sponsorship.objects.filter(user=donor).exists())
        with self.captureOnCommitCallbacks(execute=True):
            services.donate(user=donor, amount=Decimal("10"), token=uuid.uuid4())
            services.donate(user=donor, amount=Decimal("15"), token=uuid.uuid4())
        lifetime = Sponsorship.objects.get(user=donor, kind=Sponsorship.Kind.LIFETIME)
        self.assertEqual((lifetime.status, lifetime.amount), (Sponsorship.Status.CANDIDATE, Decimal("10010.00")))
        self.assertEqual(self.Notification.objects.filter(recipient=self.admin, kind=KIND_SPONSOR_CANDIDATES).count(), 1)

    def test_admin_grants_declines_and_revokes_in_control_center(self):
        donor, other = make_user("sp_granted"), make_user("sp_declined")
        self.donation(donor, "8000")
        self.donation(other, "5500")
        monthly, declined = sponsors.find_monthly_candidates(today=self.today)
        client = Client()
        client.force_login(self.admin)
        page = client.get(reverse("control_center:donations"))
        self.assertContains(page, "Выдать роль")

        url = reverse("control_center:donations")
        client.post(url, {"action": "sponsor", "sponsorship_id": monthly.pk, "decision": "grant"})
        client.post(url, {"action": "sponsor", "sponsorship_id": declined.pk, "decision": "decline"})
        monthly.refresh_from_db()
        self.assertEqual(monthly.status, Sponsorship.Status.GRANTED)
        self.assertEqual(timezone.localtime(monthly.expires_at).date(), sponsors.add_months(self.last_month, 2))
        self.assertTrue(sponsors.is_sponsor(donor))
        self.assertFalse(sponsors.is_sponsor(other))
        granted = self.Notification.objects.get(recipient=donor, kind=KIND_SPONSOR_GRANTED)
        self.assertTrue(granted.payload["until"])
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.DONATION_UPDATE, target_user=donor).exists())

        client.post(url, {"action": "sponsor", "sponsorship_id": monthly.pk, "decision": "revoke"})
        self.assertFalse(sponsors.is_sponsor(donor))
        self.assertEqual(client.post(url, {"action": "sponsor", "sponsorship_id": "junk", "decision": "grant"}).status_code, 404)

    def test_renderers(self):
        from .notifications import render_candidates, render_granted

        note = self.Notification(kind=KIND_SPONSOR_CANDIDATES, payload={"reason": "monthly", "month": "октябрь 2026", "threshold": "5000", "names": ["a", "b"]})
        self.assertIn("октябрь 2026", render_candidates(note, "ru").title)
        self.assertIn("навсегда", render_granted(self.Notification(kind=KIND_SPONSOR_GRANTED, payload={"until": ""}), "ru").body)
