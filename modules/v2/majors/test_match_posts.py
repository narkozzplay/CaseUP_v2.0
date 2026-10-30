from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from modules.v2.control_center.tests import make_user
from modules.v2.profiles.models import UserRole
from modules.v2.tg_notifier.models import TelegramNotifierConfig
from modules.v2.tg_notifier.services import TelegramNotifierError, TelegramSendResult

from . import match_posts, pandascore
from .models import Major, MatchPost, Team

NAVI, FAZE, G2, VIT = (1, "Natus Vincere", "NAVI"), (2, "FaZe Clan", "FaZe"), (3, "G2 Esports", "G2"), (4, "Team Vitality", "VIT")


def raw_match(match_id, a, b, winner, *, score=(2, 1), name="", tournament=(10, "Stage 1"), ended=None, games=None):
    ended = ended or timezone.now() - timedelta(minutes=5)
    return {
        "id": match_id,
        "name": name or f"{a[1]} vs {b[1]}",
        "status": "finished",
        "end_at": ended.isoformat(),
        "winner_id": winner[0],
        "forfeit": False,
        "tournament_id": tournament[0],
        "tournament": {"id": tournament[0], "name": tournament[1]},
        "opponents": [{"opponent": {"id": a[0], "name": a[1], "acronym": a[2]}}, {"opponent": {"id": b[0], "name": b[1], "acronym": b[2]}}],
        "results": [{"team_id": a[0], "score": score[0]}, {"team_id": b[0], "score": score[1]}],
        "games": games if games is not None else [],
    }


def sent(message_id=77):
    return TelegramSendResult(chat_id="@caseup_pro_anons", payload={"ok": True, "result": {"message_id": message_id}})


class MatchPostFixtures(TestCase):
    def setUp(self):
        TelegramNotifierConfig.objects.create(singleton_key=1, is_enabled=True, bot_token="123456:ABCDEF", public_base_url="https://case-up.ru", channel_chat_id="@caseup_pro_anons")
        now = timezone.now()
        self.major = Major.objects.create(
            name="Test Major", slug="test-major", starts_at=now - timedelta(days=1), ends_on=(now + timedelta(days=10)).date(),
            is_published=True, pandascore_serie_id=555, match_posts_enabled=True,
        )
        Team.objects.update_or_create(slug="navi", defaults={"name": "Natus Vincere", "tag": "NAVI"})


@override_settings(PANDASCORE_API_KEY="test-key")
class SyncTests(MatchPostFixtures):
    @patch("modules.v2.majors.match_posts.send_message", return_value=sent())
    @patch("modules.v2.majors.pandascore._get")
    def test_new_match_is_posted_once(self, api, send):
        games = [
            {"id": 1, "position": 1, "winner": {"id": 1}, "map": {"name": "de_mirage"}, "teams": [{"team": {"id": 1}, "score": 13}, {"team": {"id": 2}, "score": 9}]},
            {"id": 2, "position": 2, "winner": {"id": 2}, "map": {"name": "Inferno"}},
            {"id": 3, "position": 3, "winner": {"id": 1}, "map": {"name": "Nuke"}},
        ]
        api.return_value = [raw_match(100, NAVI, FAZE, NAVI, games=games)]
        self.assertIn("posted=1", match_posts.sync_all())
        post = MatchPost.objects.get(external_id=100)
        self.assertEqual(post.status, MatchPost.Status.SENT)
        self.assertEqual(post.message_id, 77)
        text = send.call_args.kwargs["text"]
        self.assertIn("<b>NAVI 2:1 FaZe</b>", text)
        self.assertIn("🗺 Mirage — 13:9 (NAVI)", text)
        self.assertIn("🗺 Inferno — FaZe", text)
        self.assertIn("utm_campaign=major", text)
        self.assertEqual(send.call_args.kwargs["chat_id"], "@caseup_pro_anons")

        match_posts.sync_all()
        self.assertEqual(send.call_count, 1)

    @patch("modules.v2.majors.match_posts.send_message", return_value=sent())
    @patch("modules.v2.majors.pandascore._get")
    def test_old_match_is_recorded_without_post(self, api, send):
        api.return_value = [raw_match(101, NAVI, FAZE, NAVI, ended=timezone.now() - timedelta(hours=5))]
        match_posts.sync_all()
        self.assertEqual(MatchPost.objects.get(external_id=101).status, MatchPost.Status.SKIPPED)
        send.assert_not_called()

    @patch("modules.v2.majors.match_posts.send_message", side_effect=TelegramNotifierError("Bad Request: chat not found"))
    @patch("modules.v2.majors.pandascore._get")
    def test_telegram_error_is_written(self, api, send):
        api.return_value = [raw_match(102, NAVI, FAZE, NAVI)]
        self.assertIn("errors=1", match_posts.sync_all())
        post = MatchPost.objects.get(external_id=102)
        self.assertEqual(post.status, MatchPost.Status.ERROR)
        self.assertIn("chat not found", post.error)

    @patch("modules.v2.majors.pandascore._get")
    def test_disabled_or_finished_major_is_not_polled(self, api):
        Major.objects.filter(pk=self.major.pk).update(ends_on=(timezone.now() - timedelta(days=5)).date())
        self.assertEqual(match_posts.sync_all(), "idle")
        api.assert_not_called()

    @override_settings(PANDASCORE_API_KEY="")
    def test_no_key(self):
        self.assertEqual(match_posts.sync_all(), "no_key")

    @patch("modules.v2.majors.match_posts.send_message", return_value=sent())
    @patch("modules.v2.majors.pandascore._get")
    def test_map_details_are_fetched_when_missing(self, api, send):
        match = raw_match(103, NAVI, FAZE, NAVI, score=(1, 0), games=[{"id": 9, "position": 1, "winner": {"id": 1}}])
        api.side_effect = lambda path, **kwargs: {"id": 9, "map": {"name": "Ancient"}, "rounds_score": [{"team_id": 1, "score": 13}, {"team_id": 2, "score": 4}]} if "/games/" in path else [match]
        match_posts.sync_all()
        self.assertIn("🗺 Ancient — 13:4 (NAVI)", send.call_args.kwargs["text"])

    @patch("modules.v2.majors.match_posts.send_message", return_value=sent())
    @patch("modules.v2.majors.pandascore._get")
    def test_paid_map_details_are_skipped(self, api, send):
        match = raw_match(104, NAVI, FAZE, NAVI, score=(1, 0), games=[{"id": 9, "position": 1, "winner": {"id": 1}}])

        def fake(path, **kwargs):
            if "/games/" in path:
                raise pandascore.PandaScoreError("forbidden", status_code=403)
            return [match]

        api.side_effect = fake
        match_posts.sync_all()
        self.assertEqual(MatchPost.objects.get(external_id=104).status, MatchPost.Status.SENT)


class ProgressTests(MatchPostFixtures):
    def _parse(self, *raws):
        return [pandascore.parse_match(raw) for raw in raws]

    def test_swiss_qualification_and_elimination(self):
        start = timezone.now() - timedelta(days=2)
        history = self._parse(
            raw_match(1, NAVI, G2, NAVI, ended=start), raw_match(2, NAVI, VIT, NAVI, ended=start + timedelta(hours=1)),
            raw_match(3, FAZE, G2, G2, ended=start), raw_match(4, FAZE, VIT, VIT, ended=start + timedelta(hours=1)),
            raw_match(5, NAVI, FAZE, NAVI, ended=start + timedelta(hours=2)),
        )
        _round, line = match_posts.describe_progress(history[-1], history, major=self.major, names={"natus vincere": "NAVI"})
        self.assertEqual(line, "✅ NAVI выходит дальше (3-0) · ❌ FaZe Clan вылетает (0-3)")

    def test_swiss_records_mid_stage(self):
        history = self._parse(raw_match(1, NAVI, FAZE, FAZE))
        _round, line = match_posts.describe_progress(history[0], history, major=self.major, names={})
        self.assertEqual(line, "FaZe Clan 1-0 · Natus Vincere 0-1")

    def test_playoffs(self):
        semi = self._parse(raw_match(7, NAVI, FAZE, NAVI, name="Semifinal 1: NAVI vs FaZe", tournament=(11, "Playoffs")))[0]
        self.assertEqual(match_posts.describe_progress(semi, [semi], major=self.major, names={}), ("Полуфинал", "Natus Vincere проходит в финал, FaZe Clan выбывает"))
        final = self._parse(raw_match(8, NAVI, G2, G2, name="Grand final: NAVI vs G2", tournament=(11, "Playoffs")))[0]
        self.assertEqual(match_posts.describe_progress(final, [final], major=self.major, names={}), ("Финал", "🏆 G2 Esports — чемпион Test Major!"))


class ControlCenterTests(MatchPostFixtures):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_user("boss", UserRole.ADMIN))
        self.post = MatchPost.objects.create(
            major=self.major, external_id=200, stage_name="Stage 1", team_a="NAVI", team_b="FaZe", score_a=2, score_b=1, winner="NAVI",
            maps=[{"name": "", "winner": "NAVI", "score_a": None, "score_b": None}], status=MatchPost.Status.SENT,
            chat_id="@caseup_pro_anons", message_id=77, finished_at=timezone.now(),
        )

    def test_pages_render(self):
        self.assertEqual(self.client.get(reverse("control_center:major_edit", args=[self.major.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("control_center:match_post_edit", args=[self.post.pk])).status_code, 200)

    def test_viewer_is_forbidden(self):
        self.client.force_login(make_user("viewer"))
        self.assertEqual(self.client.get(reverse("control_center:match_post_edit", args=[self.post.pk])).status_code, 403)

    @patch("modules.v2.majors.match_posts.call_bot_api", return_value={"ok": True})
    def test_mvp_edits_the_channel_message(self, edit):
        data = {"team_a": "NAVI", "team_b": "FaZe", "score_a": 2, "score_b": 1, "mvp": "iM", "progress": "", "maps_text": "Mirage 13:9\nInferno 9:13\nNuke", "action": "send"}
        response = self.client.post(reverse("control_center:match_post_edit", args=[self.post.pk]), data)
        self.assertEqual(response.status_code, 302)
        method, payload = edit.call_args.args
        self.assertEqual(method, "editMessageText")
        self.assertEqual(payload["message_id"], 77)
        self.assertIn("⭐ MVP: <b>iM</b>", payload["text"])
        self.post.refresh_from_db()
        self.assertEqual([item["winner"] for item in self.post.maps], ["NAVI", "FaZe", ""])

    @override_settings(PANDASCORE_API_KEY="test-key")
    @patch("modules.v2.majors.pandascore._get", return_value=[{"id": 9001, "full_name": "Major 2026", "league": {"name": "BLAST"}, "begin_at": "2026-11-25T08:00:00Z"}])
    def test_find_series(self, api):
        response = self.client.get(reverse("control_center:major_edit", args=[self.major.pk]), {"find_series": "major"})
        self.assertContains(response, "9001")
        self.assertContains(response, "BLAST Major 2026")
