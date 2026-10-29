from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from modules.v2.majors.models import BracketPick, Major, Match, RewardGrant, RewardRule, Stage, StageTeam, SwissPick, Team
from modules.v2.profiles.models import UserProfile, UserRole

from .models import AdminAuditLog, AuditAction
from .tests import make_user

R = Match.Round


def _dt(value) -> str:
    return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M")


def _management(prefix: str, total: int, initial: int) -> dict:
    return {f"{prefix}-TOTAL_FORMS": str(total), f"{prefix}-INITIAL_FORMS": str(initial), f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"}


class MajorsControlCenterTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss", UserRole.ADMIN)
        self.client.force_login(self.admin)
        now = timezone.now()
        self.major = Major.objects.create(name="Test Major", slug="test-major", starts_at=now + timedelta(days=2), ends_on=(now + timedelta(days=20)).date(), is_published=True)
        self.teams = [Team.objects.create(name=f"Team {index}", slug=f"team-{index}") for index in range(8)]

    def test_pages_need_admin(self):
        self.client.force_login(make_user("viewer"))
        for url in (reverse("control_center:majors"), reverse("control_center:major_edit", args=[self.major.pk]), reverse("control_center:major_teams")):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_pages_render(self):
        stage = Stage.objects.create(major=self.major, order=1, name="Плей-офф", kind=Stage.Kind.PLAYOFFS, starts_at=timezone.now())
        rule = RewardRule.objects.create(major=self.major, title="Чемпион", condition=RewardRule.Condition.CHAMPION, amount=Decimal("100"))
        for url in (
            reverse("control_center:majors"),
            reverse("control_center:major_create"),
            reverse("control_center:major_edit", args=[self.major.pk]),
            reverse("control_center:stage_create", args=[self.major.pk]),
            reverse("control_center:stage_edit", args=[stage.pk]),
            reverse("control_center:reward_create", args=[self.major.pk]),
            reverse("control_center:reward_edit", args=[rule.pk]),
            reverse("control_center:major_teams"),
            reverse("control_center:team_create"),
            reverse("control_center:team_edit", args=[self.teams[0].pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, "Мейджоры")

    def test_create_major_makes_slug_and_logs(self):
        start = timezone.now() + timedelta(days=30)
        response = self.client.post(reverse("control_center:major_create"), {
            "action": "save", "name": "Cologne Major 2027", "slug": "", "starts_at": _dt(start), "ends_on": (start + timedelta(days=14)).date().isoformat(), "require_steam": "on",
        })
        major = Major.objects.get(name="Cologne Major 2027")
        self.assertRedirects(response, reverse("control_center:major_edit", args=[major.pk]), fetch_redirect_response=False)
        self.assertEqual(major.slug, "cologne-major-2027")
        self.assertFalse(major.is_published)
        self.assertTrue(AdminAuditLog.objects.filter(action=AuditAction.MAJOR_UPDATE, summary__contains="Cologne").exists())

    def test_publish_toggle(self):
        self.client.post(reverse("control_center:major_edit", args=[self.major.pk]), {"action": "publish"})
        self.major.refresh_from_db()
        self.assertFalse(self.major.is_published)

    def test_swiss_stage_with_teams_and_records(self):
        url = reverse("control_center:stage_create", args=[self.major.pk])
        data = {
            "action": "save", "order": "1", "name": "Стадия 1", "kind": "swiss", "starts_at": _dt(timezone.now() + timedelta(days=2)), "pass_correct": "5",
            **_management("teams", 2, 0),
            "teams-0-seed": "1", "teams-0-team": str(self.teams[0].pk), "teams-0-record": "",
            "teams-1-seed": "2", "teams-1-team": str(self.teams[1].pk), "teams-1-record": "",
        }
        self.client.post(url, data)
        stage = Stage.objects.get(major=self.major, order=1)
        self.assertEqual(stage.entries.count(), 2)

        entries = list(stage.entries.order_by("seed"))
        data = {
            "action": "save", "order": "1", "name": "Стадия 1", "kind": "swiss", "starts_at": _dt(stage.starts_at), "pass_correct": "5", "is_settled": "on",
            **_management("teams", 3, 2),
            "teams-0-id": str(entries[0].pk), "teams-0-stage": str(stage.pk), "teams-0-seed": "1", "teams-0-team": str(self.teams[0].pk), "teams-0-record": "3-0",
            "teams-1-id": str(entries[1].pk), "teams-1-stage": str(stage.pk), "teams-1-seed": "2", "teams-1-team": str(self.teams[1].pk), "teams-1-record": "0-3",
            "teams-2-seed": "0", "teams-2-team": "", "teams-2-record": "",
        }
        response = self.client.post(reverse("control_center:stage_edit", args=[stage.pk]), data)
        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertTrue(stage.is_settled)
        self.assertEqual(dict(stage.entries.values_list("team_id", "record")), {self.teams[0].pk: "3-0", self.teams[1].pk: "0-3"})

    def test_duplicate_team_is_rejected(self):
        data = {
            "action": "save", "order": "1", "name": "Стадия 1", "kind": "swiss", "starts_at": _dt(timezone.now()), "pass_correct": "5",
            **_management("teams", 2, 0),
            "teams-0-seed": "1", "teams-0-team": str(self.teams[0].pk), "teams-0-record": "",
            "teams-1-seed": "2", "teams-1-team": str(self.teams[0].pk), "teams-1-record": "",
        }
        response = self.client.post(reverse("control_center:stage_create", args=[self.major.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "повторяющееся значение")
        self.assertFalse(Stage.objects.filter(major=self.major).exists())

    def test_bracket_and_winners_advance(self):
        stage = Stage.objects.create(major=self.major, order=2, name="Плей-офф", kind=Stage.Kind.PLAYOFFS, starts_at=timezone.now() + timedelta(days=10))
        url = reverse("control_center:stage_edit", args=[stage.pk])
        self.client.post(url, {"action": "bracket", "third_place": "1"})
        self.assertEqual(stage.matches.count(), 8)

        matches = list(stage.matches.order_by("round", "slot"))
        data = {"action": "save", "order": "2", "name": "Плей-офф", "kind": "playoffs", "starts_at": _dt(stage.starts_at), "pass_correct": "5", **_management("teams", 0, 0), **_management("matches", len(matches), len(matches))}
        for index, match in enumerate(matches):
            row = {"id": match.pk, "stage": stage.pk, "round": match.round, "slot": match.slot, "team_a": "", "team_b": "", "winner": "", "score": "", "starts_at": ""}
            if match.round == R.QUARTERFINAL:
                a, b = self.teams[2 * match.slot - 2], self.teams[2 * match.slot - 1]
                row.update(team_a=a.pk, team_b=b.pk, winner=a.pk if match.slot <= 2 else "", score="2:0" if match.slot <= 2 else "")
            data.update({f"matches-{index}-{key}": str(value) for key, value in row.items()})
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        semi = stage.matches.get(round=R.SEMIFINAL, slot=1)
        self.assertEqual((semi.team_a_id, semi.team_b_id), (self.teams[0].pk, self.teams[2].pk))

    def test_winner_must_play_in_match(self):
        stage = Stage.objects.create(major=self.major, order=2, name="Плей-офф", kind=Stage.Kind.PLAYOFFS, starts_at=timezone.now())
        match = Match.objects.create(stage=stage, round=R.QUARTERFINAL, slot=1, team_a=self.teams[0], team_b=self.teams[1])
        data = {
            "action": "save", "order": "2", "name": "Плей-офф", "kind": "playoffs", "starts_at": _dt(stage.starts_at), "pass_correct": "5",
            **_management("teams", 0, 0), **_management("matches", 1, 1),
            "matches-0-id": str(match.pk), "matches-0-stage": str(stage.pk), "matches-0-round": "1", "matches-0-slot": "1",
            "matches-0-team_a": str(self.teams[0].pk), "matches-0-team_b": str(self.teams[1].pk), "matches-0-winner": str(self.teams[5].pk), "matches-0-score": "", "matches-0-starts_at": "",
        }
        response = self.client.post(reverse("control_center:stage_edit", args=[stage.pk]), data)
        self.assertContains(response, "Победитель должен быть одной из команд матча")
        match.refresh_from_db()
        self.assertIsNone(match.winner_id)

    def test_carry_over_advanced_teams(self):
        first = Stage.objects.create(major=self.major, order=1, name="Стадия 1", starts_at=timezone.now())
        second = Stage.objects.create(major=self.major, order=2, name="Стадия 2", starts_at=timezone.now() + timedelta(days=4))
        records = ["3-0", "3-2", "2-3", "0-3"]
        for index, record in enumerate(records):
            StageTeam.objects.create(stage=first, team=self.teams[index], seed=index + 1, record=record)
        self.client.post(reverse("control_center:stage_edit", args=[second.pk]), {"action": "carry_over"})
        self.assertEqual(list(second.entries.order_by("seed").values_list("team_id", flat=True)), [self.teams[0].pk, self.teams[1].pk])

    def test_stage_with_picks_is_not_deleted(self):
        stage = Stage.objects.create(major=self.major, order=1, name="Стадия 1", starts_at=timezone.now())
        SwissPick.objects.create(user=self.admin, stage=stage, team=self.teams[0], pick="3-0")
        self.client.post(reverse("control_center:stage_edit", args=[stage.pk]), {"action": "delete"})
        self.assertTrue(Stage.objects.filter(pk=stage.pk).exists())

    def test_reward_rule_create_and_settle(self):
        stage = Stage.objects.create(major=self.major, order=1, name="Стадия 1", starts_at=timezone.now() - timedelta(days=1), is_settled=True, pass_correct=1)
        StageTeam.objects.create(stage=stage, team=self.teams[0], record="3-0")
        player = make_user("player")
        UserProfile.objects.filter(user=player).update(steam_id="76561190000000001")
        SwissPick.objects.create(user=player, stage=stage, team=self.teams[0], pick="3-0")

        response = self.client.post(reverse("control_center:reward_create", args=[self.major.pk]), {
            "action": "save", "title": "Пройдена стадия", "condition": "stage_passed", "stage": "", "kind": "balance", "amount": "50", "days": "0", "sort_order": "100", "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        rule = RewardRule.objects.get(major=self.major)

        self.client.post(reverse("control_center:major_edit", args=[self.major.pk]), {"action": "settle"})
        grant = RewardGrant.objects.get(rule=rule, user=player)
        self.assertEqual(grant.status, RewardGrant.Status.GRANTED)
        self.assertEqual(UserProfile.objects.get(user=player).balance_coins, Decimal("50.00"))

    def test_reward_needs_known_skin(self):
        response = self.client.post(reverse("control_center:reward_create", args=[self.major.pk]), {
            "action": "save", "title": "Скин", "condition": "champion", "kind": "item", "item_name": "No Such Skin", "amount": "0", "days": "0", "sort_order": "100",
        })
        self.assertContains(response, "В каталоге нет предмета")
        self.assertFalse(RewardRule.objects.filter(major=self.major).exists())

    def test_grant_queue_approve_and_reject(self):
        rule = RewardRule.objects.create(major=self.major, title="Большой приз", condition=RewardRule.Condition.CHAMPION, amount=Decimal("30"), requires_approval=True)
        first, second = make_user("winner1"), make_user("winner2")
        approve = RewardGrant.objects.create(rule=rule, user=first)
        reject = RewardGrant.objects.create(rule=rule, user=second)
        self.assertContains(self.client.get(reverse("control_center:majors")), "Большой приз")

        self.client.post(reverse("control_center:major_grant_action", args=[approve.pk]), {"decision": "approve"})
        self.client.post(reverse("control_center:major_grant_action", args=[reject.pk]), {"decision": "reject"})
        approve.refresh_from_db()
        reject.refresh_from_db()
        self.assertEqual(approve.status, RewardGrant.Status.GRANTED)
        self.assertEqual(reject.status, RewardGrant.Status.REJECTED)
        self.assertEqual(UserProfile.objects.get(user=first).balance_coins, Decimal("30.00"))

    def test_team_create(self):
        self.client.post(reverse("control_center:team_create"), {"name": "Test Squad", "slug": "", "tag": "TSQ", "region": "cis", "is_active": "on"})
        team = Team.objects.get(name="Test Squad")
        self.assertEqual(team.slug, "test-squad")

    def _playoff_post(self, stage, match, **overrides) -> dict:
        row = {"id": match.pk, "stage": stage.pk, "round": match.round, "slot": match.slot, "team_a": match.team_a_id, "team_b": match.team_b_id, "winner": "", "score": "", "starts_at": ""}
        row.update(overrides)
        data = {"action": "save", "order": str(stage.order), "name": stage.name, "kind": stage.kind, "starts_at": _dt(stage.starts_at), "pass_correct": "5", **_management("teams", 0, 0), **_management("matches", 1, 1)}
        data.update({f"matches-0-{key}": "" if value is None else str(value) for key, value in row.items()})
        return data

    def test_picked_bracket_keeps_its_matches_and_format(self):
        stage = Stage.objects.create(major=self.major, order=2, name="Плей-офф", kind=Stage.Kind.PLAYOFFS, starts_at=timezone.now())
        match = Match.objects.create(stage=stage, round=R.QUARTERFINAL, slot=1, team_a=self.teams[0], team_b=self.teams[1])
        BracketPick.objects.create(user=self.admin, stage=stage, round=R.QUARTERFINAL, slot=1, team=self.teams[0])
        url = reverse("control_center:stage_edit", args=[stage.pk])

        self.assertContains(self.client.post(url, self._playoff_post(stage, match, DELETE="on")), "удалять матчи нельзя")
        self.assertContains(self.client.post(url, self._playoff_post(stage, match, slot=2)), "менять нельзя")
        self.assertContains(self.client.post(url, {**self._playoff_post(stage, match), "kind": "swiss"}), "формат менять нельзя")
        match.refresh_from_db()
        stage.refresh_from_db()
        self.assertEqual((match.slot, stage.kind), (1, Stage.Kind.PLAYOFFS))

        # Entering the result is still fine.
        self.assertEqual(self.client.post(url, self._playoff_post(stage, match, winner=self.teams[0].pk)).status_code, 302)
        match.refresh_from_db()
        self.assertEqual(match.winner_id, self.teams[0].pk)

    def test_picked_swiss_stage_keeps_its_teams(self):
        stage = Stage.objects.create(major=self.major, order=1, name="Стадия 1", starts_at=timezone.now())
        entry = StageTeam.objects.create(stage=stage, team=self.teams[0], seed=1)
        SwissPick.objects.create(user=self.admin, stage=stage, team=self.teams[0], pick="3-0")
        url = reverse("control_center:stage_edit", args=[stage.pk])

        def post(**row):
            fields = {"id": entry.pk, "stage": stage.pk, "seed": 1, "team": self.teams[0].pk, "record": "", **row}
            data = {"action": "save", "order": "1", "name": stage.name, "kind": "swiss", "starts_at": _dt(stage.starts_at), "pass_correct": "5", **_management("teams", 1, 1)}
            data.update({f"teams-0-{key}": str(value) for key, value in fields.items()})
            return self.client.post(url, data)

        self.assertContains(post(DELETE="on"), "убирать команды нельзя")
        self.assertContains(post(team=self.teams[1].pk), "заменять команды нельзя")
        self.assertEqual(post(record="3-0").status_code, 302)
        entry.refresh_from_db()
        self.assertEqual((entry.team_id, entry.record), (self.teams[0].pk, "3-0"))

    def test_stage_with_granted_rewards_is_not_deleted(self):
        stage = Stage.objects.create(major=self.major, order=1, name="Стадия 1", starts_at=timezone.now())
        rule = RewardRule.objects.create(major=self.major, stage=stage, title="Стадия", condition=RewardRule.Condition.STAGE_PASSED, amount=Decimal("10"))
        RewardGrant.objects.create(rule=rule, user=self.admin, scope=f"stage:{stage.pk}", stage=stage)
        response = self.client.post(reverse("control_center:stage_edit", args=[stage.pk]), {"action": "delete"})
        self.assertRedirects(response, reverse("control_center:stage_edit", args=[stage.pk]), fetch_redirect_response=False)
        self.assertTrue(Stage.objects.filter(pk=stage.pk).exists())
