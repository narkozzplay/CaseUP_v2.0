from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.notifications.models import Notification
from modules.v2.profiles.models import LedgerEntry, LedgerReason, UserProfile
from modules.v2.steam_catalog.models import SteamItem
from modules.v2.subscriptions.models import SubscriptionPlan
from modules.v2.subscriptions.services import get_active_subscription

from . import services
from .items import check_major_items, page_items
from .models import BracketPick, Major, Match, RewardGrant, RewardRule, Stage, StageTeam, SwissPick, Team

User = get_user_model()
R = Match.Round


def make_user(username: str, *, steam: bool = True) -> User:
    user = User.objects.create_user(username=username, password="StrongPass123")
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.steam_id = f"7656119{user.pk:010d}" if steam else ""
    profile.steam_personaname = f"{username} persona"
    profile.save()
    return User.objects.get(pk=user.pk)


def balance(user) -> Decimal:
    return UserProfile.objects.get(user=user).balance_coins


class MajorTestCase(TestCase):
    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.major = Major.objects.create(
            name="Test Major 2026", slug="test-major", location="Сингапур", starts_at=now + timedelta(days=2),
            ends_on=(now + timedelta(days=20)).date(), is_published=True, items_keyword="Test 2026",
        )
        self.swiss = Stage.objects.create(major=self.major, order=1, name="Стадия 1", kind=Stage.Kind.SWISS, starts_at=now + timedelta(days=2))
        self.playoffs = Stage.objects.create(major=self.major, order=2, name="Плей-офф", kind=Stage.Kind.PLAYOFFS, starts_at=now + timedelta(days=15))
        self.teams = [Team.objects.create(name=f"Team {index:02d}", slug=f"team-{index:02d}", tag=f"T{index}") for index in range(16)]
        for seed, team in enumerate(self.teams, start=1):
            StageTeam.objects.create(stage=self.swiss, team=team, seed=seed)
        for slot in range(1, 5):
            Match.objects.create(stage=self.playoffs, round=R.QUARTERFINAL, slot=slot, team_a=self.teams[2 * slot - 2], team_b=self.teams[2 * slot - 1])
        Match.objects.create(stage=self.playoffs, round=R.SEMIFINAL, slot=1)
        Match.objects.create(stage=self.playoffs, round=R.SEMIFINAL, slot=2)
        Match.objects.create(stage=self.playoffs, round=R.FINAL, slot=1)
        self.user = make_user("fan")

    def t(self, index: int) -> int:
        return self.teams[index].pk

    def perfect_swiss_picks(self) -> dict[int, str]:
        picks = {self.t(0): "3-0", self.t(1): "3-0", self.t(14): "0-3", self.t(15): "0-3"}
        picks.update({self.t(index): "advance" for index in range(2, 8)})
        return picks

    def record_swiss_results(self):
        records = {0: "3-0", 1: "3-0", 14: "0-3", 15: "0-3"}
        for index in range(2, 8):
            records[index] = "3-1"
        for index in range(8, 14):
            records[index] = "2-3"
        for index, record in records.items():
            StageTeam.objects.filter(stage=self.swiss, team=self.teams[index]).update(record=record)

    def bracket_picks(self) -> dict[tuple[int, int], int]:
        # Team 0, 2, 4, 6 win the quarterfinals, 0 and 4 the semifinals, 0 the final.
        return {
            (1, 1): self.t(0), (1, 2): self.t(2), (1, 3): self.t(4), (1, 4): self.t(6),
            (2, 1): self.t(0), (2, 2): self.t(4), (3, 1): self.t(0),
        }

    def play_bracket(self, winners: dict[tuple[int, int], int]):
        for (round_no, slot), team_id in winners.items():
            Match.objects.filter(stage=self.playoffs, round=round_no, slot=slot).update(winner_id=team_id)
            services.advance_winners(self.playoffs)


class PublicPageTests(MajorTestCase):
    def test_guest_sees_the_major_with_banner_slot_countdown_and_seo(self):
        response = Client().get(self.major.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="site-header"')
        self.assertContains(response, "data-mj-countdown")
        self.assertContains(response, "До старта мейджора")
        self.assertContains(response, "mj-banner--empty")
        self.assertNotContains(response, "Место для баннера")  # the hint is for staff only
        self.assertContains(response, "<title>Test Major 2026: Pick&#x27;em, команды и расписание")
        self.assertContains(response, '"@type": "SportsEvent"')
        self.assertContains(response, "Team 00")

    def test_staff_sees_where_the_banner_goes_and_uploaded_banner_replaces_it(self):
        staff = make_user("boss")
        User.objects.filter(pk=staff.pk).update(is_staff=True)
        client = Client()
        client.force_login(staff)
        self.assertContains(client.get(self.major.get_absolute_url()), "Место для баннера 1920×640")
        self.major.banner = "majors/banners/test.webp"
        self.major.save()
        page = client.get(self.major.get_absolute_url())
        self.assertContains(page, "/media/majors/banners/test.webp")
        self.assertContains(page, "data-dashboard-shell")

    def test_unpublished_major_is_hidden_and_index_redirects_to_the_current_one(self):
        Major.objects.exclude(pk=self.major.pk).update(is_published=False)
        self.assertRedirects(Client().get(reverse("majors:index")), self.major.get_absolute_url(), fetch_redirect_response=False)
        self.major.is_published = False
        self.major.save()
        self.assertEqual(Client().get(self.major.get_absolute_url()).status_code, 404)

    def test_countdown_moves_to_the_next_stage_lock_after_the_start(self):
        self.major.starts_at = timezone.now() - timedelta(hours=1)
        self.major.save()
        Stage.objects.filter(pk=self.swiss.pk).update(starts_at=timezone.now() - timedelta(hours=1))
        self.assertContains(Client().get(self.major.get_absolute_url()), "До закрытия прогнозов: Плей-офф")

    def test_sitemap_lists_the_major(self):
        response = Client().get("/sitemap-majors.xml")
        self.assertContains(response, f"https://testserver{self.major.get_absolute_url()}")
        self.assertContains(response, "https://testserver/major/test-major/leaderboard/")


class PickemSavingTests(MajorTestCase):
    def test_pickem_needs_sign_in_and_steam(self):
        response = Client().get(reverse("majors:pickem", args=[self.major.slug]))
        self.assertEqual(response.status_code, 302)
        no_steam = make_user("nosteam", steam=False)
        with self.assertRaisesMessage(services.PickemError, "привязанным Steam"):
            services.save_swiss_picks(no_steam, self.swiss, {self.t(0): "3-0"})

    def test_swiss_picks_respect_the_limits_and_can_be_changed(self):
        self.assertEqual(services.save_swiss_picks(self.user, self.swiss, self.perfect_swiss_picks()), 10)
        with self.assertRaisesMessage(services.PickemError, "не больше 2"):
            services.save_swiss_picks(self.user, self.swiss, {self.t(0): "3-0", self.t(1): "3-0", self.t(2): "3-0"})
        services.save_swiss_picks(self.user, self.swiss, {self.t(3): "0-3"})
        self.assertEqual(list(SwissPick.objects.filter(user=self.user).values_list("team_id", "pick")), [(self.t(3), "0-3")])

    def test_swiss_picks_close_when_the_stage_starts(self):
        Stage.objects.filter(pk=self.swiss.pk).update(starts_at=timezone.now() - timedelta(minutes=1))
        self.swiss.refresh_from_db()
        with self.assertRaisesMessage(services.PickemError, "закрыты"):
            services.save_swiss_picks(self.user, self.swiss, {self.t(0): "3-0"})

    def test_swiss_form_posts_and_shows_the_picks(self):
        client = Client()
        client.force_login(self.user)
        url = reverse("majors:pickem", args=[self.major.slug])
        data = {"stage": "1", **{f"team_{team_id}": pick for team_id, pick in self.perfect_swiss_picks().items()}}
        response = client.post(url, data)
        self.assertRedirects(response, f"{url}?stage=1", fetch_redirect_response=False)
        self.assertEqual(SwissPick.objects.filter(user=self.user).count(), 10)
        page = client.get(url, {"stage": "1"})
        self.assertContains(page, "data-mj-swiss")
        self.assertContains(page, f'name="team_{self.t(0)}" value="3-0" checked')

    def test_bracket_later_rounds_only_take_teams_picked_before(self):
        self.assertEqual(services.save_bracket_picks(self.user, self.playoffs, self.bracket_picks()), 7)
        wrong = {**self.bracket_picks(), (2, 1): self.t(1)}  # team 1 was not picked to win its quarterfinal
        with self.assertRaisesMessage(services.PickemError, "Сетка заполнена неверно"):
            services.save_bracket_picks(self.user, self.playoffs, wrong)
        outsider = {**self.bracket_picks(), (1, 1): self.t(9)}
        with self.assertRaises(services.PickemError):
            services.save_bracket_picks(self.user, self.playoffs, outsider)

    def test_third_place_takes_a_semifinal_loser(self):
        Match.objects.create(stage=self.playoffs, round=R.THIRD_PLACE, slot=1)
        picks = {**self.bracket_picks(), (4, 1): self.t(2)}
        self.assertEqual(services.save_bracket_picks(self.user, self.playoffs, picks), 8)
        with self.assertRaises(services.PickemError):
            services.save_bracket_picks(self.user, self.playoffs, {**self.bracket_picks(), (4, 1): self.t(0)})

    def test_bracket_form_renders_options_from_own_picks(self):
        services.save_bracket_picks(self.user, self.playoffs, self.bracket_picks())
        client = Client()
        client.force_login(self.user)
        page = client.get(reverse("majors:pickem", args=[self.major.slug]), {"stage": "2"})
        self.assertContains(page, "data-mj-bracket")
        self.assertContains(page, 'data-winner-of="r1s1"')
        self.assertContains(page, f'name="r3s1" value="{self.t(0)}" checked')


class ScoringAndRewardTests(MajorTestCase):
    def setUp(self):
        super().setUp()
        self.plus = SubscriptionPlan.objects.filter(family="site").order_by("level").last()
        self.skin = SteamItem.objects.create(market_hash_name="AWP | Dragon Lore (Factory New)", name="AWP | Dragon Lore", item_type="skin")
        self.stage_rule = RewardRule.objects.create(major=self.major, title="Стадия", condition=RewardRule.Condition.STAGE_PASSED, amount=Decimal("25"))
        self.champion = RewardRule.objects.create(major=self.major, title="Чемпион", condition=RewardRule.Condition.CHAMPION, amount=Decimal("150"))
        self.finalists = RewardRule.objects.create(major=self.major, title="Финалисты", condition=RewardRule.Condition.FINALISTS, min_correct=2, amount=Decimal("100"))
        self.all_stages = RewardRule.objects.create(
            major=self.major, title="Все стадии", condition=RewardRule.Condition.ALL_STAGES,
            kind=RewardRule.Kind.SUBSCRIPTION, plan=self.plus, days=30, requires_approval=True,
        )
        self.perfect = RewardRule.objects.create(major=self.major, title="Идеально", condition=RewardRule.Condition.PERFECT, kind=RewardRule.Kind.ITEM, item=self.skin)
        services.save_swiss_picks(self.user, self.swiss, self.perfect_swiss_picks())
        services.save_bracket_picks(self.user, self.playoffs, self.bracket_picks())
        self.rival = make_user("rival")
        services.save_swiss_picks(self.rival, self.swiss, {self.t(8): "3-0", self.t(9): "3-0", self.t(0): "0-3"})

    def settle_everything(self):
        self.record_swiss_results()
        self.play_bracket({(1, 1): self.t(0), (1, 2): self.t(2), (1, 3): self.t(4), (1, 4): self.t(6)})
        self.play_bracket({(2, 1): self.t(0), (2, 2): self.t(4)})
        self.play_bracket({(3, 1): self.t(0)})
        Stage.objects.filter(major=self.major).update(is_settled=True)

    def test_winners_move_on_to_the_next_round(self):
        self.play_bracket({(1, 1): self.t(1), (1, 2): self.t(2)})
        semi = Match.objects.get(stage=self.playoffs, round=R.SEMIFINAL, slot=1)
        self.assertEqual((semi.team_a_id, semi.team_b_id), (self.t(1), self.t(2)))

    def test_scores_and_leaderboard(self):
        self.record_swiss_results()
        summary = services.user_summary(self.major, self.user)
        self.assertEqual(summary.for_stage(self.swiss.pk).correct, 10)
        self.assertIsNone(summary.for_stage(self.swiss.pk).passed)  # not settled yet
        board = services.leaderboard(self.major)
        self.assertEqual([row["user"].pk for row in board], [self.user.pk, self.rival.pk])
        self.assertEqual(board[0]["name"], "fan persona")
        page = Client().get(reverse("majors:leaderboard", args=[self.major.slug]))
        self.assertContains(page, "fan persona")
        self.assertContains(page, "10/10")

    def test_settle_grants_each_reward_once(self):
        self.settle_everything()
        report = services.settle_major(self.major)
        # Both stages passed (25 each), finalists 100, champion 150; the subscription and the skin wait.
        self.assertEqual((report.granted, report.pending), (4, 2))
        self.assertEqual(balance(self.user), Decimal("300.00"))
        self.assertEqual(LedgerEntry.objects.filter(user=self.user, reason=LedgerReason.MAJOR_PICKEM).count(), 4)
        self.assertTrue(Notification.objects.filter(recipient=self.user, kind="major_pickem_reward").exists())
        # The rival passed nothing.
        self.assertFalse(RewardGrant.objects.filter(user=self.rival).exists())
        again = services.settle_major(self.major)
        self.assertEqual((again.granted, again.pending), (0, 0))
        self.assertEqual(balance(self.user), Decimal("300.00"))

    def test_big_rewards_wait_for_approval(self):
        self.settle_everything()
        services.settle_major(self.major)
        subscription_grant = RewardGrant.objects.get(rule=self.all_stages)
        self.assertEqual(subscription_grant.status, RewardGrant.Status.PENDING)
        self.assertIsNone(get_active_subscription(self.user))
        self.assertTrue(services.approve_grant(subscription_grant))
        self.assertEqual(get_active_subscription(self.user).plan, self.plus)
        self.assertFalse(services.approve_grant(subscription_grant))

        skin_grant = RewardGrant.objects.get(rule=self.perfect)
        self.assertTrue(services.approve_grant(skin_grant))
        skin_grant.refresh_from_db()
        self.assertEqual(skin_grant.status, RewardGrant.Status.NEEDS_TRADE_URL)
        self.assertTrue(services.mark_item_sent(skin_grant))
        skin_grant.refresh_from_db()
        self.assertEqual(skin_grant.status, RewardGrant.Status.GRANTED)

    def test_skin_goes_straight_to_sending_when_the_trade_link_is_known(self):
        from modules.v2.market.models import MarketSeller

        MarketSeller.objects.create(user=self.user, trade_url="https://steamcommunity.com/tradeoffer/new/?partner=1&token=abc")
        self.settle_everything()
        services.settle_major(self.major)
        skin_grant = RewardGrant.objects.get(rule=self.perfect)
        services.approve_grant(skin_grant)
        skin_grant.refresh_from_db()
        self.assertEqual(skin_grant.status, RewardGrant.Status.TO_SEND)

    def test_expensive_prizes_need_tax_data(self):
        self.assertFalse(self.perfect.needs_tax_data)  # no price yet
        SteamItem.objects.filter(pk=self.skin.pk).update(price_rub=Decimal("250000"))
        self.perfect.refresh_from_db()
        self.assertTrue(self.perfect.needs_tax_data)
        self.assertFalse(self.champion.needs_tax_data)

    def test_wrong_champion_gets_no_champion_reward(self):
        self.record_swiss_results()
        self.play_bracket({(1, 1): self.t(0), (1, 2): self.t(2), (1, 3): self.t(4), (1, 4): self.t(6)})
        self.play_bracket({(2, 1): self.t(0), (2, 2): self.t(4)})
        self.play_bracket({(3, 1): self.t(4)})
        Stage.objects.filter(major=self.major).update(is_settled=True)
        services.settle_major(self.major)
        titles = set(RewardGrant.objects.filter(user=self.user).values_list("rule__title", flat=True))
        self.assertIn("Финалисты", titles)
        self.assertNotIn("Чемпион", titles)
        self.assertNotIn("Идеально", titles)

    def test_stage_reward_is_paid_per_stage_and_only_when_settled(self):
        self.record_swiss_results()
        self.assertEqual(services.settle_major(self.major).granted, 0)
        Stage.objects.filter(pk=self.swiss.pk).update(is_settled=True)
        services.settle_major(self.major)
        grant = RewardGrant.objects.get(rule=self.stage_rule, user=self.user)
        self.assertEqual(grant.scope, f"stage:{self.swiss.pk}")

    def test_user_without_steam_is_skipped(self):
        UserProfile.objects.filter(user=self.user).update(steam_id="")
        self.settle_everything()
        report = services.settle_major(self.major)
        self.assertGreater(report.skipped, 0)
        self.assertFalse(RewardGrant.objects.filter(user=self.user).exists())

    def test_pickem_page_lists_own_rewards(self):
        self.settle_everything()
        services.settle_major(self.major)
        client = Client()
        client.force_login(self.user)
        page = client.get(reverse("majors:pickem", args=[self.major.slug]))
        self.assertContains(page, "Ваши награды")
        self.assertContains(page, "AWP | Dragon Lore (Factory New)")
        self.assertContains(page, "трейд-ссылку")


class MajorItemsTests(MajorTestCase):
    def test_items_by_keyword_and_by_hand(self):
        capsule = SteamItem.objects.create(market_hash_name="Test 2026 Legends Sticker Capsule", name="Capsule", item_type="case", price_rub=Decimal("90"))
        sticker = SteamItem.objects.create(market_hash_name="Sticker | Team 00 | Test 2026", name="Sticker", item_type="sticker")
        manual = SteamItem.objects.create(market_hash_name="Souvenir Charm | Test", name="Charm", item_type="charm")
        SteamItem.objects.create(market_hash_name="Sticker | Other 2025", name="Other", item_type="sticker")
        self.major.extra_items.add(manual)
        items = page_items(self.major)
        self.assertEqual((items["packages"], items["stickers"], items["other"]), ([capsule], [sticker], [manual]))
        page = Client().get(self.major.get_absolute_url())
        self.assertContains(page, "Test 2026 Legends Sticker Capsule")
        self.assertContains(page, "/steam-catalog/?q=Test 2026")

    @patch("modules.v2.system.alerts.send_admin_alert")
    def test_daily_check_alerts_about_new_and_missing_items(self, alert):
        check_major_items(refresh=False)
        alert.assert_not_called()  # before the start nothing is missing yet

        started = timezone.now() + timedelta(days=3)
        check_major_items(now=started, refresh=False)
        self.assertIn("не появились", alert.call_args.args[1])

        alert.reset_mock()
        SteamItem.objects.create(market_hash_name="Test 2026 Legends Sticker Capsule", name="Capsule", item_type="case")
        check_major_items(now=started, refresh=False)
        self.assertIn("новые предметы", alert.call_args.args[1])
        self.major.refresh_from_db()
        self.assertEqual(self.major.items_known_count, 1)

        alert.reset_mock()
        check_major_items(now=started, refresh=False)
        alert.assert_not_called()

    @patch("modules.v2.system.alerts.send_admin_alert")
    def test_majors_far_away_are_not_watched(self, alert):
        self.assertEqual(check_major_items(now=timezone.now() - timedelta(days=60), refresh=False), {})
        alert.assert_not_called()


class SeedTests(TestCase):
    def test_singapore_major_and_team_logos_are_seeded(self):
        major = Major.objects.get(slug="singapore-2026")
        self.assertEqual(major.ends_on, date(2026, 12, 13))
        self.assertEqual(list(major.stages.values_list("kind", flat=True)), ["swiss", "swiss", "swiss", "playoffs"])
        self.assertEqual(major.stages.get(kind="playoffs").matches.count(), 8)
        self.assertGreaterEqual(Team.objects.count(), 50)
        from django.contrib.staticfiles import finders

        for team in Team.objects.all():
            self.assertTrue(finders.find(team.logo_static), team.slug)
